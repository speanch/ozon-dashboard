"""Дашборд Ozon — метрики магазина из SQLite."""
import json
import os
import sys
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.models import init_db, get_session, Order, DailyMetric, AdDailyStats, FinanceBalance
from etl.config import SHOP_LABELS, SHOP_COLORS, STATUS_LABELS
from etl.shared_ui import build_presets, date_filter_section_with_shops

st.title("Обзор Ozon")

from etl.shared_ui import render_refresh_button
render_refresh_button()

# ── загрузка данных ──────────────────────────────────────────────────────

engine = init_db()
session = get_session(engine)
orders_raw = session.query(Order).all()
analytics_raw = session.query(DailyMetric).all()
ads_raw = session.query(AdDailyStats).all()
balance_raw = session.query(FinanceBalance).all()
session.close()
engine.dispose()

# ── последний баланс по каждому кабинету ─────────────────────────────────

balances: dict[str, float] = {}
balance_dates: dict[str, date] = {}
for b in balance_raw:
    if b.shop not in balance_dates or b.snapshot_date >= balance_dates[b.shop]:
        balance_dates[b.shop] = b.snapshot_date
        balances[b.shop] = b.balance

# ── построение датафреймов ──────────────────────────────────────────────

analytics_df = pd.DataFrame([{
    "shop": a.shop, "date": a.date, "sku": a.sku,
    "product_name": a.product_name or "",
    "revenue": a.revenue, "ordered_units": a.ordered_units,
    "returns": a.returns, "cancellations": a.cancellations,
} for a in analytics_raw])

ads_df = pd.DataFrame([{
    "shop": a.shop, "date": a.date,
    "campaign_id": a.campaign_id,
    "campaign_name": a.campaign_name, "sku": a.sku,
    "impressions": a.impressions, "clicks": a.clicks,
    "ctr": a.ctr, "cart_adds": a.cart_adds, "spend": a.spend,
    "units_sold": a.units_sold, "promo_revenue": a.promo_revenue,
    "total_order_amount": a.total_order_amount,
    "promo_acos": a.promo_acos, "overall_acos": a.overall_acos,
} for a in ads_raw])

# ── построение заказов ──────────────────────────────────────────────────

rows = []
all_products_raw = []

for o in orders_raw:
    try:
        products = json.loads(o.ozon_costs_data) if o.ozon_costs_data else []
    except json.JSONDecodeError:
        products = []

    quantities = [p.get("quantity", 1) or 1 for p in products]
    prices = [p.get("price", 0) or 0 for p in products]
    old_prices = [p.get("old_price", 0) or 0 for p in products]
    discounts = [p.get("discount_value", 0) or 0 for p in products]
    commissions = [p.get("commission_amount", 0) or 0 for p in products]

    rows.append({
        "posting_number": o.name,
        "marketplace": o.marketplace,
        "status": o.marketplace_status or "—",
        "order_date": o.order_date,
        "customer": o.customer_name or "",
        "phone": o.phone or "",
        "delivery_address": o.delivery_address or "",
        "distance_mkad": o.distance_mkad or 0,
        "elevator": o.elevator or "",
        "floor": o.floor or "",
        "has_assembly": o.has_assembly or "",
        "products_count": len(products),
        "ozon_price_sum": sum(p.get("price", 0) * p.get("quantity", 1) for p in products),
        "ozon_old_price_sum": sum(p.get("old_price", 0) * p.get("quantity", 1) for p in products),
        "ozon_discount_sum": sum(p.get("discount_value", 0) * p.get("quantity", 1) for p in products),
        "ozon_commission_sum": sum(abs(p.get("commission_amount", 0)) for p in products),
        "products_json": products,
    })

    for i, p in enumerate(products):
        if o.marketplace_status in ("delivered", "delivering"):
            all_products_raw.append({
                "name": p.get("name") or p.get("offer_id", "—"),
                "offer_id": p.get("offer_id"),
                "quantity": p.get("quantity", 1) or 1,
                "price": p.get("price", 0) or 0,
                "commission": p.get("commission_amount", 0) or 0,
                "discount": p.get("discount_value", 0) or 0,
                "order_date": o.order_date,
                "marketplace": o.marketplace,
            })

