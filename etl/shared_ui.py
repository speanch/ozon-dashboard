import os
import tempfile
from bisect import bisect_right
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from .config import PRESETS as _PRESETS, SHOP_LABELS

try:
    import fcntl
    _HAS_FCNTL = True
except ImportError:  # Windows
    _HAS_FCNTL = False
    import threading
    _THREAD_LOCK = threading.Lock()

EXPERIMENT_PALETTE = [
    "#E53935", "#1E88E5", "#43A047", "#FB8C00", "#8E24AA",
    "#00ACC1", "#6D4C41", "#3949AB", "#F4511E", "#7CB342",
    "#C0CA33", "#D81B60",
]


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)},{int(h[2:4], 16)},{int(h[4:6], 16)},{alpha})"


GRANULARITIES = ["день", "неделя", "месяц", "квартал", "год"]

_GRAN_FREQ = {
    "неделя": "W-MON",
    "месяц": "MS",
    "квартал": "QS",
    "год": "YS",
}

GRAN_TICKFMT = {
    "день": "%d.%m",
    "неделя": "%d.%m",
    "месяц": "%m.%Y",
    "квартал": "%m.%Y",
    "год": "%Y",
}


def _gran_label(ts, gran: str) -> str:
    if gran == "неделя":
        return f"нед. {ts.strftime('%d.%m.%Y')}"
    if gran == "месяц":
        return ts.strftime("%m.%Y")
    if gran == "квартал":
        q = (ts.month - 1) // 3 + 1
        return f"{q}-й кв. {ts.year}"
    if gran == "год":
        return ts.strftime("%Y")
    return ts.strftime("%d.%m.%Y")


def build_net_price_asof(prices_raw):
    """Строит функцию себестоимости «на дату заказа».

    Возвращает asof(shop, offer_id, order_date) -> net_price на ближайшую дату
    снимка, которая <= order_date. Снимки с нулевой себестоимостью пропускаются
    (поле net_price появилось в выгрузке позже — нули означают «ещё не было
    данных»). Если заказ раньше всех снимков — берётся самый ранний; если
    снимков нет — 0.
    """
    snapshots: dict = {}
    for p in prices_raw:
        if p.snapshot_date is None:
            continue
        np = p.net_price or 0.0
        if np <= 0:
            continue
        rec = snapshots.setdefault((p.shop, p.offer_id), {"dates": [], "values": []})
        rec["dates"].append(p.snapshot_date)
        rec["values"].append(np)
    for rec in snapshots.values():
        order = sorted(range(len(rec["dates"])), key=lambda i: rec["dates"][i])
        rec["dates"] = [rec["dates"][i] for i in order]
        rec["values"] = [rec["values"][i] for i in order]

    def asof(shop, offer_id, d):
        rec = snapshots.get((shop, offer_id))
        if not rec or d is None:
            return 0.0
        i = bisect_right(rec["dates"], d) - 1
        if i < 0:
            return rec["values"][0]
        return rec["values"][i]

    return asof


def resample_daily(df, gran: str, group_col: str, sum_cols, date_col: str = "date"):
    """Агрегирует дневные строки по неделям/месяцам/кварталам/годам.

    Группирует по group_col, суммирует sum_cols, добавляет колонки:
    - label — подпись периода для подсказок;
    - pos — позиция периода внутри группы (для выравнивания прошлого периода).

    Для gran == "день" возвращает те же строки, но с добавленными label/pos.
    """
    freq = _GRAN_FREQ.get(gran)
    out = []
    for key, g in df.groupby(group_col, dropna=False):
        g = g.copy()
        if freq:
            g[date_col] = pd.to_datetime(g[date_col])
            g = g.set_index(date_col).sort_index()
            agg = g.resample(freq).agg({c: "sum" for c in sum_cols}).reset_index()
        else:
            agg = g[[date_col] + list(sum_cols)].sort_values(date_col).reset_index(drop=True)
        agg[group_col] = key
        agg["label"] = agg[date_col].map(lambda ts: _gran_label(ts, gran))
        agg["pos"] = range(len(agg))
        out.append(agg)
    return pd.concat(out, ignore_index=True)


def _experiment_shops(e) -> set:
    """Кабинеты эксперимента как множество ключей (поле shop хранит их через запятую)."""
    if not e.shop:
        return set()
    return {s.strip() for s in str(e.shop).split(",") if s.strip()}


