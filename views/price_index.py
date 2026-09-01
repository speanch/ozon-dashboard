"""Ценовой индекс — сравнение цены товара с рынком (внешние площадки и Ozon)."""
import json
import os
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.models import init_db, get_session, ProductPrice, ProductMapping
from etl.config import SHOP_LABELS, order_shops

st.title("Ценовой индекс")
st.caption("Насколько цена товара отличается от рыночной.")

engine = init_db()
session = get_session(engine)
prices_raw = session.query(ProductPrice).all()
mapping_raw = session.query(ProductMapping).all()
session.close()
engine.dispose()

if not prices_raw:
    st.warning("Нет данных. Запустите `python run_etl.py` для загрузки.")
    st.stop()

# ── пояснения ─────────────────────────────────────────────────────────────

with st.expander("Что такое ценовой индекс?", expanded=False):
    st.markdown(
        """
**Ценовой индекс** показывает, насколько цена вашего товара отличается от рыночной
цены аналогичных товаров.

- **Числовой индекс:** меньше **1** — ваша цена **ниже** рынка (конкурентная);
  **1** — на уровне рынка; больше **1** — **выше** рынка.
- **Цвет индекса:**
  - 🟢 **Суперцена** (`SUPER`) — цена заметно ниже рынка;
  - 🟢 **Ниже рынка** (`GREEN`);
  - 🟡 **На уровне рынка** (`YELLOW`);
  - 🔴 **Выше рынка** (`RED`);
  - ⚪ **Нет индекса** (`WITHOUT_INDEX`) — недостаточно данных для сравнения.

> В нашей выгрузке заполнен индекс по **внешним площадкам** (другие маркетплейсы);
> индекс по Ozon (другие продавцы Ozon) в выгрузке пустой.
"""
    )

# ── данные ────────────────────────────────────────────────────────────────

offer_to_name = {m.offer_id: (m.name or "") for m in mapping_raw if m.offer_id}

rows = []
for p in prices_raw:
    raw = json.loads(p.raw_json) if p.raw_json else {}
    pi = raw.get("price_indexes") or {}
    ext = pi.get("external_index_data") or {}
    rows.append({
        "shop": p.shop,
        "snapshot_date": p.snapshot_date,
        "offer_id": p.offer_id or "",
        "price": p.price or 0,
        "old_price": p.old_price or 0,
        "min_price": p.min_price or 0,
        "net_price": p.net_price or 0,
        "color_index": p.color_index or "",
        "external_index": float(ext.get("price_index_value") or 0),
    })
df = pd.DataFrame(rows)

latest_date = df["snapshot_date"].max()
st.caption(f"Снимок цен от {latest_date.strftime('%d.%m.%Y')}")

COLOR_INDEX_LABELS = {
    "SUPER": "Суперцена",
    "GREEN": "Ниже рынка",
    "YELLOW": "На уровне рынка",
    "RED": "Выше рынка",
    "WITHOUT_INDEX": "Нет индекса",
}

# ── фильтр по кабинету ────────────────────────────────────────────────────

shops_avail = order_shops(df["shop"].unique().tolist())
selected_shop = st.selectbox(
    "Кабинет", shops_avail, format_func=lambda x: SHOP_LABELS.get(x, x),
)

shop_df = df[df["shop"] == selected_shop].copy()
shop_df["Цвет"] = shop_df["color_index"].map(COLOR_INDEX_LABELS).fillna("Нет индекса")

latest = shop_df[shop_df["snapshot_date"] == latest_date].copy()

if latest.empty:
    st.info("Нет данных по выбранному кабинету.")
    st.stop()

# ── сводка ────────────────────────────────────────────────────────────────

total = len(latest)
with_index = int((latest["external_index"] > 0).sum())
n_super = int((latest["color_index"] == "SUPER").sum())
n_above = int((latest["external_index"] > 1).sum())

m1, m2, m3, m4 = st.columns(4)
m1.metric("Товаров в снимке", total)
m2.metric("С индексом", with_index, help="Товары, для которых посчитан ценовой индекс.")
m3.metric("Суперцена", n_super, help="Цена заметно ниже рынка — конкурентная.")
m4.metric("Дороже рынка", n_above, help="Индекс больше 1 — цена выше рыночной.")

# ── распределение по цвету ────────────────────────────────────────────────

st.subheader("Распределение по цвету индекса")

color_counts = (
    latest["Цвет"]
    .value_counts()
    .reindex(["Суперцена", "Ниже рынка", "На уровне рынка", "Выше рынка", "Нет индекса"])
    .fillna(0)
    .astype(int)
    .reset_index()
)
color_counts.columns = ["Цвет", "Количество"]

fig_color = px.bar(
    color_counts, x="Цвет", y="Количество", text="Количество",
    color="Цвет",
    color_discrete_map={
        "Суперцена": "#1E88E5",
        "Ниже рынка": "#43A047",
        "На уровне рынка": "#FBC02D",
        "Выше рынка": "#E53935",
        "Нет индекса": "#9E9E9E",
    },
    title="Сколько товаров в каждом цвете",
)
fig_color.update_layout(showlegend=False, height=360, xaxis_title=None)
st.plotly_chart(fig_color, width="stretch")

# ── числовой индекс ───────────────────────────────────────────────────────

st.subheader("Числовой индекс (внешние площадки)")

indexed = latest[latest["external_index"] > 0].copy()
if indexed.empty:
    st.info("Нет товаров с рассчитанным индексом.")
else:
    fig_hist = px.histogram(
        indexed, x="external_index", nbins=20,
        title="Распределение числового индекса (1 = на уровне рынка)",
        labels={"external_index": "Индекс"},
    )
    fig_hist.add_vline(x=1.0, line_dash="dash", line_color="#E53935",
                       annotation_text="уровень рынка", annotation_position="top right")
    fig_hist.update_layout(height=360, yaxis_title="Товаров")
    st.plotly_chart(fig_hist, width="stretch")

# ── таблица товаров с индексом ────────────────────────────────────────────

st.subheader("Товары с индексом")

if indexed.empty:
    st.info("Нет товаров с индексом.")
else:
    table = indexed.copy()
    table["Название"] = table["offer_id"].map(offer_to_name).fillna("")
    table["Индекс"] = table["external_index"].round(3)
    table["Маржа, ₽"] = (table["price"] - table["net_price"]).round(0)
    table["Маржа, %"] = (
        ((table["price"] - table["net_price"]) / table["price"] * 100)
        .where(table["price"] > 0, 0)
        .round(1)
    )
    table = table[
        ["offer_id", "Название", "Цвет", "Индекс", "price", "net_price", "Маржа, ₽", "Маржа, %"]
    ].copy()
    table.columns = ["Артикул", "Товар", "Цвет индекса", "Индекс", "Цена", "Себестоимость", "Маржа, ₽", "Маржа, %"]
    table = table.sort_values("Индекс")

    st.dataframe(
        table, width="stretch", hide_index=True,
        column_config={
            "Индекс": st.column_config.NumberColumn(format="%.3f"),
            "Цена": st.column_config.NumberColumn(format="₽ %,.0f"),
            "Себестоимость": st.column_config.NumberColumn(format="₽ %,.0f"),
            "Маржа, ₽": st.column_config.NumberColumn(format="₽ %,.0f"),
            "Маржа, %": st.column_config.NumberColumn(format="%.1f %%"),
        },
    )

    st.download_button(
        "Скачать таблицу (CSV)",
        data=table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"sebestoimost_{selected_shop}_{latest_date.strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )
