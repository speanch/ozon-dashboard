"""Бустинг 75% — эластичный бустинг Ozon и цены для участия в акции."""
import os
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.models import init_db, get_session, BoostSnapshot, ProductPrice
from etl.config import SHOP_LABELS
from etl.shared_ui import render_refresh_button

st.title("Бустинг 75%")
render_refresh_button()

# ── загрузка снимков ─────────────────────────────────────────────────────

engine = init_db()
session = get_session(engine)
raw = session.query(BoostSnapshot).all()
prices_raw = session.query(ProductPrice).all()
session.close()
engine.dispose()

if not raw:
    st.warning("Нет снимков бустинга. Запустите `python run_etl.py` для загрузки.")
    st.stop()

df = pd.DataFrame([{
    "shop": b.shop,
    "snapshot_date": b.snapshot_date,
    "product_id": b.product_id or "",
    "offer_id": b.offer_id or "",
    "name": b.name or "",
    "current_boost": b.current_boost or 0,
    "min_boost": b.min_boost or 0,
    "max_boost": b.max_boost or 0,
    "price_min_elastic": b.price_min_elastic or 0,
    "price_max_elastic": b.price_max_elastic or 0,
    "action_price": b.action_price or 0,
} for b in raw])


def _add_boost_status(d: pd.DataFrame) -> pd.DataFrame:
    """Группа бустинга (по current_boost) и флаг «можно поднять мин. цену, сохранив 75%»."""
    d = d.copy()
    cb = d["current_boost"].fillna(0).astype(float)

    def group(v: float) -> str:
        if v >= 75:
            return "75%+"
        if v >= 50:
            return "50-75%"
        return "ниже 50%"

    d["boost_group"] = cb.map(group)

    pme = d["price_max_elastic"].fillna(0).astype(float)
    mp = d["min_price"].fillna(0).astype(float)
    d["can_raise_min"] = (cb >= 75) & (mp > 0) & (mp < pme)
    return d


def _fmt_chg(old: float, new: float) -> str:
    """Формат «старое → новое» для изменения цены."""
    if (old or 0) != (new or 0):
        return f"{old:,.0f} → {new:,.0f}"
    return f"{new:,.0f}"


def _categorize(name: str) -> str:
    """Категория товара по названию."""
    n = (name or "").strip().lower()
    if n.startswith("антресоль"):
        return "Антресоль"
    if n.startswith("матрас"):
        return "Матрас"
    if n.startswith("напольная рама") or n.startswith("подлокотник"):
        return "Детали кровати"
    if n.startswith("шкаф кровать"):
        return "Кровать"
    if n.startswith("шкаф"):
        return "Шкаф"
    return "Прочее"


df["category"] = df["name"].map(_categorize)


# ── фильтр по кабинету ───────────────────────────────────────────────────

shops_avail = sorted(df["shop"].unique().tolist())
selected_shop = st.selectbox(
    "Кабинет", shops_avail, format_func=lambda x: SHOP_LABELS.get(x, x),
)

shop_df = df[df["shop"] == selected_shop]
dates = sorted(shop_df["snapshot_date"].unique())
if not dates:
    st.info(f"Нет снимков для кабинета {SHOP_LABELS.get(selected_shop, selected_shop)}.")
    st.stop()

latest_date = dates[-1]
previous_date = dates[-2] if len(dates) >= 2 else None

latest = shop_df[shop_df["snapshot_date"] == latest_date].copy()

# ── минимальная цена (ниже неё опускать цену нельзя) ─────────────────────

min_by_product: dict = {}
min_by_offer: dict = {}
if prices_raw:
    prices_df = pd.DataFrame([{
        "shop": p.shop, "offer_id": p.offer_id or "",
        "product_id": p.product_id or "", "snapshot_date": p.snapshot_date,
        "min_price": p.min_price or 0,
    } for p in prices_raw])
    ps = prices_df[prices_df["shop"] == selected_shop]
    if not ps.empty:
        ps = ps.sort_values("snapshot_date")
        for _, r in ps.groupby("product_id", as_index=False).tail(1).iterrows():
            if r["product_id"]:
                min_by_product[r["product_id"]] = r["min_price"]
        for _, r in ps.groupby("offer_id", as_index=False).tail(1).iterrows():
            if r["offer_id"]:
                min_by_offer[r["offer_id"]] = r["min_price"]