def _active_experiments(experiments, date_from, date_to, selected_shops=None):
    """Эксперименты, попадающие в период (с учётом кабинета), отсортированные по дате начала."""
    active = []
    for e in experiments:
        effective_end = e.end_date or e.start_date
        if selected_shops is not None:
            shops = _experiment_shops(e)
            if shops and not (shops & set(selected_shops)):
                continue
        if not e.start_date:
            continue
        if effective_end < date_from or e.start_date > date_to:
            continue
        active.append(e)
    active.sort(key=lambda e: (e.start_date, e.end_date or e.start_date))
    return active


def _assign_lanes(active):
    """Разносит пересекающиеся по датам эксперименты по разным «полосам» (жадная раскраска интервалов)."""
    lanes = {}
    lane_ends = []
    for e in active:
        lane = None
        effective_end = e.end_date or e.start_date
        for li, end in enumerate(lane_ends):
            if e.start_date >= end:
                lane = li
                break
        if lane is None:
            lane = len(lane_ends)
            lane_ends.append(effective_end)
        else:
            lane_ends[lane] = effective_end
        lanes[e.id] = lane
    return lanes


def _experiment_colors(active):
    """Стабильные цвета экспериментов — по позиции в полном списке (не меняются при скрытии чекбоксами)."""
    return {e.id: EXPERIMENT_PALETTE[i % len(EXPERIMENT_PALETTE)] for i, e in enumerate(active)}


def get_hidden_experiment_ids(experiments, date_from, date_to, selected_shops=None):
    """Возвращает id экспериментов, отключённых чекбоксами (по умолчанию все выключены)."""
    active = _active_experiments(experiments, date_from, date_to, selected_shops)
    hidden = set()
    for e in active:
        key = f"exp_show_{e.id}"
        if not st.session_state.get(key, False):
            hidden.add(e.id)
    return hidden


def add_experiment_markers(fig, experiments, date_from, date_to, selected_shops=None, exclude_ids=None):
    """Закрашенные области экспериментов на графике: разные цвета, подписи без наложений."""
    active = _active_experiments(experiments, date_from, date_to, selected_shops)
    colors = _experiment_colors(active)
    if exclude_ids:
        active = [e for e in active if e.id not in exclude_ids]
    if not active:
        return

    lanes = _assign_lanes(active)

    ymax = None
    for tr in fig.data:
        y = getattr(tr, "y", None)
        if y is not None and len(y) > 0:
            try:
                m = max(y)
            except (TypeError, ValueError):
                continue
            ymax = m if ymax is None else max(ymax, m)
    if ymax is None or ymax <= 0:
        ymax = 1.0

    n_lanes = max(lanes.values()) + 1
    step = ymax * 0.07
    fig.layout.yaxis.range = [None, ymax + step * (n_lanes + 1)]

    for e in active:
        color = colors[e.id]
        lane = lanes[e.id]
        x0 = max(e.start_date, date_from)
        if e.end_date is None:
            fig.add_vline(
                x=e.start_date,
                line_width=1,
                line_color=color,
                line_dash="dash",
            )
        else:
            x1 = min(e.end_date, date_to)
            fig.add_vrect(
                x0=x0, x1=x1,
                fillcolor=_hex_to_rgba(color, 0.15),
                opacity=0.5,
                line_width=1,
                line_color=color,
                line_dash="dash",
            )
        fig.add_annotation(
            x=x0,
            y=ymax + step * (lane + 1),
            text=e.name,
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            font=dict(size=11, color=color),
        )


def render_experiment_list(experiments, date_from, date_to, selected_shops=None):
    """Чекбоксы для включения/отключения отображения экспериментов на графике."""
    active = _active_experiments(experiments, date_from, date_to, selected_shops)
    colors = _experiment_colors(active)
    st.subheader("Эксперименты за период")
    if not active:
        st.caption("Нет экспериментов за выбранный период.")
        return
    for e in active:
        color = colors[e.id]
        shop_labels = [SHOP_LABELS.get(s, s) for s in _experiment_shops(e)]
        shop = ", ".join(shop_labels) if shop_labels else "Все кабинеты"
        if e.end_date is None:
            label = f"{e.name} · {e.start_date:%d.%m.%Y} · {shop}"
        else:
            label = f"{e.name} · {e.start_date:%d.%m.%Y} – {e.end_date:%d.%m.%Y} · {shop}"
        col_dot, col_box = st.columns([0.06, 0.94], gap="small")
        with col_dot:
            st.markdown(
                f"<span style='color:{color};font-size:16px'>&#9632;</span>",
                unsafe_allow_html=True,
            )
        with col_box:
            st.checkbox(label, value=False, key=f"exp_show_{e.id}")


