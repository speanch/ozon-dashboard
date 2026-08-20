"""Аналитика по SKU (артикулу offer_id) — продажи, реклама, кампании и цены."""
import json
import os
import sys
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.models import (
    init_db, get_session, Order, AdDailyStats, ProductPrice, ProductMapping,
)
from etl.config import SHOP_LABELS
from etl.shared_ui import build_presets, date_filter_section, render_refresh_button

st.title("Аналитика по артикулу")

render_refresh_button()

engine = init_db()
session = get_session(engine)
orders_raw = session.query(Order).all()
ads_raw = session.query(AdDailyStats).all()
prices_raw = session.query(ProductPrice).all()
mapping_raw = session.query(ProductMapping).all()
session.close()
engine.dispose()

# ── продажи из заказов (ключ — offer_id) ──────────────────────────────────

sales_rows = []
for o in orders_raw:
    try:
        prods = json.loads(o.ozon_costs_data) if o.ozon_costs_data else []
    except (json.JSONDecodeError, TypeError):
        continue
    for p in prods:
        sales_rows.append({
            "shop": o.marketplace,
            "offer_id": p.get("offer_id"),
            "name": p.get("name", ""),
            "order_date": o.order_date,
            "status": o.marketplace_status,
            "quantity": p.get("quantity", 1) or 1,
            "price": p.get("price", 0) or 0,
            "old_price": p.get("old_price", 0) or 0,
            "discount": p.get("discount_value", 0) or 0,
            "commission": p.get("commission_amount", 0) or 0,
            "payout": p.get("payout", 0) or 0,
        })
sales_df = pd.DataFrame(sales_rows)
if sales_df.empty:
    sales_df = pd.DataFrame(columns=[
        "shop", "offer_id", "name", "order_date", "status", "quantity",
        "price", "old_price", "discount", "commission", "payout",
    ])

# ── мост sku → offer_id ────────────────────────────────────────────────────

sku_to_offer = {}
offer_to_name = {}
for m in mapping_raw:
    if m.sku and m.offer_id:
        sku_to_offer[m.sku] = m.offer_id
        offer_to_name[m.offer_id] = m.name or ""
for _, r in sales_df.iterrows():
    if r["offer_id"] and r["offer_id"] not in offer_to_name and r["name"]:
        offer_to_name[r["offer_id"]] = r["name"]

# ── реклама (числовой sku → offer_id) ──────────────────────────────────────

ads_df = pd.DataFrame([{
    "shop": a.shop, "date": a.date, "campaign_name": a.campaign_name,
    "sku": a.sku, "impressions": a.impressions, "clicks": a.clicks,
    "ctr": a.ctr, "cart_adds": a.cart_adds, "spend": a.spend,
    "units_sold": a.units_sold, "total_order_amount": a.total_order_amount,
} for a in ads_raw])
if ads_df.empty:
    ads_df = pd.DataFrame(columns=[
        "shop", "date", "campaign_name", "sku", "impressions", "clicks",
        "ctr", "cart_adds", "spend", "units_sold", "total_order_amount",
    ])
ads_df["offer_id"] = ads_df["sku"].map(sku_to_offer)
ads_df = ads_df[ads_df["offer_id"].notna() & (ads_df["offer_id"] != "")]

# ── цены ───────────────────────────────────────────────────────────────────

prices_df = pd.DataFrame([{
    "shop": p.shop, "snapshot_date": p.snapshot_date, "offer_id": p.offer_id,
    "price": p.price, "old_price": p.old_price, "min_price": p.min_price,
    "price_index": p.price_index, "color_index": p.color_index,
    "commission_fbo": p.commission_fbo, "commission_fbs": p.commission_fbs,
} for p in prices_raw])
if prices_df.empty:
    prices_df = pd.DataFrame(columns=[
        "shop", "snapshot_date", "offer_id", "price", "old_price",
        "min_price", "price_index", "color_index", "commission_fbo", "commission_fbs",
    ])

if sales_df.empty and ads_df.empty and prices_df.empty:
    st.warning("Нет данных. Запустите `python run_etl.py` для загрузки.")
    st.stop()

# ── фильтры: кабинет + период ─────────────────────────────────────────────

shops_avail = sorted(set(sales_df["shop"]).union(ads_df["shop"]).union(prices_df["shop"]))
selected_shops = st.multiselect(
    "Кабинет", shops_avail, default=shops_avail,
    format_func=lambda x: SHOP_LABELS.get(x, x),
)

sales_sel = sales_df[sales_df["shop"].isin(selected_shops)]
ads_sel = ads_df[ads_df["shop"].isin(selected_shops)]

if not sales_sel.empty:
    min_d = sales_sel["order_date"].min().date()
    max_d = sales_sel["order_date"].max().date()
elif not ads_sel.empty:
    min_d = ads_sel["date"].min()
    max_d = ads_sel["date"].max()
else:
    min_d = max_d = datetime.now().date()