df = pd.DataFrame(rows)

if df.empty:
    st.warning("Нет данных. Запустите `python run_etl.py` для загрузки.")
    st.stop()

# ── фильтры ───────────────────────────────────────────────────────────────

min_d = df["order_date"].min().date()
max_d = df["order_date"].max().date()

presets = build_presets(min_d, max_d)

marketplaces = sorted(df["marketplace"].unique().tolist())
statuses = sorted(df["status"].unique().tolist())
active = {"awaiting_packaging", "awaiting_deliver", "delivering", "delivered"}
default_statuses = [s for s in statuses if s in active]

selected_mp, selected_status, date_from, date_to = date_filter_section_with_shops(
    presets, min_d, max_d, marketplaces, SHOP_LABELS,
    status_options=statuses, default_statuses=default_statuses,
    status_labels=STATUS_LABELS,
)

mask = (
    df["marketplace"].isin(selected_mp)
    & df["status"].isin(selected_status)
    & (df["order_date"].dt.date >= date_from)
    & (df["order_date"].dt.date <= date_to)
)
filtered = df[mask]

st.caption(
    f"Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')}  ·  "
    f"Показано: {len(filtered)} заказов из {len(df)}"
)

# ── 1. Ключевые показатели ────────────────────────────────────────────────

st.subheader("Ключевые показатели")

total_orders = len(filtered)
total_revenue = filtered["ozon_price_sum"].sum()

k1, k2 = st.columns(2)
k1.metric("Заказов", total_orders, help="Количество заказов за выбранный период.")
k2.metric("Выручка", f"{total_revenue:,.0f} ₽", help="Сумма продаж (цена × количество).")

for mp in selected_mp:
    mp_data = filtered[filtered["marketplace"] == mp]
    mp_orders = len(mp_data)
    mp_revenue = mp_data["ozon_price_sum"].sum()
    mp_avg = mp_revenue / mp_orders if mp_orders > 0 else 0
    mp_balance = balances.get(mp)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"{SHOP_LABELS.get(mp, mp)} · заказов", mp_orders)
    c2.metric(f"{SHOP_LABELS.get(mp, mp)} · выручка", f"{mp_revenue:,.0f} ₽")
    c3.metric(
        f"{SHOP_LABELS.get(mp, mp)} · средний чек", f"{mp_avg:,.0f} ₽",
        help="Выручка ÷ количество заказов — средняя сумма одного заказа.",
    )
    c4.metric(
        f"{SHOP_LABELS.get(mp, mp)} · баланс",
        f"{mp_balance:,.0f} ₽" if mp_balance is not None else "н/д",
        help="Текущий баланс кабинета Ozon (начисления минус списания).",
    )

st.divider()

# ── 2. Рейтинг ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_ratings() -> dict[str, float]:
    """Возвращает {shop_key: rating_value} для каждого магазина."""
    from dotenv import load_dotenv; load_dotenv()
    from etl.ozon_client import OzonClient
    ratings = {}
    for shop in ["ozon_stylint", "ozon_rs"]:
        try:
            client = OzonClient(shop)
            r = client.get_seller_ratings()
            ratings[shop] = r.get("Оценка товаров")
        except Exception:
            ratings[shop] = None
    return ratings

seller_ratings = _fetch_ratings()

@st.cache_data(ttl=3600, show_spinner=False)
def _active_campaigns() -> set[str]:
    from dotenv import load_dotenv; load_dotenv()
    from etl.performance_client import PerformanceClient
    ids = set()
    for shop in ["ozon_stylint", "ozon_rs"]:
        try:
            pc = PerformanceClient(shop)
            ids |= pc.get_active_campaign_ids()
        except Exception:
            pass
    return ids

rk1, rk2 = st.columns(2)
for idx, shop in enumerate(["ozon_stylint", "ozon_rs"]):
    if shop not in selected_mp:
        continue
    rating = seller_ratings.get(shop)
    col = rk1 if idx == 0 else rk2
    if rating is not None:
        col.metric(f"{SHOP_LABELS.get(shop, shop)} · рейтинг", f"{rating:.2f} ★")
    else:
        col.metric(f"{SHOP_LABELS.get(shop, shop)} · рейтинг", "н/д")