def _acquire_etl_lock():
    """Неблокирующая попытка захватить глобальный лок ETL (общий для всех сессий)."""
    if _HAS_FCNTL:
        path = os.path.join(tempfile.gettempdir(), "ozon_dashboard_etl.lock")
        f = open(path, "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            f.close()
            return None
    if _THREAD_LOCK.acquire(blocking=False):
        return _THREAD_LOCK
    return None


def _release_etl_lock(lock):
    if lock is None:
        return
    if _HAS_FCNTL:
        try:
            fcntl.flock(lock, fcntl.LOCK_UN)
        finally:
            lock.close()
    else:
        lock.release()


def _run_etl_update(fast: bool):
    """Запускает ETL, если он ещё не выполняется (лок от одновременных запусков)."""
    lock = _acquire_etl_lock()
    if lock is None:
        st.warning("Обновление уже выполняется другим пользователем. Подождите и повторите.")
        return
    try:
        label = "Быстрое обновление…" if fast else "Полное обновление (реклама и аналитика)…"
        with st.spinner(label):
            from .pipeline import run_pipeline
            run_pipeline(days_back=60, fast=fast)
    finally:
        _release_etl_lock(lock)


def render_refresh_button():
    """Две кнопки обновления в сайдбаре."""
    with st.sidebar:
        st.markdown("### Данные")

        if st.button("🔄 Обновить данные", use_container_width=True):
            _run_etl_update(fast=True)
            st.rerun()
        st.caption("Заказы, остатки, финансы, цены, себестоимость (~1–2 мин). Без рекламы и аналитики.")

        if st.button("📊 Реклама и аналитика", use_container_width=True):
            _run_etl_update(fast=False)
            st.rerun()
        st.caption("Реклама и аналитика продаж (~5–10 мин). То же, что выше, плюс реклама (Performance API) и ежедневная аналитика.")


def build_presets(min_date: date, max_date: date) -> dict[str, tuple[str, callable]]:
    """Возвращает пресеты периодов, привязанные к границам данных."""
    today = date.today()
    return {
        "today": ("Сегодня", lambda: (today, today)),
        "yesterday": ("Вчера", lambda: (today - timedelta(days=1), today - timedelta(days=1))),
        "this_week": ("Эта неделя", lambda: (today - timedelta(days=today.weekday()), today)),
        "last_week": ("Прошлая неделя", lambda: (
            today - timedelta(days=today.weekday() + 7),
            today - timedelta(days=today.weekday() + 1),
        )),
        "this_month": ("Этот месяц", lambda: (today.replace(day=1), today)),
        "last_month": ("Прошлый месяц", lambda: (
            (today.replace(day=1) - timedelta(days=1)).replace(day=1),
            today.replace(day=1) - timedelta(days=1),
        )),
        "last_7_days": ("Последние 7 дней", lambda: (today - timedelta(days=6), today)),
        "last_14_days": ("Последние 2 недели", lambda: (today - timedelta(weeks=2), today)),
        "last_30_days": ("Последние 30 дней", lambda: (today - timedelta(days=29), today)),
        "last_4_weeks": ("Последние 4 недели", lambda: (today - timedelta(weeks=4), today)),
        "last_8_weeks": ("Последние 8 недель", lambda: (today - timedelta(weeks=8), today)),
        "last_90_days": ("Последние 90 дней", lambda: (today - timedelta(days=89), today)),
        "all_time": ("Всё время", lambda: (min_date, max_date)),
    }


def date_filter_section(
    presets: dict[str, tuple[str, callable]],
    min_date: date,
    max_date: date,
    prefix: str = "",
    default_preset: str = "last_4_weeks",
) -> tuple[date, date]:
    """
    Отрисовывает UI фильтра: пресет + чекбокс «Свои даты».
    Возвращает (date_from, date_to).
    Префикс позволяет независимые экземпляры на странице.
    """
    pk = f"{prefix}preset" if prefix else "preset"
    dfk = f"{prefix}date_from" if prefix else "date_from"
    dtk = f"{prefix}date_to" if prefix else "date_to"
    cck = f"{prefix}custom_cb" if prefix else "custom_cb"
    dwk = f"{prefix}date_widget" if prefix else "date_widget"
    ick = f"{prefix}_custom" if prefix else "_custom"

    preset_keys = list(presets.keys())

    if pk not in st.session_state:
        st.session_state[pk] = default_preset
        df_, dt_ = presets[default_preset][1]()
        st.session_state[dfk] = max(df_, min_date)
        st.session_state[dtk] = min(dt_, max_date)

    col3, col4 = st.columns(2)

    with col3:
        current_preset = st.selectbox(
            "Период",
            preset_keys,
            format_func=lambda x: presets[x][0],
            index=preset_keys.index(st.session_state[pk]),
            key=f"{prefix}preset_widget",
        )

    with col4:
        custom = st.checkbox("Свои даты", value=st.session_state.get(ick, False), key=cck)
        st.session_state[ick] = custom

        if not custom:
            if current_preset != st.session_state[pk]:
                st.session_state[pk] = current_preset
                df_, dt_ = presets[current_preset][1]()
                st.session_state[dfk] = max(min(df_, max_date), min_date)
                st.session_state[dtk] = min(max(dt_, min_date), max_date)
            if st.session_state[dfk] > st.session_state[dtk]:
                st.session_state[dfk] = st.session_state[dtk]
            st.caption(
                f"{st.session_state[dfk].strftime('%d.%m.%Y')} – "
                f"{st.session_state[dtk].strftime('%d.%m.%Y')}"
            )
        else:
            dr = st.date_input(
                "Выбрать даты",
                value=(st.session_state[dfk], st.session_state[dtk]),
                min_value=min_date,
                max_value=max_date,
                key=dwk,
            )
            if isinstance(dr, tuple) and len(dr) == 2:
                st.session_state[dfk], st.session_state[dtk] = dr[0], dr[1]
            elif hasattr(dr, "day"):
                st.session_state[dfk], st.session_state[dtk] = dr, dr

    return st.session_state[dfk], st.session_state[dtk]


def date_filter_section_with_shops(
    presets: dict[str, tuple[str, callable]],
    min_date: date,
    max_date: date,
    shops: list[str],
    shop_labels: dict[str, str],
    prefix: str = "",
    default_preset: str = "last_4_weeks",
    status_options: list[str] = None,
    default_statuses: list[str] = None,
    status_labels: dict[str, str] = None,
) -> tuple[list[str], list[str], date, date]:
    """Отрисовывает полный фильтр: кабинеты, статусы, период. Возвращает (shops, statuses, from, to)."""
    pk = f"{prefix}preset" if prefix else "preset"
    dfk = f"{prefix}date_from" if prefix else "date_from"
    dtk = f"{prefix}date_to" if prefix else "date_to"
    cck = f"{prefix}custom_cb" if prefix else "custom_cb"
    dwk = f"{prefix}date_widget" if prefix else "date_widget"
    ick = f"{prefix}_custom" if prefix else "_custom"

    preset_keys = list(presets.keys())
    if status_options is None:
        status_options = []
    if default_statuses is None:
        default_statuses = []

    if pk not in st.session_state:
        st.session_state[pk] = default_preset
        df_, dt_ = presets[default_preset][1]()
        st.session_state[dfk] = max(df_, min_date)
        st.session_state[dtk] = min(dt_, max_date)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        selected_shops = st.multiselect(
            "Кабинет", shops, default=shops,
            format_func=lambda x: shop_labels.get(x, x),
        )

    with col2:
        sf = (lambda x: status_labels.get(x, x)) if status_labels else None
        selected_statuses = st.multiselect(
            "Статус", status_options, default=default_statuses, format_func=sf,
        )

    with col3:
        current_preset = st.selectbox(
            "Период",
            preset_keys,
            format_func=lambda x: presets[x][0],
            index=preset_keys.index(st.session_state[pk]),
            key=f"{prefix}preset_widget",
        )

    with col4:
        custom = st.checkbox("Свои даты", value=st.session_state.get(ick, False), key=cck)
        st.session_state[ick] = custom

        if not custom:
            if current_preset != st.session_state[pk]:
                st.session_state[pk] = current_preset
                df_, dt_ = presets[current_preset][1]()
                st.session_state[dfk] = max(min(df_, max_date), min_date)
                st.session_state[dtk] = min(max(dt_, min_date), max_date)
            if st.session_state[dfk] > st.session_state[dtk]:
                st.session_state[dfk] = st.session_state[dtk]
            st.caption(
                f"{st.session_state[dfk].strftime('%d.%m.%Y')} – "
                f"{st.session_state[dtk].strftime('%d.%m.%Y')}"
            )
        else:
            dr = st.date_input(
                "Выбрать даты",
                value=(st.session_state[dfk], st.session_state[dtk]),
                min_value=min_date,
                max_value=max_date,
                key=dwk,
            )
            if isinstance(dr, tuple) and len(dr) == 2:
                st.session_state[dfk], st.session_state[dtk] = dr[0], dr[1]
            elif hasattr(dr, "day"):
                st.session_state[dfk], st.session_state[dtk] = dr, dr

    return selected_shops, selected_statuses, st.session_state[dfk], st.session_state[dtk]
