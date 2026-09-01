"""Сводка по кабинетам — динамика с шагом 1 день."""
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.models import init_db, get_session, Order, DailyMetric, AdDailyStats, Experiment, ProductPrice
from etl.config import SHOP_LABELS, order_shops, FULL_COST_MULTIPLIER
from etl.shared_ui import build_presets, date_filter_section, add_experiment_markers, render_experiment_list, get_hidden_experiment_ids, resample_daily, GRANULARITIES, GRAN_TICKFMT, build_net_price_asof

st.title("Динамика продаж по кабинетам")

engine = init_db()
session = get_session(engine)
orders_raw = session.query(Order).all()
analytics_raw = session.query(DailyMetric).all()
ads_raw = session.query(AdDailyStats).all()
experiments_raw = session.query(Experiment).all()
prices_raw = session.query(ProductPrice).all()
session.close()
engine.dispose()

# ── себестоимость «на дату заказа» ────────────────────────────────────────
net_price_asof = build_net_price_asof(prices_raw)

order_rows = []
for o in orders_raw:
    try:
        prods = json.loads(o.ozon_costs_data) if o.ozon_costs_data else []
    except (json.JSONDecodeError, TypeError):
        prods = []
    revenue = sum(p.get("price", 0) * p.get("quantity", 1) for p in prods)
    order_date = o.order_date.date() if o.order_date else None
    cost = sum(
        net_price_asof(o.marketplace, p.get("offer_id"), order_date) * (p.get("quantity", 1) or 1)
        for p in prods
    )
    revenue_accepted = revenue if o.marketplace_status != "cancelled" else 0
    cost_accepted = cost if o.marketplace_status != "cancelled" else 0
    order_rows.append({
        "shop": o.marketplace,
        "date": o.order_date.date() if o.order_date else None,
        "status": o.marketplace_status,
        "revenue": revenue,
        "revenue_accepted": revenue_accepted,
        "cost_accepted": cost_accepted,
    })
orders_df = pd.DataFrame(order_rows)

analytics_df = pd.DataFrame([{
    "shop": a.shop, "date": a.date, "revenue": a.revenue, "units": a.ordered_units,
} for a in analytics_raw])

ads_df = pd.DataFrame([{
    "shop": a.shop, "date": a.date, "sku": a.sku, "spend": a.spend, "promo_revenue": a.promo_revenue or 0,
} for a in ads_raw]) if ads_raw else pd.DataFrame()

if orders_df.empty:
    st.warning("Нет данных. Запустите `python run_etl.py` для загрузки.")
    st.stop()

min_d = orders_df["date"].min()
max_d = date.today()

presets = build_presets(min_d, max_d)

shops_avail = order_shops(orders_df["shop"].unique().tolist())
col1, _ = st.columns(2)
with col1:
    selected_shops = [st.radio(
        "Кабинет", shops_avail, index=0, horizontal=True,
        format_func=lambda x: SHOP_LABELS.get(x, x),
    )]

date_from, date_to = date_filter_section(presets, min_d, max_d, prefix="w_")

# ── дни ───────────────────────────────────────────────────────────────────

def _days_between(start: date, end: date) -> list[date]:
    out = []
    d = start
    while d <= end:
        out.append(d)
        d += timedelta(days=1)
    return out


def _build_daily(selected: list[str], days: list[date]) -> pd.DataFrame:
    rows = []
    for shop in selected:
        for day in days:
            anal = analytics_df[
                (analytics_df["shop"] == shop) & (analytics_df["date"] == day)
            ]
            rev_orders = anal["revenue"].sum()
            units_orders = int(anal["units"].sum())

            deliv = orders_df[
                (orders_df["shop"] == shop)
                & (orders_df["status"] != "cancelled")
                & (orders_df["date"] == day)
            ]
            rev_accepted = deliv["revenue_accepted"].sum()
            units_accepted = len(deliv)
            cost_accepted = deliv["cost_accepted"].sum()

            ad_spend = 0.0
            rev_ads = 0.0
            if not ads_df.empty:
                ad = ads_df[(ads_df["shop"] == shop) & (ads_df["date"] == day)]
                if not ad.empty:
                    if "spend" in ad.columns:
                        ad_spend = ad["spend"].sum()
                    # Выручка с рекламы — только по строкам с конкретным SKU:
                    # строки с пустым SKU дублируют итог кампании (завышают сумму).
                    if "promo_revenue" in ad.columns:
                        ad_sku = ad[ad["sku"].fillna("").astype(str) != ""]
                        rev_ads = ad_sku["promo_revenue"].sum()

            drr_o = (ad_spend / rev_orders * 100) if rev_orders > 0 else 0.0
            drr_a = (ad_spend / rev_accepted * 100) if rev_accepted > 0 else 0.0

            rows.append({
                "shop": shop, "date": day, "label": day.strftime("%d.%m"),
                "rev_orders": rev_orders, "units_orders": units_orders,
                "rev_accepted": rev_accepted, "units_accepted": units_accepted,
                "cost_accepted": cost_accepted,
                "profit_accepted": rev_accepted - cost_accepted * FULL_COST_MULTIPLIER,
                "rev_ads": rev_ads,
                "ad_spend": ad_spend, "drr_orders": drr_o, "drr_accepted": drr_a,
            })
    return pd.DataFrame(rows)