latest = latest.copy()
latest["min_price"] = latest["product_id"].map(min_by_product)
latest["min_price"] = latest["min_price"].fillna(
    latest["offer_id"].map(min_by_offer)
)

latest = _add_boost_status(latest)

# ── сводка ───────────────────────────────────────────────────────────────

st.caption(f"Снимок от {latest_date.strftime('%d.%m.%Y')}")

total = len(latest)
boost_75 = int((latest["current_boost"] >= 75).sum())
boost_50_75 = int(((latest["current_boost"] >= 50) & (latest["current_boost"] < 75)).sum())
boost_below_50 = int((latest["current_boost"] < 50).sum())

m1, m2, m3, m4 = st.columns(4)
m1.metric("Товаров в акции", total)
m2.metric(
    "Бустинг 75%+", boost_75,
    help="Товары с максимальным бустингом (75% и выше).",
)
m3.metric(
    "Бустинг 50-75%", boost_50_75,
    help="Товары с бустингом от 50% до 75%.",
)
m4.metric(
    "Бустинг ниже 50%", boost_below_50,
    help="Товары с бустингом ниже 50%.",
)

# ── изменения ────────────────────────────────────────────────────────────

st.divider()
st.subheader("Изменения")

if previous_date is None:
    st.info("Нужно минимум два снимка, чтобы показать изменения. Обновите данные повторно.")
else:
    prev = shop_df[shop_df["snapshot_date"] == previous_date].copy()
    merged = latest.merge(prev, on="product_id", suffixes=("_cur", "_prev"), how="inner")

    changes = []
    for _, r in merged.iterrows():
        pme_c = r["price_max_elastic_cur"] or 0
        pme_p = r["price_max_elastic_prev"] or 0
        pmi_c = r["price_min_elastic_cur"] or 0
        pmi_p = r["price_min_elastic_prev"] or 0
        ap_c = r["action_price_cur"] or 0
        ap_p = r["action_price_prev"] or 0

        if pme_c == pme_p and pmi_c == pmi_p:
            continue

        lost_now = pme_c > 0 and ap_c > pme_c
        lost_before = pme_p > 0 and ap_p > pme_p
        if lost_now and not lost_before:
            status = "потерял 75%"
        elif lost_now:
            status = "ниже 75%"
        else:
            status = "75%"

        changes.append({
            "Артикул": r["offer_id_cur"] or "—",
            "Товар": r["name_cur"] or "",
            "Цена для 75%": _fmt_chg(pme_p, pme_c),
            "Цена для 15%": _fmt_chg(pmi_p, pmi_c),
            "Текущая цена по акции": ap_c,
            "Статус": status,
        })

    if changes:
        changes_df = pd.DataFrame(changes)
        order = {"потерял 75%": 0, "ниже 75%": 1, "75%": 2}
        changes_df = changes_df.sort_values(
            "Статус", key=lambda s: s.map(order).fillna(3)
        )
        n_lost = int((changes_df["Статус"] == "потерял 75%").sum())
        st.caption(
            f"Сравнение снимков {previous_date.strftime('%d.%m.%Y')} → "
            f"{latest_date.strftime('%d.%m.%Y')}"
        )
        if n_lost:
            st.warning(
                f"{n_lost} товаров потеряли бустинг 75%: цена по акции стала выше цены для 75%."
            )
        st.dataframe(
            changes_df, width="stretch", hide_index=True,
            column_config={
                "Текущая цена по акции": st.column_config.NumberColumn(format="₽ %,.0f"),
            },
        )
    else:
        st.info("Цены для бустинга не изменились между последними снимками.")

# ── таблица товаров ──────────────────────────────────────────────────────

st.divider()
st.subheader("Товары в акции")

c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    status_filter = st.selectbox(
        "Статус бустинга",
        ["Все", "Бустинг 75%+", "Бустинг 50-75%", "Бустинг ниже 50%", "Можно поднять мин. цену, сохранив 75%"],
    )
with c2:
    categories = sorted(latest["category"].unique().tolist())
    category_filter = st.selectbox("Категория", ["Все"] + categories)
with c3:
    search = st.text_input("Поиск по артикулу", "")

view = latest.copy()
if status_filter == "Бустинг 75%+":
    view = view[view["boost_group"] == "75%+"]
elif status_filter == "Бустинг 50-75%":
    view = view[view["boost_group"] == "50-75%"]
elif status_filter == "Бустинг ниже 50%":
    view = view[view["boost_group"] == "ниже 50%"]
elif status_filter == "Можно поднять мин. цену, сохранив 75%":
    view = view[view["can_raise_min"]]

if category_filter != "Все":
    view = view[view["category"] == category_filter]

if search:
    s = search.lower()
    mask = (
        view["offer_id"].astype(str).str.lower().str.contains(s, na=False)
        | view["name"].astype(str).str.lower().str.contains(s, na=False)
    )
    view = view[mask]

table = view[[
    "offer_id", "name", "category", "current_boost", "price_max_elastic",
    "action_price", "price_min_elastic", "min_price", "boost_group", "can_raise_min",
]].copy()
table.columns = [
    "Артикул", "Товар", "Категория", "Текущий бустинг %", "Цена для 75%",
    "Текущая цена по акции", "Цена для 15%", "Мин. цена", "Статус", "Можно поднять мин. цену",
]
table["Можно поднять мин. цену"] = table["Можно поднять мин. цену"].map({True: "Да", False: ""})
table = table.sort_values("Артикул")

common_config = {
    "Текущий бустинг %": st.column_config.NumberColumn(
        format="%.1f %%", help="Текущий бустинг товара в акции.",
    ),
    "Цена для 75%": st.column_config.NumberColumn(
        format="₽ %,.0f",
        help="Цена, при которой товар получает максимальный бустинг 75%.",
    ),
    "Текущая цена по акции": st.column_config.NumberColumn(
        format="₽ %,.0f", help="Текущая цена товара по акции.",
    ),
    "Цена для 15%": st.column_config.NumberColumn(
        format="₽ %,.0f",
        help="Цена, при которой товар получает минимальный бустинг 15%.",
    ),
    "Мин. цена": st.column_config.NumberColumn(
        format="₽ %,.0f",
        help="Минимальная цена товара — ниже неё опускать цену нельзя.",
    ),
}

st.dataframe(
    table, width="stretch", hide_index=True,
    column_config={
        **common_config,
        "Можно поднять мин. цену": st.column_config.TextColumn(
            help="«Да» — минимальную цену можно поднять (до цены для 75%), сохранив бустинг 75%.",
        ),
    },
)

# ── товары, выпавшие из бустинга 75% ─────────────────────────────────────

st.divider()
st.subheader("Товары, выпавшие из бустинга 75%")

dropped = latest[latest["current_boost"] < 75].copy()
if dropped.empty:
    st.info("Все товары сохраняют бустинг 75%.")
else:
    dropped_table = dropped[[
        "offer_id", "name", "category", "current_boost", "price_max_elastic",
        "action_price", "price_min_elastic", "min_price", "boost_group",
    ]].copy()
    dropped_table.columns = [
        "Артикул", "Товар", "Категория", "Текущий бустинг %", "Цена для 75%",
        "Текущая цена по акции", "Цена для 15%", "Мин. цена", "Статус",
    ]
    dropped_table = dropped_table.sort_values("Текущий бустинг %", ascending=False)
    st.dataframe(
        dropped_table, width="stretch", hide_index=True,
        column_config=common_config,
    )

# ── история по товару ────────────────────────────────────────────────────

st.divider()
st.subheader("История по товару")

uniq = latest[latest["offer_id"] != ""].drop_duplicates("offer_id")
name_map = dict(zip(uniq["offer_id"], uniq["name"]))
offers = sorted(name_map.keys())

if not offers:
    st.info("Нет артикулов для отображения истории.")
else:
    selected_offer = st.selectbox(
        "Артикул", offers,
        format_func=lambda o: f"{o} — {name_map.get(o, '')[:60]}",
    )

    hist = shop_df[shop_df["offer_id"].astype(str) == selected_offer].sort_values("snapshot_date")
    if hist.empty:
        st.info("Нет данных по этому артикулу.")
    else:
        hist_plot = hist.rename(columns={
            "price_max_elastic": "Цена для 75%",
            "action_price": "Текущая цена по акции",
        })
        fig = px.line(
            hist_plot, x="snapshot_date", y=["Цена для 75%", "Текущая цена по акции"],
            markers=True,
            title=f"Цены по снимкам — {selected_offer}",
        )
        fig.update_layout(
            xaxis_title=None, yaxis_title="Цена, ₽", height=350,
            legend=dict(orientation="h"),
        )
        st.plotly_chart(fig, width="stretch")