st.divider()

# ── 3. Сводка по кабинетам ────────────────────────────────────────────────

st.subheader("Сводка по кабинетам")

by_mp_rows = []
for mp in selected_mp:
    mp_data = filtered[filtered["marketplace"] == mp]
    orders = len(mp_data)
    revenue = mp_data["ozon_price_sum"].sum()
    old_price = mp_data["ozon_old_price_sum"].sum()
    discount = mp_data["ozon_discount_sum"].sum()
    commission = mp_data["ozon_commission_sum"].sum()
    discount_pct = (discount / old_price * 100) if old_price > 0 else 0
    commission_pct = (commission / revenue * 100) if revenue > 0 else 0
    by_mp_rows.append({
        "Кабинет": SHOP_LABELS.get(mp, mp),
        "Заказов": orders,
        "Заработано": revenue,
        "Цена без скидки": old_price,
        "Скидка": discount,
        "Комиссия": commission,
        "Текущий баланс": balances.get(mp),
        "Скидка %": round(discount_pct, 1),
        "Комиссия %": round(commission_pct, 1),
    })

by_mp = pd.DataFrame(by_mp_rows)
st.dataframe(
    by_mp,
    width="stretch", hide_index=True,
    column_config={
        "Заработано": st.column_config.NumberColumn(format="₽ %,.0f", help="Выручка от продаж."),
        "Цена без скидки": st.column_config.NumberColumn(format="₽ %,.0f", help="Сумма цен без учёта скидок."),
        "Скидка": st.column_config.NumberColumn(format="₽ %,.0f", help="Сумма скидок."),
        "Комиссия": st.column_config.NumberColumn(format="₽ %,.0f", help="Комиссия Ozon."),
        "Текущий баланс": st.column_config.NumberColumn(format="₽ %,.0f", help="Баланс кабинета Ozon на сегодня."),
        "Скидка %": st.column_config.NumberColumn(format="%.1f %%", help="Скидка ÷ цена без скидки."),
        "Комиссия %": st.column_config.NumberColumn(format="%.1f %%", help="Комиссия ÷ выручка."),
    },
)

st.divider()

# ── 3. ДРР ──────────────────────────────────────────────────────────────

st.subheader("Реклама и ДРР")
st.caption("ACOS — расход на рекламу ÷ выручка с рекламы. Чем ниже, тем эффективнее.")

