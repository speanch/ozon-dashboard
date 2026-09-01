#!/usr/bin/env python3
"""Генерирует синтетические демо-данные для запуска дашборда без API-ключей.

    python seed_demo.py [--days 120] [--db dashboard.db]

Создаёт SQLite-базу с заказами, рекламной статистикой, ценами, бустингом
и экспериментами. Все товары, кампании и цифры — вымышленные.
"""
import argparse
import json
import random
from datetime import date, datetime, time, timedelta, timezone

from etl.models import (
    init_db, get_session, Order, DailyMetric, FinanceBalance,
    AdDailyStats, ProductPrice, ProductMapping, BoostSnapshot, Experiment,
)

random.seed(7)

# ── каталог: (offer_id, sku, name, price, net_price) ──────────────────────
CATALOG = [
    ("BED-A1-160", 101, "Кровать-трансформер «А1» 160×200, Белый", 89000, 50000),
    ("BED-A2-160", 102, "Кровать-трансформер «А2» 160×200, Графит", 94000, 53000),
    ("BED-A3-140", 103, "Кровать-трансформер «А3» 140×200, Дуб сонома", 78000, 44000),
    ("MATT-M1-90", 104, "Матрас «М1» 90×200, средней жесткости", 18900, 10000),
    ("MATT-M2-160", 105, "Матрас «М2» 160×200, жесткий", 27900, 15500),
    ("ARM-U1", 106, "Подлокотник универсальный, Белый", 6900, 3700),
    ("ANT-2D", 107, "Антресоль 2-створчатая, Белый", 24500, 13800),
    ("FRM-160", 108, "Опора-подиум 160 см", 15900, 8800),
]
SHOP_PRODUCTS = {
    "shop_a": CATALOG,
    "shop_b": CATALOG[:5] + CATALOG[6:7],
}

COLORS = ["WHITE", "GREEN", "YELLOW", "RED", "SUPER", "WITHOUT_INDEX"]

STATUS_POOL = [
    ("delivered", 0.80), ("delivering", 0.04), ("awaiting_packaging", 0.03),
    ("cancelled", 0.08), ("returned", 0.05),
]
CUSTOMERS = ["Иванов И.И.", "Петрова А.С.", "Смирнов К.Д.", "Кузнецова О.В.",
             "Попов Д.А.", "Соколова М.И.", "Лебедев П.П.", "Новикова Е.В."]

# ── кампании: (id, название, promoted offer, тип, первый день, последний) ──
# тип: winner (ROAS 4–7), test (ROAS 2–3), waste (без выручки / высокий ДРР)
CAMPAIGNS = {
    "shop_a": [
        ("C-101", "Поиск: кровати", "BED-A1-160", "winner", 0, None),
        ("C-102", "Автокампания: матрасы", "MATT-M1-90", "winner", 0, 10),
        ("C-103", "ССК - топ товаров", "BED-A2-160", "winner", 40, None),
        ("C-104", "Тест: подлокотники", "ARM-U1", "test", 30, None),
        ("C-105", "Баннеры: антресоли", "ANT-2D", "waste", 0, 45),
        ("C-106", "Бустинг: опоры", "FRM-160", "waste", 0, 45),
    ],
    "shop_b": [
        ("C-201", "Поиск: кровати 140", "BED-A3-140", "winner", 0, None),
        ("C-202", "Автокампания: кровати 160", "BED-A1-160", "test", 0, None),
        ("C-203", "ССК - матрасы", "MATT-M2-160", "test", 0, None),
        ("C-204", "Баннеры: общий", "BED-A2-160", "waste", 0, 40),
    ],
}

EXPERIMENTS = [
    ("Реклама: новая стратегия ставок", 60, 52, "shop_a", None),
    ("Скидка 10% на матрасы", 40, 33, "shop_a,shop_b", None),
    ("Новое главное фото кроватей", 25, 15, "shop_b", None),
    ("Тест бесплатной сборки", 12, None, "shop_a,shop_b", None),
]


def _pick_status() -> str:
    r, acc = random.random(), 0.0
    for status, p in STATUS_POOL:
        acc += p
        if r <= acc:
            return status
    return "delivered"


