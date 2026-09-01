"""Динамика продаж по артикулам (SKU)."""
import json
import os
import sys
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.models import init_db, get_session, Order, ProductMapping, ProductPrice, Experiment
from etl.config import SHOP_LABELS, order_shops, FULL_COST_MULTIPLIER
from etl.shared_ui import (
    build_presets, date_filter_section, resample_daily, GRANULARITIES, GRAN_TICKFMT,
    build_net_price_asof, add_experiment_markers, render_experiment_list,
    get_hidden_experiment_ids,
)

st.title("Динамика продаж по артикулам (SKU)")

engine = init_db()
session = get_session(engine)
orders_raw = session.query(Order).all()
mapping_raw = session.query(ProductMapping).all()
prices_raw = session.query(ProductPrice).all()
experiments_raw = session.query(Experiment).all()
session.close()
engine.dispose()

# ── себестоимость «на дату заказа» ────────────────────────────────────────
net_price_asof = build_net_price_asof(prices_raw)

# ── данные по артикулам (ключ — offer_id) ────────────────────────────────

sku_rows = []
for o in orders_raw:
    try:
        prods = json.loads(o.ozon_costs_data) if o.ozon_costs_data else []
    except (json.JSONDecodeError, TypeError):
        prods = []
    order_date = o.order_date.date() if o.order_date else None
    for p in prods:
        sku_rows.append({
            "shop": o.marketplace,
            "offer_id": p.get("offer_id"),
            "name": p.get("name", ""),
            "date": order_date,
            "status": o.marketplace_status,
            "quantity": p.get("quantity", 1) or 1,
            "price": p.get("price", 0) or 0,
            "net_price": net_price_asof(o.marketplace, p.get("offer_id"), order_date),
        })
sku_df = pd.DataFrame(sku_rows)

offer_to_name = {m.offer_id: (m.name or "") for m in mapping_raw if m.offer_id}
for oid, nm in zip(sku_df["offer_id"], sku_df["name"]):
    if oid and oid not in offer_to_name and nm:
        offer_to_name[oid] = nm

if sku_df.empty:
    st.warning("Нет данных. Запустите `python run_etl.py` для загрузки.")
    st.stop()

min_d = sku_df["date"].min()
max_d = date.today()

presets = build_presets(min_d, max_d)

shops_avail = order_shops(sku_df["shop"].unique().tolist())
col1, _ = st.columns(2)
with col1:
    selected_shops = st.multiselect(
        "Кабинет", shops_avail, default=shops_avail,
        format_func=lambda x: SHOP_LABELS.get(x, x),
    )

date_from, date_to = date_filter_section(presets, min_d, max_d, prefix="skd_")

# ── топ популярных артикулов за всё время ────────────────────────────────

alltime = (
    sku_df[
        sku_df["shop"].isin(selected_shops)
        & (sku_df["status"] != "cancelled")
        & (sku_df["offer_id"].notna())
        & (sku_df["offer_id"] != "")
    ]
    .assign(
        revenue=lambda d: d["price"] * d["quantity"],
        cost=lambda d: d["net_price"] * d["quantity"],
    )
    .groupby("offer_id")
    .agg(units=("quantity", "sum"), revenue=("revenue", "sum"), cost=("cost", "sum"))
    .reset_index()
)
alltime["profit"] = alltime["revenue"] - alltime["cost"] * FULL_COST_MULTIPLIER
alltime = alltime.sort_values("units", ascending=False).reset_index(drop=True)

# ── динамика популярных артикулов (тепловая карта) ────────────────────────

st.subheader("Динамика популярных артикулов")
st.caption("Топ артикулов за выбранный период; столбцы — временные отрезки внутри периода.")

METRIC_COL = {"Продано (шт)": "units", "Выручка (₽)": "revenue", "Прибыль (₽)": "profit"}

period_days = (date_to - date_from).days + 1
if period_days <= 14:
    default_gran = "день"