if not ads_df.empty:
    ads_mp = ads_df[
        ads_df["shop"].isin([s for s in selected_mp if s in ads_df["shop"].unique()])
        & (ads_df["date"] >= date_from)
        & (ads_df["date"] <= date_to)
    ]
    if ads_mp.empty:
        ads_min = ads_df["date"].min()
        ads_max = ads_df["date"].max()
        st.info(
            f"Нет данных рекламы за выбранный период. "
            f"В базе есть данные за {ads_min:%d.%m.%Y} – {ads_max:%d.%m.%Y}. "
            f"Обновите рекламу: `python run_etl.py`."
        )
    else:
        total_spend = ads_mp["spend"].sum()
        total_ad_revenue = ads_mp["total_order_amount"].sum()
        overall_acos = (total_spend / total_ad_revenue * 100) if total_ad_revenue > 0 else 0

        drr_col1, drr_col2, drr_col3, drr_col4, drr_col5 = st.columns(5)
        drr_col1.metric("Расход на рекламу", f"{total_spend:,.0f} ₽",
                        help="Сколько потрачено на рекламу за период.")
        drr_col2.metric("Выручка с рекламы", f"{total_ad_revenue:,.0f} ₽",
                        help="Выручка от заказов, сделанных после клика по рекламе.")
        drr_col3.metric("ACOS", f"{overall_acos:.2f}%",
                        help="Расход на рекламу ÷ выручка с рекламы.")
        drr_col4.metric("Показы / Клики",
                        f"{ads_mp['impressions'].sum():,.0f} / {ads_mp['clicks'].sum():,.0f}",
                        help="Сколько раз показали объявления / сколько раз кликнули.")
        drr_col5.metric("Добавлений в корзину", f"{ads_mp['cart_adds'].sum():,.0f}",
                        help="Сколько раз товар добавили в корзину после клика по рекламе.")

        drr_ch1, drr_ch2 = st.columns(2)
        with drr_ch1:
            daily_ads = ads_mp.groupby("date").agg(
                spend=("spend", "sum"), revenue=("total_order_amount", "sum")
            ).reset_index()
            if not daily_ads.empty:
                daily_ads["acos"] = daily_ads.apply(
                    lambda r: r["spend"] / r["revenue"] * 100 if r["revenue"] > 0 else 0, axis=1
                )
                fig_ads = px.bar(
                    daily_ads, x="date", y=["spend", "revenue"],
                    barmode="group", title="Расходы и выручка с рекламы"
                )
                fig_ads.update_layout(xaxis_title=None, height=300, legend=dict(orientation="h"))
                st.plotly_chart(fig_ads, width="stretch")

        with drr_ch2:
            st.write("**Активные кампании**")

            active_ids = _active_campaigns()
            if active_ids:
                top_camp = (
                    ads_mp[ads_mp["campaign_id"].isin(active_ids)]
                    .groupby("campaign_name")
                    .agg(spend=("spend", "sum"), revenue=("total_order_amount", "sum"))
                    .sort_values("spend", ascending=False)
                    .reset_index()
                )
            else:
                st.caption("Не удалось получить список активных кампаний — показаны все")
                top_camp = (
                    ads_mp.groupby("campaign_name")
                    .agg(spend=("spend", "sum"), revenue=("total_order_amount", "sum"))
                    .sort_values("spend", ascending=False)
                    .reset_index()
                )
            top_camp["acos"] = top_camp.apply(
                lambda r: r["spend"] / r["revenue"] * 100 if r["revenue"] > 0 else 0, axis=1
            )
            top_camp.columns = ["Кампания", "Расход", "Выручка", "ДРР %"]
            st.dataframe(
                top_camp,
                width="stretch", hide_index=True,
                column_config={
                    "Расход": st.column_config.NumberColumn(format="₽ %,.0f"),
                    "Выручка": st.column_config.NumberColumn(format="₽ %,.0f"),
                    "ДРР %": st.column_config.NumberColumn(format="%.2f %%"),
                },
            )
else:
    st.info("Для расчёта ДРР нужен Performance API ключ. Как получить: Настройки → API-ключи → Performance API → Добавить ключ.")

st.divider()

# ── 4. Графики ────────────────────────────────────────────────────────────

chart_col1, chart_col2, chart_col3 = st.columns(3)