presets = build_presets(min_d, max_d)
date_from, date_to = date_filter_section(presets, min_d, max_d, prefix="sku_")

tab1, tab2 = st.tabs(["Продажи и реклама", "Цены и ценовой индекс"])

# ── Tab 1 ──────────────────────────────────────────────────────────────────

with tab1:
    sales_f = sales_sel[
        (sales_sel["order_date"].dt.date >= date_from)
        & (sales_sel["order_date"].dt.date <= date_to)
    ]
    ads_f = ads_sel[
        (ads_sel["date"] >= date_from) & (ads_sel["date"] <= date_to)
    ]

    offers = sorted(set(sales_f["offer_id"]).union(ads_f["offer_id"]))
    offers = [o for o in offers if o]

    if not offers:
        st.info("Нет артикулов с данными за выбранный период.")
    else:
        selected_offer = st.selectbox(
            "Артикул (offer_id)", offers,
            format_func=lambda o: f"{o} — {offer_to_name.get(o, '')[:80]}",
        )

        a = sales_f[sales_f["offer_id"] == selected_offer]
        ad = ads_f[ads_f["offer_id"] == selected_offer]

        revenue = (a["price"] * a["quantity"]).sum()
        units = int(a["quantity"].sum())
        orders = len(a)
        commission = abs(a["commission"]).sum()
        payout = a["payout"].sum()
        discount = a["discount"].sum()
        avg_price = revenue / units if units > 0 else 0

        spend = ad["spend"].sum()
        ad_revenue = ad["total_order_amount"].sum()
        impressions = int(ad["impressions"].sum())
        clicks = int(ad["clicks"].sum())
        cart_adds = int(ad["cart_adds"].sum())
        acos = (spend / ad_revenue * 100) if ad_revenue > 0 else 0
        drr = (spend / revenue * 100) if revenue > 0 else 0
        ctr = (clicks / impressions * 100) if impressions > 0 else 0
        n_campaigns = ad["campaign_name"].nunique()

        st.subheader(offer_to_name.get(selected_offer, selected_offer))

        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric("Выручка", f"{revenue:,.0f} ₽")
        k2.metric("Продано", f"{units} шт")
        k3.metric("Заказов", orders)
        k4.metric("Средняя цена", f"{avg_price:,.0f} ₽")
        k5.metric("Комиссия", f"{commission:,.0f} ₽")
        k6.metric("К выплате", f"{payout:,.0f} ₽")

        has_ads = not ad.empty

        k7, k8, k9, k10 = st.columns(4)
        k7.metric("Скидки", f"{discount:,.0f} ₽")
        k8.metric(
            "Расход на рекламу", f"{spend:,.0f} ₽" if has_ads else "—",
            help="Сколько потрачено на рекламу этого артикула за период.",
        )
        k9.metric(
            "ACOS", f"{acos:.1f}%" if has_ads else "—",
            help="Доля рекламных расходов от выручки с рекламы. Чем ниже — тем эффективнее.",
        )
        k10.metric(
            "Рекламных кампаний", n_campaigns if has_ads else "—",
            help="Сколько рекламных кампаний продвигали артикул.",
        )

        k11, k12, k13, k14 = st.columns(4)
        k11.metric(
            "Показы рекламы", f"{impressions:,}" if has_ads else "—",
            help="Сколько раз объявление показали покупателям.",
        )
        k12.metric(
            "Клики по рекламе", f"{clicks:,}" if has_ads else "—",
            help="Сколько раз покупатели кликнули по объявлению.",
        )
        k13.metric(
            "Добавлений в корзину", f"{cart_adds:,}" if has_ads else "—",
            help="Сколько раз товар добавили в корзину после клика по рекламе.",
        )
        k14.metric(
            "Выручка с рекламы", f"{ad_revenue:,.0f} ₽" if has_ads else "—",
            help="Выручка от заказов, сделанных после клика по рекламе.",
        )

        if has_ads:
            st.caption(
                f"ДРР (расход на рекламу ÷ выручка) = {drr:.1f}% · "
                f"CTR (клики ÷ показы) = {ctr:.2f}%"
            )
        else:
            st.info(
                "По этому артикулу нет данных рекламы за выбранный период — "
                "реклама выгружается из Performance API, в базе данные только до 09.07.2026. "
                "Обновите рекламу: `python run_etl.py`."
            )

        c1, c2 = st.columns(2)
        with c1:
            if not a.empty:
                daily = a.groupby(a["order_date"].dt.date).agg(
                    units=("quantity", "sum"), revenue=("price", "sum")
                ).reset_index()
                daily.columns = ["date", "units", "revenue"]
                fig = px.bar(daily, x="date", y="units", title="Продажи по дням (шт)")
                fig.update_layout(xaxis_title=None, height=300)
                st.plotly_chart(fig, width="stretch")
        with c2:
            if not a.empty:
                fig2 = px.area(daily, x="date", y="revenue", title="Выручка по дням (₽)")
                fig2.update_layout(xaxis_title=None, height=300)
                st.plotly_chart(fig2, width="stretch")

        if not ad.empty:
            daily_ad = ad.groupby("date").agg(
                spend=("spend", "sum"), clicks=("clicks", "sum")
            ).reset_index()
            fig3 = px.bar(
                daily_ad, x="date", y=["spend", "clicks"], barmode="group",
                title="Расход на рекламу и клики по дням",
            )
            fig3.update_layout(xaxis_title=None, height=300, legend=dict(orientation="h"))
            st.plotly_chart(fig3, width="stretch")

            st.write(f"**Кампании за период {date_from.strftime('%d.%m.%Y')} – {date_to.strftime('%d.%m.%Y')}**")
            camps = (
                ad.groupby("campaign_name")
                .agg(
                    spend=("spend", "sum"), impressions=("impressions", "sum"),
                    clicks=("clicks", "sum"), units_sold=("units_sold", "sum"),
                    revenue=("total_order_amount", "sum"),
                )
                .sort_values("spend", ascending=False)
                .reset_index()
            )
            camps["acos"] = camps.apply(
                lambda r: r["spend"] / r["revenue"] * 100 if r["revenue"] > 0 else 0,
                axis=1,
            )
            camps.columns = ["Кампания", "Расход", "Показы", "Клики", "Продано", "Выручка", "ACOS %"]
            st.dataframe(
                camps, width="stretch", hide_index=True,
                column_config={
                    "Расход": st.column_config.NumberColumn(format="₽ %,.0f"),
                    "Выручка": st.column_config.NumberColumn(format="₽ %,.0f"),
                    "ACOS %": st.column_config.NumberColumn(format="%.2f %%"),
                },
            )
        else:
            st.caption("Нет данных рекламы по этому артикулу за период.")

    st.divider()

    st.subheader("Топ артикулов по выручке за период")
    if not sales_f.empty:
        top = (
            sales_f.groupby(["offer_id", "name"])
            .agg(revenue=("price", "sum"), units=("quantity", "sum"))
            .sort_values("revenue", ascending=False)
            .head(20)
            .reset_index()
        )
        top.columns = ["Артикул", "Товар", "Выручка", "Продано"]
        st.dataframe(
            top, width="stretch", hide_index=True,
            column_config={"Выручка": st.column_config.NumberColumn(format="₽ %,.0f")},
        )