elif period_days <= 62:
    default_gran = "неделя"
elif period_days <= 400:
    default_gran = "месяц"
elif period_days <= 1600:
    default_gran = "квартал"
else:
    default_gran = "год"

pop_c1, pop_c2, pop_c3 = st.columns([2, 3, 1])
with pop_c1:
    pop_metric = st.selectbox(
        "Метрика", ["Продано (шт)", "Выручка (₽)", "Прибыль (₽)"], key="pop_metric"
    )
with pop_c2:
    pop_gran = st.radio(
        "Отрезки", GRANULARITIES, index=GRANULARITIES.index(default_gran),
        horizontal=True, key="pop_heat_gran",
    )
with pop_c3:
    pop_n = st.selectbox("Артикулов", ["Топ 10", "Топ 20"], key="pop_n")

metric_col = METRIC_COL[pop_metric]
n_top = 10 if pop_n == "Топ 10" else 20
cross_year = date_from.year != date_to.year


def _bucket_key_label(d, gran):
    ts = pd.Timestamp(d)
    if gran == "день":
        key = ts.normalize()
        lbl = ts.strftime("%d.%m.%y") if cross_year else ts.strftime("%d.%m")
    elif gran == "неделя":
        key = (ts - pd.Timedelta(days=ts.weekday())).normalize()
        lbl = key.strftime("%d.%m.%y") if cross_year else key.strftime("%d.%m")
    elif gran == "месяц":
        key = pd.Timestamp(year=ts.year, month=ts.month, day=1)
        lbl = ts.strftime("%m.%y")
    elif gran == "квартал":
        key = pd.Timestamp(year=ts.year, month=(ts.month - 1) // 3 * 3 + 1, day=1)
        lbl = f"{(ts.month - 1) // 3 + 1} кв {str(ts.year)[2:]}"
    else:
        key = pd.Timestamp(year=ts.year, month=1, day=1)
        lbl = str(ts.year)
    return key, lbl


dyn = sku_df[
    sku_df["shop"].isin(selected_shops)
    & (sku_df["status"] != "cancelled")
    & sku_df["date"].notna()
    & sku_df["offer_id"].notna()
    & (sku_df["offer_id"] != "")
    & (sku_df["date"] >= date_from)
    & (sku_df["date"] <= date_to)
].copy()
dyn["revenue"] = dyn["price"] * dyn["quantity"]
dyn["cost"] = dyn["net_price"] * dyn["quantity"]

top_offers = []
if not dyn.empty:
    per_article = (
        dyn.groupby("offer_id")
        .agg(units=("quantity", "sum"), revenue=("revenue", "sum"), cost=("cost", "sum"))
        .reset_index()
    )
    per_article["profit"] = (
        per_article["revenue"] - per_article["cost"] * FULL_COST_MULTIPLIER
    )
    per_article = per_article.sort_values(metric_col, ascending=False)
    top_offers = per_article.head(n_top)["offer_id"].tolist()

if top_offers:
    axis_pairs = sorted(
        {_bucket_key_label(d, pop_gran) for d in pd.date_range(date_from, date_to)}
    )
    axis_keys = [k for k, _ in axis_pairs]
    axis_labels = [lbl for _, lbl in axis_pairs]

    dyn_top = dyn[dyn["offer_id"].isin(top_offers)].copy()
    dyn_top["bkey"] = [_bucket_key_label(d, pop_gran)[0] for d in dyn_top["date"]]

    grouped = (
        dyn_top.groupby(["offer_id", "bkey"])
        .agg(units=("quantity", "sum"), revenue=("revenue", "sum"), cost=("cost", "sum"))
        .reset_index()
    )
    grouped["profit"] = grouped["revenue"] - grouped["cost"] * FULL_COST_MULTIPLIER

    pivot = grouped.pivot(index="offer_id", columns="bkey", values=metric_col)
    pivot = pivot.reindex(index=top_offers, columns=axis_keys).fillna(0)

    heat = pivot.copy()
    heat.columns = axis_labels
    heat.index.name = "Артикул"
    heat.insert(0, "Товар", [offer_to_name.get(o, "") or "" for o in heat.index])

    vals = heat[axis_labels].to_numpy(dtype=float)
    vmin = float(vals.min())
    vmax = float(vals.max())

    def _heat_bg(t: float) -> str:
        def _lerp(a, b, k):
            return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))
        if t < 0.5:
            c = _lerp((255, 205, 210), (255, 255, 224), t * 2)
        else:
            c = _lerp((255, 255, 224), (200, 230, 201), (t - 0.5) * 2)
        return f"background-color: rgb({c[0]},{c[1]},{c[2]})"

    def _cell_color(v):
        if pd.isna(v):
            return ""
        t = 0.5 if vmax == vmin else (float(v) - vmin) / (vmax - vmin)
        return _heat_bg(t)

    styler = heat.style.map(_cell_color, subset=axis_labels)
    if metric_col == "units":
        styler = styler.format("{:,.0f}", subset=axis_labels)
    else:
        styler = styler.format(lambda v: f"{v / 1000:,.0f}к", subset=axis_labels)
    st.dataframe(styler, width="stretch")

    gran_caption = {
        "день": "Столбцы — дни (дд.мм).",
        "неделя": "Столбцы — недели (дата — понедельник).",
        "месяц": "Столбцы — месяцы (мм.гг).",
        "квартал": "Столбцы — кварталы.",
        "год": "Столбцы — годы.",
    }[pop_gran]
    cap = gran_caption + (" Денежные значения — в тысячах ₽." if metric_col != "units" else "")
    if len(axis_labels) > 120:
        cap += " Отрезков много — листайте таблицу или выберите деление крупнее."
    st.caption(cap)

    sel_label = st.selectbox(
        "Артикул для графика",
        top_offers,
        format_func=lambda o: f"{offer_to_name.get(o, o) or o} ({o})",
        key="pop_sel",
    )

    # эксперименты: без привязки к артикулу — все; с привязкой — только этот артикул
    exp_for_offer = [
        e for e in experiments_raw
        if not (e.offer_ids or "").strip()
        or sel_label in {o.strip() for o in str(e.offer_ids).split(",") if o.strip()}
    ]

    d_sel = sku_df[
        (sku_df["offer_id"] == sel_label)
        & sku_df["shop"].isin(selected_shops)
        & (sku_df["status"] != "cancelled")
        & sku_df["date"].notna()
        & (sku_df["date"] >= date_from)
        & (sku_df["date"] <= date_to)
    ]
    daily_sel = (
        d_sel.assign(
            revenue=lambda x: x["price"] * x["quantity"],
            cost=lambda x: x["net_price"] * x["quantity"],
        )
        .groupby("date")
        .agg(revenue=("revenue", "sum"), cost=("cost", "sum"), units=("quantity", "sum"))
        .reset_index()
    )
    daily_sel["profit"] = daily_sel["revenue"] - daily_sel["cost"] * FULL_COST_MULTIPLIER

    SKU_GRAPH_METRICS = {
        "revenue": "Выручка (₽)",
        "profit": "Прибыль (₽)",
        "units": "Продано (шт)",
    }
    MONEY_METRICS = {"revenue", "profit"}
    METRIC_COLORS = {"revenue": "#2962FF", "profit": "#FF6D00", "units": "#00C853"}

    graph_metrics = st.multiselect(
        "Метрики", list(SKU_GRAPH_METRICS.keys()), default=["revenue", "units"],
        format_func=lambda m: SKU_GRAPH_METRICS[m],
        key="pop_graph_metrics",
    )
    graph_gran = st.radio(
        "Деление оси X", GRANULARITIES, index=0, horizontal=True, key="pop_graph_gran"
    )

    if not graph_metrics:
        st.info("Выберите хотя бы одну метрику.")
    elif daily_sel.empty:
        st.info("Нет продаж по выбранному артикулу за период.")
    else:
        daily_sel["_g"] = 1
        plot = resample_daily(
            daily_sel, graph_gran, "_g", ["revenue", "profit", "units"]
        )

        fig_pop = go.Figure()
        has_money = any(m in MONEY_METRICS for m in graph_metrics)
        has_units = any(m not in MONEY_METRICS for m in graph_metrics)
        for m in graph_metrics:
            fig_pop.add_trace(go.Scatter(
                x=plot["date"], y=plot[m],
                mode="lines+markers", name=SKU_GRAPH_METRICS[m],
                line=dict(color=METRIC_COLORS.get(m, "#999"), width=2),
                marker=dict(size=5),
                yaxis="y" if m in MONEY_METRICS else "y2",
                text=plot["label"],
                hovertemplate="%{text}<br>%{y:,.0f}<extra>%{fullData.name}</extra>",
            ))
        fig_pop.update_layout(
            height=320, xaxis_title=None,
            yaxis_title="₽" if has_money else "шт",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(t=10, b=10, l=10, r=10),
        )
        if has_units:
            fig_pop.update_layout(yaxis2=dict(title="шт", overlaying="y", side="right"))
        fig_pop.update_xaxes(tickformat=GRAN_TICKFMT.get(graph_gran, "%d.%m"))
        hidden_exp = get_hidden_experiment_ids(
            exp_for_offer, date_from, date_to, selected_shops=selected_shops
        )
        add_experiment_markers(
            fig_pop, exp_for_offer, date_from, date_to,
            selected_shops=selected_shops, exclude_ids=hidden_exp,
        )
        st.plotly_chart(fig_pop, width="stretch")
        render_experiment_list(
            exp_for_offer, date_from, date_to, selected_shops=selected_shops
        )