def main():
    ap = argparse.ArgumentParser(description="Синтетические демо-данные для Ozon Dashboard")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--db", default="dashboard.db")
    args = ap.parse_args()

    today = date.today()
    start = today - timedelta(days=args.days - 1)
    engine = init_db(args.db)
    session = get_session(engine)
    if session.query(Order).first():
        print(f"База {args.db} уже содержит данные — сеанс демо-данных пропущен.")
        session.close()
        engine.dispose()
        return

    posting_seq = 0

    for shop, catalog in SHOP_PRODUCTS.items():
        product_by_offer = {offer: {"sku": sku, "name": name, "price": price, "net": net}
                            for offer, sku, name, price, net in catalog}

        # ── mapping + снимки цен ─────────────────────────────────────────
        for offer, sku, name, price, net in catalog:
            session.add(ProductMapping(shop=shop, sku=str(sku), offer_id=offer,
                                       product_id=str(sku * 1000), name=name))
            snap = start
            while snap <= today:
                p = price * random.uniform(0.97, 1.03)
                session.add(ProductPrice(
                    shop=shop, snapshot_date=snap, offer_id=offer,
                    product_id=str(sku * 1000),
                    price=round(p), old_price=round(p * 1.25), min_price=round(p * random.uniform(0.72, 0.92)),
                    marketing_price=round(p), retail_price=round(p), net_price=net,
                    price_index=round(random.uniform(0.9, 1.25), 2),
                    color_index=random.choice(COLORS),
                    commission_fbo=round(p * 0.16),
                    raw_json=json.dumps({"price_indexes": {"external_index_data": {
                        "price_index_value": round(random.uniform(0.85, 1.3), 2)}}}),
                ))
                snap += timedelta(days=7)

        # ── заказы + дневная аналитика ───────────────────────────────────
        for d in range(args.days):
            day = start + timedelta(days=d)
            weekend = day.weekday() >= 5
            n_orders = max(0, int(random.gauss(3.2 if weekend else 2.4, 1.4)))
            offer_weights = {o: w for o, w in zip(
                [c[0] for c in CATALOG], [4, 3, 3, 3, 2, 2, 1.5, 1.5])}
            catalog_weights = [offer_weights[c[0]] for c in catalog]
            day_sales: dict = {}
            for _ in range(n_orders):
                offer, sku, name, price, net = random.choices(catalog, weights=catalog_weights)[0]
                status = _pick_status()
                items = [{"offer_id": offer, "name": name, "quantity": 1,
                          "price": price, "old_price": round(price * 1.18),
                          "discount_value": round(price * 0.18),
                          "commission_amount": round(price * 0.16)}]
                if random.random() < 0.22:  # к кровати часто берут матрас
                    m = product_by_offer["MATT-M1-90" if "MATT-M1-90" in product_by_offer else "MATT-M2-160"]
                    items.append({"offer_id": "MATT-M1-90" if "MATT-M1-90" in product_by_offer else "MATT-M2-160",
                                  "name": m["name"], "quantity": 1, "price": m["price"],
                                  "old_price": round(m["price"] * 1.18),
                                  "discount_value": round(m["price"] * 0.18),
                                  "commission_amount": round(m["price"] * 0.16)})
                posting_seq += 1
                session.add(Order(
                    name=f"DEMO-{shop}-{posting_seq:06d}",
                    marketplace=shop, marketplace_status=status,
                    order_date=datetime.combine(day, time(random.randint(8, 23), random.randint(0, 59)),
                                                tzinfo=timezone.utc),
                    customer_name=random.choice(CUSTOMERS),
                    phone=f"+7 (9{random.randint(10, 99)}) {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}",
                    delivery_address="г. Москва, ул. Ленина, д. 10",
                    distance_mkad=random.randint(0, 30),
                    elevator=random.choice(["passenger", "cargo", "none"]),
                    floor=str(random.randint(1, 17)),
                    has_assembly=random.choice(["yes", "no"]),
                    ozon_costs_data=json.dumps(items, ensure_ascii=False),
                ))
                if status not in ("cancelled",):
                    key = (offer, str(sku), name)
                    rev, units = day_sales.get(key, (0.0, 0))
                    day_sales[key] = (rev + price * 1, units + 1)
            for (offer, sku, name), (rev, units) in day_sales.items():
                session.add(DailyMetric(shop=shop, date=day, sku=sku, product_name=name,
                                        revenue=float(rev), ordered_units=units,
                                        returns=0, cancellations=0, raw_json="{}"))

        # ── рекламные кампании ───────────────────────────────────────────
        for cid, cname, offer, kind, first_off, stop_off in CAMPAIGNS[shop]:
            promo = product_by_offer[offer]
            first_day = start + timedelta(days=first_off)
            last_day = today - timedelta(days=stop_off) if stop_off else today
            d = first_day
            while d <= last_day:
                spend = round(random.uniform(700, 2800) * (0.6 if kind == "test" else 1))
                clicks = max(1, int(spend / random.uniform(28, 70)))
                impressions = int(clicks / random.uniform(0.004, 0.012))
                cart_adds = int(clicks * random.uniform(0.03, 0.08))
                if kind == "winner":
                    roas = random.uniform(3.6, 7.0)
                    cross = random.uniform(8.0, 12.0)
                    units = random.choices([0, 1, 2, 3], weights=[2, 4, 3, 1])[0]
                elif kind == "test":
                    roas = random.uniform(1.9, 3.1)
                    cross = random.uniform(7.0, 11.0)
                    units = random.choices([0, 1], weights=[3, 2])[0]
                else:
                    roas = 0.0
                    cross = random.choice([0, random.uniform(2.0, 4.0)])
                    units = 0
                promo_revenue = round(spend * roas, 2)
                total_orders_amt = round(spend * cross, 2)
                session.add(AdDailyStats(
                    shop=shop, date=d, campaign_id=cid, campaign_name=cname,
                    sku=promo["sku"], product_name=promo["name"],
                    impressions=impressions, clicks=clicks,
                    ctr=round(clicks / impressions * 100, 2) if impressions else 0,
                    cart_adds=cart_adds, avg_cpc=round(spend / clicks, 2), spend=float(spend),
                    units_sold=units, promo_revenue=promo_revenue,
                    total_order_amount=total_orders_amt,
                    promo_acos=round(spend / promo_revenue * 100, 2) if promo_revenue else 0,
                    overall_acos=round(spend / total_orders_amt * 100, 2) if total_orders_amt else 0,
                ))
                d += timedelta(days=1)

        # ── бустинг ──────────────────────────────────────────────────────
        for offer, sku, name, price, net in catalog:
            current = random.choices([0, 10, 25, 50, 75], weights=[3, 2, 2, 2, 1])[0]
            session.add(BoostSnapshot(
                shop=shop, snapshot_date=today, product_id=str(sku * 1000),
                offer_id=offer, sku=str(sku), name=name,
                current_boost=current, min_boost=0, max_boost=75,
                price_min_elastic=round(price * 0.95),
                price_max_elastic=round(price * 0.85),
                action_price=round(price * (1 - current / 100)),
                price=price, max_action_price=round(price * 0.85),
                stock=random.randint(3, 40), add_mode="ADD_MODE_AUTO",
            ))

        # ── баланс кабинета ──────────────────────────────────────────────
        for weeks_ago, bal in [(12, 180000), (8, 240000), (4, 290000), (0, random.randint(200000, 400000))]:
            session.add(FinanceBalance(shop=shop, snapshot_date=today - timedelta(weeks=weeks_ago),
                                       balance=float(bal)))

    # ── эксперименты ─────────────────────────────────────────────────────
    for name, start_off, end_off, shops, offers in EXPERIMENTS:
        session.add(Experiment(
            name=name,
            start_date=today - timedelta(days=start_off),
            end_date=today - timedelta(days=end_off) if end_off is not None else None,
            shop=shops, offer_ids=offers,
            description="Синтетический эксперимент для демонстрации меток на графиках.",
        ))

    session.commit()
    counts = {m.__tablename__: session.query(m).count() for m in
              [Order, DailyMetric, AdDailyStats, ProductPrice, ProductMapping,
               BoostSnapshot, Experiment, FinanceBalance]}
    session.close()
    engine.dispose()
    print(f"Демо-данные записаны в {args.db}:")
    for table, n in counts.items():
        print(f"  {table}: {n}")


if __name__ == "__main__":
    main()