METRICS = {
    "rev_orders": "Выручка по заказам",
    "rev_accepted": "Выручка по принятым",
    "profit_accepted": "Прибыль по принятым",
    "rev_ads": "Выручка с рекламы",
    "ad_spend": "Расходы на рекламу",
    "drr_orders": "ДРР по заказам",
    "drr_accepted": "ДРР по принятым",
}

METRIC_HELP = {
    "rev_orders": "Выручка по всем заказам из аналитики Ozon (включая ещё не доставленные).",
    "rev_accepted": "Выручка по принятым заказам — без отмен (доставлен + в пути).",
    "profit_accepted": "Прибыль по принятым = выручка − полная себестоимость (себестоимость × 1.795: +55% комиссия, +15% ДРР, +7% налог, +2.5% эквайринг).",
    "rev_ads": "Выручка, приписанная рекламным кампаниям (Performance API, promo_revenue).",
    "ad_spend": "Расход на рекламу (Performance API).",
    "drr_orders": "ДРР = расход на рекламу ÷ выручка по заказам.",
    "drr_accepted": "ДРР = расход на рекламу ÷ выручка по принятым.",
}

days_in_range = _days_between(date_from, date_to)
period_len = len(days_in_range)
prev_from = date_from - timedelta(days=period_len)
prev_to = date_from - timedelta(days=1)
prev_days = _days_between(prev_from, prev_to)

daily_df = _build_daily(selected_shops, days_in_range)
prev_daily_df = _build_daily(selected_shops, prev_days)

if daily_df.empty:
    st.warning("Нет данных за выбранный период.")
    st.stop()

metrics = st.multiselect(
    "Метрики", list(METRICS.keys()), default=["rev_accepted"],
    format_func=lambda m: METRICS[m],
)
granularity = st.radio("Деление оси X", GRANULARITIES, index=0, horizontal=True)
show_prev = st.checkbox("Показывать прошлый период", value=False)

SUM_COLS = ["rev_orders", "units_orders", "rev_accepted", "units_accepted", "ad_spend", "cost_accepted", "rev_ads"]

cur_plot = resample_daily(daily_df, granularity, "shop", SUM_COLS)
prev_plot = resample_daily(prev_daily_df, granularity, "shop", SUM_COLS)
for p in (cur_plot, prev_plot):
    p["drr_orders"] = (p["ad_spend"] / p["rev_orders"].where(p["rev_orders"] > 0)).fillna(0.0) * 100
    p["drr_accepted"] = (p["ad_spend"] / p["rev_accepted"].where(p["rev_accepted"] > 0)).fillna(0.0) * 100
    p["profit_accepted"] = p["rev_accepted"] - p["cost_accepted"] * FULL_COST_MULTIPLIER

cur_x_map = {(r["shop"], int(r["pos"])): r["date"] for _, r in cur_plot.iterrows()}
prev_plot["x"] = prev_plot.apply(lambda r: cur_x_map.get((r["shop"], int(r["pos"]))), axis=1)
prev_plot = prev_plot.dropna(subset=["x"])

if not metrics:
    st.info("Выберите хотя бы одну метрику.")