# ── Tab 2: цены и ценовой индекс ───────────────────────────────────────────

with tab2:
    if prices_df.empty:
        st.info("Нет снимков цен. Запустите `python run_etl.py`.")
    else:
        pr = prices_df[prices_df["shop"].isin(selected_shops)]
        if not pr.empty:
            snap = pr["snapshot_date"].max()
            st.caption(f"Снимок цен от {snap.strftime('%d.%m.%Y')}")
            pr = pr[pr["snapshot_date"] == snap]

        search = st.text_input("Поиск по артикулу", "")
        if search:
            pr = pr[pr["offer_id"].str.contains(search, case=False, na=False)]

        pr["name"] = pr["offer_id"].map(offer_to_name).fillna("")
        pr["discount_pct"] = pr.apply(
            lambda r: (1 - r["price"] / r["old_price"]) * 100 if r["old_price"] > 0 else 0,
            axis=1,
        )

        view = pr[[
            "offer_id", "name", "price", "old_price", "min_price",
            "discount_pct", "price_index", "color_index",
            "commission_fbo", "commission_fbs",
        ]].sort_values("price", ascending=False)
        view.columns = [
            "Артикул", "Товар", "Цена", "Цена без скидки", "Мин. цена",
            "Скидка %", "Ценовой индекс", "Индекс", "Комиссия FBO %", "Комиссия FBS %",
        ]
        st.dataframe(
            view, width="stretch", hide_index=True,
            column_config={
                "Цена": st.column_config.NumberColumn(format="₽ %,.0f"),
                "Цена без скидки": st.column_config.NumberColumn(format="₽ %,.0f"),
                "Мин. цена": st.column_config.NumberColumn(format="₽ %,.0f"),
                "Скидка %": st.column_config.NumberColumn(format="%.1f %%"),
                "Ценовой индекс": st.column_config.NumberColumn(format="%.2f"),
                "Комиссия FBO %": st.column_config.NumberColumn(format="%.1f %%"),
                "Комиссия FBS %": st.column_config.NumberColumn(format="%.1f %%"),
            },
        )

        if not pr.empty:
            idx = pr[pr["price_index"] > 0]
            if not idx.empty:
                fig_idx = px.histogram(
                    idx, x="price_index", nbins=30,
                    title="Распределение ценового индекса",
                    labels={"price_index": "Ценовой индекс"},
                )
                fig_idx.update_layout(height=300)
                st.plotly_chart(fig_idx, width="stretch")