else:
    st.info("Нет продаж для построения динамики.")

st.divider()

st.subheader("Топ артикулов по выручке за период")

sku_sel = sku_df[
    sku_df["shop"].isin(selected_shops)
    & sku_df["date"].notna()
    & (sku_df["date"] >= date_from)
    & (sku_df["date"] <= date_to)
    & (sku_df["status"] != "cancelled")
]

top = (
    sku_sel.assign(
        revenue=lambda d: d["price"] * d["quantity"],
        cost=lambda d: d["net_price"] * d["quantity"],
    )
    .groupby("offer_id")
    .agg(revenue=("revenue", "sum"), cost=("cost", "sum"), units=("quantity", "sum"))
    .sort_values("revenue", ascending=False)
    .head(20)
    .reset_index()
)
top["profit"] = top["revenue"] - top["cost"] * FULL_COST_MULTIPLIER
top["Товар"] = top["offer_id"].map(offer_to_name).fillna("")
top = top[["offer_id", "Товар", "revenue", "profit", "units"]]
top.columns = ["Артикул", "Товар", "Выручка", "Прибыль", "Продано"]
st.dataframe(
    top, width="stretch", hide_index=True,
    column_config={
        "Выручка": st.column_config.NumberColumn(format="₽ %,.0f"),
        "Прибыль": st.column_config.NumberColumn(format="₽ %,.0f"),
    },
)

st.divider()

st.subheader("Самые популярные артикулы (за всё время)")

popular = alltime.head(20).copy()
popular["Товар"] = popular["offer_id"].map(offer_to_name).fillna("")
popular = popular[["offer_id", "Товар", "units", "revenue", "profit"]]
popular.columns = ["Артикул", "Товар", "Продано", "Выручка", "Прибыль"]
st.dataframe(
    popular, width="stretch", hide_index=True,
    column_config={
        "Продано": st.column_config.NumberColumn(format="%d"),
        "Выручка": st.column_config.NumberColumn(format="₽ %,.0f"),
        "Прибыль": st.column_config.NumberColumn(format="₽ %,.0f"),
    },
)

st.caption(
    f"Текущий период: {date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')}"
)