else:
    MONEY_METRICS = {"rev_orders", "rev_accepted", "profit_accepted", "rev_ads", "ad_spend"}
    DRR_METRICS = {"drr_orders", "drr_accepted"}
    METRIC_COLORS = {
        "rev_orders": "#2962FF",
        "rev_accepted": "#00C853",
        "profit_accepted": "#FF6D00",
        "rev_ads": "#D500F9",
        "ad_spend": "#D50000",
        "drr_orders": "#6D4C41",
        "drr_accepted": "#00838F",
    }
    SHOP_DASHES = ["solid", "dash", "dot", "dashdot", "longdash"]

    st.subheader("Совмещённые метрики")
    for m in metrics:
        st.caption(f"{METRICS[m]} — {METRIC_HELP.get(m, '')}")

    fig = go.Figure()

    for shop_i, shop in enumerate(selected_shops):
        dash = SHOP_DASHES[shop_i % len(SHOP_DASHES)]
        for m in metrics:
            color = METRIC_COLORS.get(m, "#999")
            yaxis = "y" if m in MONEY_METRICS else "y2"
            is_drr = m in DRR_METRICS
            hover_fmt = "%{y:.2f}%" if is_drr else "%{y:,.0f} ₽"

            shop_data = cur_plot[cur_plot["shop"] == shop].sort_values("date")
            if not shop_data.empty:
                text_labels = [f"{v:.1f}%" if pd.notna(v) else "" for v in shop_data[m]] if is_drr else None
                fig.add_trace(go.Scatter(
                    x=shop_data["date"], y=shop_data[m],
                    mode="lines+markers+text" if is_drr else "lines+markers",
                    name=f"{SHOP_LABELS.get(shop, shop)} · {METRICS[m]}",
                    line=dict(color=color, width=2, dash=dash),
                    marker=dict(size=4),
                    yaxis=yaxis,
                    text=text_labels,
                    textposition="top center" if is_drr else None,
                    textfont=dict(size=15),
                    customdata=shop_data["label"].tolist(),
                    hovertemplate="%{customdata}<br>" + hover_fmt + "<extra>%{fullData.name}</extra>",
                ))

            if show_prev:
                prev_shop_data = prev_plot[prev_plot["shop"] == shop].sort_values("date")
                if not prev_shop_data.empty:
                    fig.add_trace(go.Scatter(
                        x=prev_shop_data["x"], y=prev_shop_data[m],
                        mode="lines+markers", name=f"{SHOP_LABELS.get(shop, shop)} · {METRICS[m]} · прошлый",
                        line=dict(color=color, width=1.5, dash=dash),
                        marker=dict(size=3),
                        opacity=0.45,
                        yaxis=yaxis,
                        customdata=prev_shop_data["label"].tolist(),
                        hovertemplate="%{customdata}<br>" + hover_fmt + "<extra>%{fullData.name}</extra>",
                    ))

    has_money = any(m in MONEY_METRICS for m in metrics)
    has_pct = any(m not in MONEY_METRICS for m in metrics)
    layout = dict(
        xaxis_title=None,
        yaxis_title="₽" if has_money else "%",
        hovermode="x unified", height=450,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5, font=dict(size=16)),
        margin=dict(t=10, b=10, l=10, r=10),
        font=dict(size=17),
    )
    if has_pct:
        layout["yaxis2"] = dict(title="%", overlaying="y", side="right")
    fig.update_layout(**layout)
    fig.update_xaxes(tickformat=GRAN_TICKFMT.get(granularity, "%d.%m"))
    hidden_ids = get_hidden_experiment_ids(experiments_raw, date_from, date_to, selected_shops=selected_shops)
    add_experiment_markers(fig, experiments_raw, date_from, date_to, selected_shops=selected_shops, exclude_ids=hidden_ids)
    st.plotly_chart(fig, width="stretch")

    render_experiment_list(experiments_raw, date_from, date_to, selected_shops=selected_shops)

    if any(m in ("ad_spend", "drr_orders", "drr_accepted", "rev_ads") for m in metrics) and daily_df["ad_spend"].sum() == 0:
        st.caption(
            "Реклама за выбранный период не выгружена — данные Performance API отсутствуют. "
            "Обновите рекламу: `python run_etl.py`."
        )

st.divider()

st.subheader("Итого за период")

