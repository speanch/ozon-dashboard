"""Стратегия рекламы: какие кампании запускать в следующем месяце и почему."""
import os
import sys
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from etl.models import init_db, get_session, AdDailyStats
from etl.config import SHOP_LABELS, order_shops
from etl.shared_ui import render_refresh_button

st.title("Стратегия рекламы")

MONTHS_RU = ["январь", "февраль", "март", "апрель", "май", "июнь",
             "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]

MIN_SPEND = 5000      # ₽ — ниже этой суммы вердикт не выносится
ROAS_LAUNCH = 3.0     # ROAS ≥ 3 → «Запустить»
DRR_MAX = 15.0        # % — жёсткий лимит: столько ДРР заложено в себестоимость каждого товара

VERDICT_ORDER = {"Запустить": 0, "Тест": 1, "Не запускать": 2, "Мало данных": 3}
VERDICT_COLORS = {"Запустить": "#00C853", "Тест": "#FFB300",
                  "Не запускать": "#D50000", "Мало данных": "#9E9E9E"}

render_refresh_button()

# ── данные ────────────────────────────────────────────────────────────────

engine = init_db()
session = get_session(engine)
ads_raw = session.query(AdDailyStats).all()
session.close()
engine.dispose()

ads_df = pd.DataFrame([{
    "shop": a.shop, "date": a.date,
    "campaign_id": (a.campaign_id or "").strip(),
    "campaign_name": a.campaign_name or "",
    "spend": a.spend or 0.0,
    "revenue": a.promo_revenue or 0.0,
    "orders_amt": a.total_order_amount or 0.0,
} for a in ads_raw])
ads_df = ads_df[(ads_df["campaign_id"] != "cpo") & (ads_df["campaign_id"] != "")]

if ads_df.empty:
    st.warning("Нет данных рекламы. Запустите `python run_etl.py`.")
    st.stop()

dmin, dmax = ads_df["date"].min(), ads_df["date"].max()
today = date.today()
month_title = f"{MONTHS_RU[today.month - 1]} {today.year}"

st.caption(
    f"План на {month_title} по статистике всех кампаний с {dmin:%d.%m.%Y} по {dmax:%d.%m.%Y}. "
    "Кампании «Оплата за заказ» (CPO) не включены."
)

with st.expander("Методика: как считается эффективность"):
    st.markdown(
        f"""
- **ROAS** — выручка, атрибутированная рекламе, ÷ расход на кампанию.
- **ДРР по заказам** — расход ÷ выручка по всем заказам после кликов (включая кросс-продажи).

**Жёсткий лимит: ДРР ≤ {DRR_MAX:.0f}%.** В полную себестоимость каждого товара уже
заложено {DRR_MAX:.0f}% на рекламу, поэтому выходить за этот порог нельзя —
кампания работает в минус.

**Вердикты:**
- **Запустить** — ROAS ≥ {ROAS_LAUNCH:.0f} и ДРР ≤ {DRR_MAX:.0f}%: каждый рубль рекламы приносил
  ≥ {ROAS_LAUNCH:.0f} ₽ выручки без выхода за лимит.
- **Тест** — ДРР ≤ {DRR_MAX:.0f}% и ROAS < {ROAS_LAUNCH:.0f}: окупаемость под вопросом,
  запускать с ограниченным бюджетом.
- **Не запускать** — ДРР > {DRR_MAX:.0f}% или заказов нет: дороже заложенного лимита.
- **Мало данных** — расход < {MIN_SPEND:,.0f} ₽, статистики недостаточно.

Рекомендуемый недельный бюджет = средний расход кампании в неделю за время её
работы — уровень, на котором её ROAS уже доказан (в Озоне бюджет кампании
вводится на неделю). Атрибуция Ozon запаздывает на 1–3 недели, поэтому
ROAS недавно остановленных кампаний — нижняя оценка.
"""
    )


# ── расчёт вердиктов по кампаниям ─────────────────────────────────────────

def build_campaigns(sub: pd.DataFrame) -> pd.DataFrame:
    act = sub[sub["spend"] > 0]
    g = (act.groupby("campaign_id")
         .agg(campaign_name=("campaign_name", "first"),
              spend=("spend", "sum"),
              days=("date", "nunique"),
              first=("date", "min"), last=("date", "max"))
         .reset_index())
    allg = sub.groupby("campaign_id").agg(
        revenue=("revenue", "sum"), orders=("orders_amt", "sum")).reset_index()
    g = g.merge(allg, on="campaign_id", how="left")

    g["roas"] = g["revenue"] / g["spend"]
    g["drr"] = (g["spend"] / g["orders"] * 100).where(g["orders"] > 0)

    l30 = sub[sub["date"] >= dmax - timedelta(days=30)]
    l30g = (l30[l30["spend"] > 0].groupby("campaign_id")
            .agg(spend30=("spend", "sum"), rev30=("revenue", "sum")).reset_index())
    g = g.merge(l30g, on="campaign_id", how="left")
    g["roas30"] = (g["rev30"] / g["spend30"]).where(g["spend30"] > 0)

    def verdict(r):
        if r["spend"] < MIN_SPEND:
            return "Мало данных"
        if r["orders"] == 0 or r["drr"] > DRR_MAX:
            return "Не запускать"
        if r["roas"] >= ROAS_LAUNCH:
            return "Запустить"
        return "Тест"

    g["verdict"] = g.apply(verdict, axis=1)
    g["daily"] = (g["spend"] / g["days"]).round(-2)
    g["weekly"] = (g["daily"] * 7).round(-2)
    g["works_now"] = g["last"] >= dmax - timedelta(days=3)
    g["_v"] = g["verdict"].map(VERDICT_ORDER)
    return g.sort_values(["_v", "roas", "spend"],
                         ascending=[True, False, False], na_position="last")


shops = order_shops(ads_df["shop"].unique().tolist())
camp_by_shop = {s: build_campaigns(ads_df[ads_df["shop"] == s]) for s in shops}
plan_by_shop = {}
for s, g in camp_by_shop.items():
    launch = g[g["verdict"] == "Запустить"]
    budget_daily = launch["daily"].sum()
    budget_month = budget_daily * 30
    budget_weekly = launch["weekly"].sum()
    exp_rev = (launch["daily"] * 30 * launch["roas"]).sum()
    waste = g.loc[g["verdict"] == "Не запускать", "spend"].sum()
    plan_by_shop[s] = {
        "launch": launch, "test": g[g["verdict"] == "Тест"],
        "budget_daily": budget_daily, "budget_month": budget_month,
        "budget_weekly": budget_weekly,
        "exp_rev": exp_rev, "waste": waste,
        "n_launch": len(launch), "n_test": int((g["verdict"] == "Тест").sum()),
        "n_bad": int((g["verdict"] == "Не запускать").sum()),
    }

# ── сводка плана ──────────────────────────────────────────────────────────

st.subheader(f"Сводка плана на {month_title}")

sum_cols = st.columns(len(shops))
for col, s in zip(sum_cols, shops):
    p = plan_by_shop[s]
    with col:
        st.markdown(f"**{SHOP_LABELS.get(s, s)}**")
        st.metric("Кампаний к запуску", p["n_launch"])
        st.metric("Бюджет на неделю", f"{p['budget_weekly']:,.0f} ₽",
                  help="Сумма недельных бюджетов кампаний, как в Озоне.")
        st.metric("Бюджет на месяц", f"{p['budget_month']:,.0f} ₽",
                  help="Недельный бюджет × ~4,3 недели.")
        st.metric("Ожидаемая выручка с рекламы", f"≈ {p['exp_rev']:,.0f} ₽",
                  help="Прогноз при сохранении исторического ROAS кампаний.")

tot_budget = sum(p["budget_month"] for p in plan_by_shop.values())
tot_weekly = sum(p["budget_weekly"] for p in plan_by_shop.values())
tot_rev = sum(p["exp_rev"] for p in plan_by_shop.values())
tot_waste = sum(p["waste"] for p in plan_by_shop.values())
tot_bad = sum(p["n_bad"] for p in plan_by_shop.values())
if tot_budget > 0:
    st.success(
        f"Итого: **{tot_budget:,.0f} ₽ в месяц** (≈ **{tot_weekly:,.0f} ₽ в неделю**) → "
        f"**≈ {tot_rev:,.0f} ₽** выручки с рекламы (ROAS {tot_rev / tot_budget:.1f}). "
        f"План исключает {tot_bad} неэффективных кампаний, на которых ранее потеряно "
        f"{tot_waste:,.0f} ₽."
    )

st.divider()

# ── детали по кабинетам ──────────────────────────────────────────────────

tabs = st.tabs([SHOP_LABELS.get(s, s) for s in shops])
for tab, s in zip(tabs, shops):
    with tab:
        g = camp_by_shop[s]
        p = plan_by_shop[s]
        sub = ads_df[ads_df["shop"] == s]

        last_spend = sub.loc[sub["spend"] > 0, "date"].max()
        rev_after = sub.loc[sub["date"] > last_spend, "revenue"].sum()
        if last_spend < dmax - timedelta(days=10):
            st.warning(
                f"Расход по кабинету прекратился {last_spend:%d.%m.%Y} — баланс ушёл в минус. "
                f"При этом после остановки реклама продолжала приносить атрибутированную выручку: "
                f"+{rev_after:,.0f} ₽ — кампании были эффективны, остановка не связана с отдачей. "
                f"Задача на {month_title}: пополнить баланс и перезапустить проверенные кампании."
            )
        elif g["works_now"].any():
            active = ", ".join(g.loc[g["works_now"], "campaign_name"].tolist())
            st.success(f"Реклама работает: {active}. Текущие кампании сохраняем и добавляем проверенные из плана.")

        # план запуска
        st.subheader("Что запускаем")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Бюджет на неделю", f"{p['budget_weekly']:,.0f} ₽")
        m2.metric("Бюджет на месяц", f"{p['budget_month']:,.0f} ₽")
        m3.metric("Ожидаемая выручка с рекламы", f"≈ {p['exp_rev']:,.0f} ₽")
        m4.metric("ROAS плана", f"{p['exp_rev'] / p['budget_month']:.1f}" if p["budget_month"] > 0 else "—")

        launch_disp = p["launch"].sort_values("roas", ascending=False)
        st.dataframe(
            launch_disp.rename(columns={
                "campaign_name": "Кампания", "roas": "ROAS", "roas30": "ROAS за 30 дн",
                "drr": "ДРР по заказам, %", "spend": "Расход за историю",
                "revenue": "Выручка с рекламы", "days": "Дней работы",
                "last": "Последний день", "weekly": "Бюджет, ₽/нед",
            })[["Кампания", "ROAS", "ROAS за 30 дн", "ДРР по заказам, %",
                "Расход за историю", "Выручка с рекламы", "Дней работы",
                "Последний день", "Бюджет, ₽/нед"]],
            width="stretch", hide_index=True,
            column_config={
                "ROAS": st.column_config.NumberColumn(format="%.2f"),
                "ROAS за 30 дн": st.column_config.NumberColumn(format="%.2f"),
                "ДРР по заказам, %": st.column_config.NumberColumn(format="%.1f %%"),
                "Расход за историю": st.column_config.NumberColumn(format="₽ %,.0f"),
                "Выручка с рекламы": st.column_config.NumberColumn(format="₽ %,.0f"),
                "Бюджет, ₽/нед": st.column_config.NumberColumn(format="₽ %,.0f"),
            },
        )
        st.caption(
            "Недельный бюджет = средний расход кампании в неделю за время её работы — "
            "уровень, на котором ROAS уже доказан (в Озоне бюджет вводится на неделю)."
        )

        if not p["test"].empty:
            st.markdown(
                f"**Кандидаты на тест** — ДРР в пределах {DRR_MAX:.0f}%, но ROAS < {ROAS_LAUNCH:.0f}: "
                "запускать по 1–2 кампаниям с половинным бюджетом; "
                "ROAS < 2 после двух недель или ДРР выше лимита — отключать."
            )
            test_disp = p["test"].sort_values("roas", ascending=False)
            st.dataframe(
                test_disp.assign(
                    budget=(test_disp["weekly"] * 0.5).round(-2)).rename(columns={
                    "campaign_name": "Кампания", "roas": "ROAS", "roas30": "ROAS за 30 дн",
                    "drr": "ДРР по заказам, %", "spend": "Расход за историю",
                    "revenue": "Выручка с рекламы", "budget": "Бюджет теста, ₽/нед",
                })[["Кампания", "ROAS", "ROAS за 30 дн", "ДРР по заказам, %",
                     "Расход за историю", "Выручка с рекламы", "Бюджет теста, ₽/нед"]],
                width="stretch", hide_index=True,
                column_config={
                    "ROAS": st.column_config.NumberColumn(format="%.2f"),
                    "ROAS за 30 дн": st.column_config.NumberColumn(format="%.2f"),
                    "ДРР по заказам, %": st.column_config.NumberColumn(format="%.1f %%"),
                    "Расход за историю": st.column_config.NumberColumn(format="₽ %,.0f"),
                    "Выручка с рекламы": st.column_config.NumberColumn(format="₽ %,.0f"),
                    "Бюджет теста, ₽/нед": st.column_config.NumberColumn(format="₽ %,.0f"),
                },
            )

        if p["n_bad"] > 0:
            st.info(
                f"Не запускать: **{p['n_bad']} кампаний** — на них ранее потрачено "
                f"**{p['waste']:,.0f} ₽** при ДРР выше {DRR_MAX:.0f}% или без заказов. "
                "Список см. в таблице вердиктов ниже."
            )

        # график ROAS
        st.subheader("ROAS по кампаниям")
        cdf = g[g["spend"] >= MIN_SPEND].sort_values("roas")
        if not cdf.empty:
            fig = go.Figure(go.Bar(
                y=cdf["campaign_name"], x=cdf["roas"], orientation="h",
                marker_color=cdf["verdict"].map(VERDICT_COLORS),
                customdata=cdf[["verdict", "spend", "revenue", "weekly"]],
                hovertemplate="%{y}<br>ROAS: %{x:.2f}<br>Вердикт: %{customdata[0]}"
                              "<br>Расход: %{customdata[1]:,.0f} ₽"
                              "<br>Выручка: %{customdata[2]:,.0f} ₽"
                              "<br>Бюджет: %{customdata[3]:,.0f} ₽/нед<extra></extra>",
            ))
            fig.add_vline(x=ROAS_LAUNCH, line_dash="dot", line_color="#00C853")
            fig.update_layout(
                height=max(320, 30 * len(cdf) + 90),
                xaxis_title="ROAS (вся история)", yaxis_title=None,
                margin=dict(t=10, b=10, l=10, r=10),
            )
            st.plotly_chart(fig, width="stretch")
            st.caption(
                f"Зелёный — запустить, жёлтый — тест, красный — не запускать "
                f"(ДРР > {DRR_MAX:.0f}% или без заказов). Пунктир {ROAS_LAUNCH:.0f} — порог запуска."
            )

        # полная таблица вердиктов
        st.subheader("Все кампании: вердикты и цифры")
        status = g.apply(
            lambda r: "Работает" if r["works_now"] else f"Остановлена {r['last']:%d.%m}", axis=1)
        full = g.assign(status=status).rename(columns={
            "campaign_name": "Кампания", "verdict": "Вердикт", "roas": "ROAS",
            "roas30": "ROAS за 30 дн", "drr": "ДРР по заказам, %",
            "spend": "Расход", "revenue": "Выручка с рекламы",
            "orders": "Заказы с рекламы", "days": "Дней работы",
            "weekly": "Бюджет, ₽/нед", "status": "Статус", "campaign_id": "ID",
        })
        st.dataframe(
            full[["Кампания", "Вердикт", "ROAS", "ROAS за 30 дн", "ДРР по заказам, %",
                  "Расход", "Выручка с рекламы", "Заказы с рекламы", "Дней работы",
                  "Статус", "Бюджет, ₽/нед", "ID"]],
            width="stretch", hide_index=True,
            column_config={
                "ROAS": st.column_config.NumberColumn(format="%.2f"),
                "ROAS за 30 дн": st.column_config.NumberColumn(format="%.2f"),
                "ДРР по заказам, %": st.column_config.NumberColumn(format="%.1f %%"),
                "Расход": st.column_config.NumberColumn(format="₽ %,.0f"),
                "Выручка с рекламы": st.column_config.NumberColumn(format="₽ %,.0f"),
                "Заказы с рекламы": st.column_config.NumberColumn(format="₽ %,.0f"),
                "Бюджет, ₽/нед": st.column_config.NumberColumn(format="₽ %,.0f"),
            },
        )

st.divider()

with st.expander("Правила контроля и почему это сработает"):
    st.markdown(
        f"""
- **Сентябрь — начало высокого сезона**: спрос на мебель растёт осенью, а стоимость клика
  обычно повышается к ноябрю. Запуск проверенных кампаний в сентябре набирает рейтинг
  и отзывы к пиковым месяцам.
- **Бюджеты не завышаем**: недельный бюджет = исторический средний расход кампании,
  на котором её ROAS уже доказан.
- **ДРР ≤ {DRR_MAX:.0f}% — жёсткий лимит**: столько заложено в себестоимость каждого товара;
  кампании, которые обходились дороже, в план не попадают.
- **Контроль еженедельно**: кампания с ROAS < 2 за две недели или ДРР выше лимита — пауза и разбор.
- **Кросс-продажи учитываем**: если ROAS по атрибуции низкий, но ДРР по заказам в пределах
  лимита, кампания идёт в тест, а не в отказ.
"""
    )
