from datetime import date, timedelta

import streamlit as st

from .config import PRESETS as _PRESETS


def render_refresh_button():
    """Кнопка «Обновить из Ozon» в сайдбаре (быстрый ETL без рекламы/аналитики)."""
    with st.sidebar:
        st.markdown("### Данные")
        if st.button("🔄 Обновить из Ozon", use_container_width=True):
            with st.spinner("Загружаю данные из Ozon…"):
                from .pipeline import run_pipeline
                run_pipeline(days_back=60, fast=True)
            st.rerun()
        st.caption("Быстрое обновление (без рекламы/аналитики).\nПолное — в терминале: `python run_etl.py`.")


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
    default_preset: str = "this_month",
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
    default_preset: str = "this_month",
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