summary_rows = []
for shop in selected_shops:
    sd = daily_df[daily_df["shop"] == shop]
    if sd.empty:
        continue

    summary_rows.append({
        "Кабинет": SHOP_LABELS.get(shop, shop),
        "Заказы": f"{sd['rev_orders'].sum():,.0f} ₽ / {int(sd['units_orders'].sum())} шт",
        "Принято": f"{sd['rev_accepted'].sum():,.0f} ₽ / {int(sd['units_accepted'].sum())} шт",
        "Прибыль по принятым": f"{sd['profit_accepted'].sum():,.0f} ₽",
        "Выручка с рекламы": f"{sd['rev_ads'].sum():,.0f} ₽",
        "Реклама": f"{sd['ad_spend'].sum():,.0f} ₽",
        "ДРР по заказам": f"{sd['ad_spend'].sum() / sd['rev_orders'].sum() * 100 if sd['rev_orders'].sum() > 0 else 0:.1f}%",
        "ДРР по принятым": f"{sd['ad_spend'].sum() / sd['rev_accepted'].sum() * 100 if sd['rev_accepted'].sum() > 0 else 0:.1f}%",
    })

st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True)

st.divider()

st.subheader("Сравнение с прошлым периодом")

for shop in selected_shops:
    sd = daily_df[daily_df["shop"] == shop]
    prev = prev_daily_df[prev_daily_df["shop"] == shop]
    if sd.empty and prev.empty:
        continue

    label = SHOP_LABELS.get(shop, shop)

    cur_rev_o = sd["rev_orders"].sum()
    cur_u_o = int(sd["units_orders"].sum())
    cur_rev_a = sd["rev_accepted"].sum()
    cur_u_a = int(sd["units_accepted"].sum())
    cur_profit_a = sd["profit_accepted"].sum()
    cur_rev_ads = sd["rev_ads"].sum()
    cur_ad = sd["ad_spend"].sum()
    cur_drr_o = cur_ad / cur_rev_o * 100 if cur_rev_o > 0 else 0
    cur_drr_a = cur_ad / cur_rev_a * 100 if cur_rev_a > 0 else 0

    prev_rev_o = prev["rev_orders"].sum()
    prev_u_o = int(prev["units_orders"].sum())
    prev_rev_a = prev["rev_accepted"].sum()
    prev_u_a = int(prev["units_accepted"].sum())
    prev_profit_a = prev["profit_accepted"].sum()
    prev_rev_ads = prev["rev_ads"].sum()
    prev_ad = prev["ad_spend"].sum()
    prev_drr_o = prev_ad / prev_rev_o * 100 if prev_rev_o > 0 else 0
    prev_drr_a = prev_ad / prev_rev_a * 100 if prev_rev_a > 0 else 0

    rows = [
        {"Показатель": "Выручка по заказам",
         "Текущий период": f"{cur_rev_o:,.0f} ₽ / {cur_u_o} шт",
         "Прошлый период": f"{prev_rev_o:,.0f} ₽ / {prev_u_o} шт"},
        {"Показатель": "Выручка по принятым",
         "Текущий период": f"{cur_rev_a:,.0f} ₽ / {cur_u_a} шт",
         "Прошлый период": f"{prev_rev_a:,.0f} ₽ / {prev_u_a} шт"},
        {"Показатель": "Прибыль по принятым",
         "Текущий период": f"{cur_profit_a:,.0f} ₽",
         "Прошлый период": f"{prev_profit_a:,.0f} ₽"},
        {"Показатель": "Выручка с рекламы",
         "Текущий период": f"{cur_rev_ads:,.0f} ₽",
         "Прошлый период": f"{prev_rev_ads:,.0f} ₽"},
        {"Показатель": "Реклама",
         "Текущий период": f"{cur_ad:,.0f} ₽",
         "Прошлый период": f"{prev_ad:,.0f} ₽"},
        {"Показатель": "ДРР по заказам",
         "Текущий период": f"{cur_drr_o:.1f}%",
         "Прошлый период": f"{prev_drr_o:.1f}%"},
        {"Показатель": "ДРР по принятым",
         "Текущий период": f"{cur_drr_a:.1f}%",
         "Прошлый период": f"{prev_drr_a:.1f}%"},
    ]

    st.markdown(f"**{label}**")
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

st.caption(
    f"Текущий период: {date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')} · "
    f"Прошлый период: {prev_from.strftime('%d.%m.%Y')} – {prev_to.strftime('%d.%m.%Y')}"
)