with chart_col1:
    st.subheader("Статусы заказов")
    status_counts = filtered["status"].value_counts().reset_index()
    status_counts.columns = ["status", "count"]
    if not status_counts.empty:
        status_counts["status_label"] = status_counts["status"].map(STATUS_LABELS).fillna(status_counts["status"])
        fig_status = px.pie(
            status_counts, values="count", names="status_label", hole=0.4,
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig_status.update_traces(textinfo="percent+label")
        st.plotly_chart(fig_status, width="stretch")

with chart_col2:
    st.subheader("Заказы по дням")
    daily = (
        filtered.groupby(filtered["order_date"].dt.date)
        .size()
        .reset_index(name="count")
    )
    daily.columns = ["date", "count"]
    if not daily.empty:
        fig_daily = px.bar(daily, x="date", y="count", text_auto=True)
        fig_daily.update_layout(xaxis_title=None, yaxis_title="Заказов", height=350)
        st.plotly_chart(fig_daily, width="stretch")

with chart_col3:
    st.subheader("Выручка по дням")
    daily_rev = (
        filtered.groupby(filtered["order_date"].dt.date)
        .agg(revenue=("ozon_price_sum", "sum"))
        .reset_index()
    )
    daily_rev.columns = ["date", "revenue"]
    if not daily_rev.empty:
        fig_rev = px.area(
            daily_rev, x="date", y="revenue",
            labels={"revenue": "Выручка (₽)"}
        )
        fig_rev.update_layout(xaxis_title=None, yaxis_title="Выручка (₽)", height=350)
        st.plotly_chart(fig_rev, width="stretch")

st.divider()

# ── 5. Топ-5 товаров ──────────────────────────────────────────────────────

st.subheader("Топ-5 товаров по количеству продаж")

if all_products_raw:
    prod_df = pd.DataFrame(all_products_raw)
    prod_df["ord_date"] = prod_df["order_date"].dt.date

    # filtered by date
    prod_mask = (
        prod_df["marketplace"].isin(selected_mp)
        & (prod_df["ord_date"] >= date_from)
        & (prod_df["ord_date"] <= date_to)
    )
    prod_filtered = prod_df[prod_mask]

    # all time
    prod_all = prod_df[prod_df["marketplace"].isin(selected_mp)]

    col5a, col5b = st.columns(2)

    with col5a:
        st.write("**За период**")
        top_p = (
            prod_filtered.groupby(["name", "offer_id"])
            .agg(quantity=("quantity", "sum"), revenue=("price", "sum"))
            .sort_values("quantity", ascending=False)
            .head(5)
            .reset_index()
        )
        if not top_p.empty:
            top_p.columns = ["Товар", "Артикул", "Продано", "Выручка"]
            st.dataframe(
                top_p,
                width="stretch", hide_index=True,
                column_config={
                    "Выручка": st.column_config.NumberColumn(format="₽ %,.0f"),
                },
            )
        else:
            st.info("Нет данных за выбранный период")

    with col5b:
        st.write("**За всё время**")
        top_all = (
            prod_all.groupby(["name", "offer_id"])
            .agg(quantity=("quantity", "sum"), revenue=("price", "sum"))
            .sort_values("quantity", ascending=False)
            .head(5)
            .reset_index()
        )
        if not top_all.empty:
            top_all.columns = ["Товар", "Артикул", "Продано", "Выручка"]
            st.dataframe(
                top_all,
                width="stretch", hide_index=True,
                column_config={
                    "Выручка": st.column_config.NumberColumn(format="₽ %,.0f"),
                },
            )
        else:
            st.info("Нет данных")

st.divider()

# ── 8. Детализация ─────────────────────────────────────────────────────────

tab1, tab2 = st.tabs(["Финансы по заказам", "Все заказы"])

with tab1:
    fin_cols = [
        "posting_number", "order_date", "marketplace", "status",
        "ozon_price_sum", "ozon_old_price_sum", "ozon_discount_sum",
        "ozon_commission_sum",
    ]
    fin_raw = filtered[fin_cols].copy()
    fin_raw["status"] = fin_raw["status"].map(STATUS_LABELS).fillna(fin_raw["status"])
    fin_df = (
        fin_raw.sort_values("order_date", ascending=False)
        .rename(columns={
            "posting_number": "Отправление",
            "order_date": "Дата",
            "marketplace": "Кабинет",
            "status": "Статус",
            "ozon_price_sum": "Ваша цена",
            "ozon_old_price_sum": "Цена без скидки",
            "ozon_discount_sum": "Скидка",
            "ozon_commission_sum": "Комиссия",
        })
    )
    st.dataframe(
        fin_df,
        width="stretch", hide_index=True,
        column_config={
            "Ваша цена": st.column_config.NumberColumn(format="₽ %,.0f"),
            "Цена без скидки": st.column_config.NumberColumn(format="₽ %,.0f"),
            "Скидка": st.column_config.NumberColumn(format="₽ %,.0f"),
            "Комиссия": st.column_config.NumberColumn(format="₽ %,.0f"),
            "Дата": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
        },
    )

with tab2:
    detail_cols = [
        "posting_number", "order_date", "marketplace", "status",
        "customer", "delivery_address", "distance_mkad",
        "elevator", "floor", "products_count",
    ]
    detail_raw = filtered[detail_cols].copy()
    detail_raw["status"] = detail_raw["status"].map(STATUS_LABELS).fillna(detail_raw["status"])
    detail_df = detail_raw.sort_values("order_date", ascending=False)
    detail_df.columns = [
        "Отправление", "Дата", "Кабинет", "Статус",
        "Клиент", "Адрес", "МКАД (км)",
        "Лифт", "Этаж", "Товаров",
    ]
    st.dataframe(
        detail_df,
        width="stretch", hide_index=True,
        column_config={
            "Дата": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm"),
            "МКАД (км)": st.column_config.NumberColumn(format="%.0f"),
        },
    )
