"""Сводка по кабинетам — динамика с шагом 1 день."""
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.models import init_db, get_session, Order, DailyMetric, AdDailyStats
from etl.config import SHOP_LABELS, SHOP_COLORS
from etl.shared_ui import build_presets, date_filter_section

st.title("Динамика по кабинетам")

engine = init_db()
session = get_session(engine)
orders_raw = session.query(Order).all()
analytics_raw = session.query(DailyMetric).all()
ads_raw = session.query(AdDailyStats).all()
session.close()
engine.dispose()

order_rows = []
for o in orders_raw:
    try:
        prods = json.loads(o.ozon_costs_data) if o.ozon_costs_data else []
    except (json.JSONDecodeError, TypeError):
        prods = []
    revenue = sum(p.get("price", 0) * p.get("quantity", 1) for p in prods)
    revenue_accepted = revenue if o.marketplace_status != "cancelled" else 0
    order_rows.append({
        "shop": o.marketplace,
        "date": o.order_date.date() if o.order_date else None,
        "status": o.marketplace_status,
        "revenue": revenue,
        "revenue_accepted": revenue_accepted,
    })
orders_df = pd.DataFrame(order_rows)

analytics_df = pd.DataFrame([{
    "shop": a.shop, "date": a.date, "revenue": a.revenue, "units": a.ordered_units,
} for a in analytics_raw])

ads_df = pd.DataFrame([{
    "shop": a.shop, "date": a.date, "spend": a.spend,
} for a in ads_raw]) if ads_raw else pd.DataFrame()

if orders_df.empty:
    st.warning("Нет данных. Запустите `python run_etl.py` для загрузки.")
    st.stop()

min_d = orders_df["date"].min()
max_d = orders_df["date"].max()

presets = build_presets(min_d, max_d)

shops_avail = sorted(orders_df["shop"].unique().tolist())
col1, _ = st.columns(2)
with col1:
    selected_shops = st.multiselect(
        "Кабинет", shops_avail, default=shops_avail,
        format_func=lambda x: SHOP_LABELS.get(x, x),
    )

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

            ad_spend = 0.0
            if not ads_df.empty:
                ad = ads_df[(ads_df["shop"] == shop) & (ads_df["date"] == day)]
                ad_spend = ad["spend"].sum() if not ad.empty and "spend" in ad.columns else 0.0

            drr_o = (ad_spend / rev_orders * 100) if rev_orders > 0 else 0.0
            drr_a = (ad_spend / rev_accepted * 100) if rev_accepted > 0 else 0.0

            rows.append({
                "shop": shop, "date": day, "label": day.strftime("%d.%m"),
                "rev_orders": rev_orders, "units_orders": units_orders,
                "rev_accepted": rev_accepted, "units_accepted": units_accepted,
                "ad_spend": ad_spend, "drr_orders": drr_o, "drr_accepted": drr_a,
            })
    return pd.DataFrame(rows)


METRICS = {
    "rev_orders": "Выручка по заказам",
    "rev_accepted": "Выручка по принятым",
    "ad_spend": "Продвижение и реклама",
    "drr_orders": "ДРР по заказам",
    "drr_accepted": "ДРР по принятым",
}

METRIC_HELP = {
    "rev_orders": "Выручка по всем заказам из аналитики Ozon (включая ещё не доставленные).",
    "rev_accepted": "Выручка по принятым заказам — без отмен (доставлен + в пути).",
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

metric = st.radio("Метрика", list(METRICS.keys()), format_func=lambda m: METRICS[m], horizontal=True)

st.subheader(f"{METRICS[metric]}")
st.caption(METRIC_HELP.get(metric, ""))

fig = go.Figure()

for shop in selected_shops:
    shop_data = daily_df[daily_df["shop"] == shop].sort_values("date")
    if not shop_data.empty:
        fig.add_trace(go.Scatter(
            x=shop_data["date"], y=shop_data[metric],
            mode="lines+markers", name=f"{SHOP_LABELS.get(shop, shop)} · текущий период",
            line=dict(color=SHOP_COLORS.get(shop, "#999"), width=2),
            marker=dict(size=4),
            hovertemplate="%{x|%d.%m.%Y}<br>%{y:,.0f}<extra>%{fullData.name}</extra>",
        ))

    prev_shop_data = prev_daily_df[prev_daily_df["shop"] == shop].sort_values("date")
    if not prev_shop_data.empty:
        fig.add_trace(go.Scatter(
            x=prev_shop_data["date"].map(lambda d: d + timedelta(days=period_len)),
            y=prev_shop_data[metric],
            mode="lines+markers", name=f"{SHOP_LABELS.get(shop, shop)} · прошлый период",
            line=dict(color=SHOP_COLORS.get(shop, "#999"), width=2, dash="dash"),
            marker=dict(size=4),
            text=prev_shop_data["date"].map(lambda d: d.strftime("%d.%m.%Y")),
            hovertemplate="%{text}<br>%{y:,.0f}<extra>%{fullData.name}</extra>",
        ))

is_pct = metric.startswith("drr_")
fig.update_layout(
    xaxis_title=None, yaxis_title="%" if is_pct else "₽",
    hovermode="x unified", height=400,
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    margin=dict(t=10, b=10, l=10, r=10),
)
fig.update_xaxes(tickformat="%d.%m")
st.plotly_chart(fig, width="stretch")

if metric in ("ad_spend", "drr_orders", "drr_accepted") and daily_df["ad_spend"].sum() == 0:
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
    cur_ad = sd["ad_spend"].sum()
    cur_drr_o = cur_ad / cur_rev_o * 100 if cur_rev_o > 0 else 0
    cur_drr_a = cur_ad / cur_rev_a * 100 if cur_rev_a > 0 else 0

    prev_rev_o = prev["rev_orders"].sum()
    prev_u_o = int(prev["units_orders"].sum())
    prev_rev_a = prev["rev_accepted"].sum()
    prev_u_a = int(prev["units_accepted"].sum())
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
