"""ETL pipeline — забирает данные из Ozon API и сохраняет в БД."""
import calendar as cal
import json
import logging
import time
from datetime import datetime, date, timedelta, timezone

from .models import (
    Order,
    DailyMetric,
    StockSnapshot,
    FinanceTransaction,
    CashFlow,
    FinanceBalance,
    AdDailyStats,
    ProductPrice,
    ProductMapping,
    BoostSnapshot,
    EtlState,
    get_session,
)
from .ozon_client import OzonClient
from .config import SHOPS as DEFAULT_SHOPS, is_premium, has_performance_api

logger = logging.getLogger(__name__)


def _date_range(days_back: int) -> tuple[str, str]:
    today = date.today()
    return (
        (today - timedelta(days=days_back)).isoformat() + "T00:00:00Z",
        today.isoformat() + "T23:59:59Z",
    )


# Дни перекрытия при инкрементальной догрузке: Ozon может корректировать
# аналитику и финансы задним числом (возвраты, отмены), поэтому берём с запасом.
WATERMARK_OVERLAP_DAYS = 3


def _get_last_sync(session, key: str):
    row = session.query(EtlState).filter_by(key=key).first()
    return row.last_sync if row else None


def _set_last_sync(session, key: str, value):
    row = session.query(EtlState).filter_by(key=key).first()
    if row:
        row.last_sync = value
        row.updated_at = datetime.now(timezone.utc)
    else:
        session.add(EtlState(key=key, last_sync=value))


def sync_orders(session, shops: list[str] = None, days_back: int = 30):
    """Синхронизация заказов из Ozon FBS."""
    if shops is None:
        shops = list(DEFAULT_SHOPS)

    for shop in shops:
        try:
            client = OzonClient(shop)
        except ValueError as e:
            logger.warning("Skip %s: %s", shop, e)
            continue

        existing_names = {
            name for (name,) in session.query(Order.name)
            .filter(Order.marketplace == shop)
            .all()
        }

        postings_fbs = client.fetch_fbs_postings(days_back=days_back, skip_details_for=existing_names)
        logger.info("[%s] Fetched %d FBS postings", shop, len(postings_fbs))

        try:
            postings_fbo = client.fetch_fbo_postings(days_back=days_back)
            logger.info("[%s] Fetched %d FBO postings", shop, len(postings_fbo))
        except Exception as e:
            logger.warning("[%s] Failed to fetch FBO postings: %s", shop, e)
            postings_fbo = []

        postings = postings_fbs + postings_fbo

        posting_numbers = [p["posting_number"] for p in postings if p.get("posting_number")]
        existing_orders = {}
        if posting_numbers:
            existing_orders = {
                o.name: o
                for o in session.query(Order)
                .filter(Order.name.in_(posting_numbers))
                .all()
            }

        for posting in postings:
            existing = existing_orders.get(posting["posting_number"])

            financial = posting.get("financial_data", {})
            products_meta = posting.get("products", [])
            fin_products = financial.get("products", [])
            merged_products = []
            for i, meta in enumerate(products_meta):
                fp = fin_products[i] if i < len(fin_products) else {}
                # Комиссия приходит в двух видах:
                #   v3 /fbs/get: плоские commission_amount / commission_percent;
                #   v4 /fbs/list: вложенный commission: {amount, percent, currency}.
                comm = fp.get("commission") or {}
                commission_amount = fp.get("commission_amount")
                if commission_amount is None:
                    commission_amount = comm.get("amount", 0) or 0
                commission_percent = fp.get("commission_percent")
                if commission_percent is None:
                    commission_percent = comm.get("percent", 0) or 0
                merged_products.append({
                    "offer_id": meta.get("sku"),
                    "name": meta.get("name"),
                    "quantity": meta.get("quantity"),
                    "price": fp.get("price"),
                    "old_price": fp.get("old_price"),
                    "discount_value": fp.get("total_discount_value"),
                    "discount_percent": fp.get("total_discount_percent"),
                    "commission_amount": commission_amount,
                    "commission_percent": commission_percent,
                    "payout": fp.get("payout"),
                    "actions": fp.get("actions", []),
                })
            merged_data = json.dumps(merged_products, ensure_ascii=False)

            if existing:
                existing.marketplace_status = posting["status"]
                existing.ozon_costs_data = merged_data
            else:
                order = Order(
                    name=posting["posting_number"],
                    marketplace=posting["marketplace"],
                    marketplace_status=posting["status"],
                    order_date=datetime.fromisoformat(posting["order_date"])
                    if posting["order_date"]
                    else datetime.utcnow(),
                    customer_name=posting["customer_name"],
                    phone=posting["customer_phone"],
                    email=posting["customer_email"],
                    delivery_address=posting["delivery_address"],
                    customer_comment=posting["customer_comment"],
                    distance_mkad=posting["distance_mkad"],
                    elevator=posting["elevator"],
                    floor=str(posting["floor"]),
                    ozon_costs_data=merged_data,
                    order_source=posting.get("order_source", "fbs"),
                )
                session.add(order)

        session.commit()


def sync_analytics(session, shops: list[str] = None, days_back: int = 30):
    """Синхронизация ежедневной аналитики продаж."""
    if shops is None:
        shops = list(DEFAULT_SHOPS)

    today = date.today()
    _, date_to = _date_range(days_back)

    for shop in shops:
        try:
            client = OzonClient(shop)
        except ValueError:
            continue

        # Инкрементальная догрузка от последнего успешного запуска (с перекрытием).
        key = f"analytics:{shop}"
        last = _get_last_sync(session, key)
        if last is not None:
            date_from = (last - timedelta(days=WATERMARK_OVERLAP_DAYS)).isoformat() + "T00:00:00Z"
        else:
            date_from = (today - timedelta(days=days_back)).isoformat() + "T00:00:00Z"

        data = client.get_analytics_data(
            date_from=date_from,
            date_to=date_to,
            dimensions=["sku", "day"],
            metrics=["revenue", "ordered_units"],
        )

        if data is None:
            logger.info("[%s] Analytics: no access (Premium required)", shop)
            continue

        result = data.get("result") or {}
        rows = result.get("data", []) or result.get("rows", [])

        logger.info("[%s] Fetched %d analytics rows", shop, len(rows))

        existing_metrics = {
            (m.date, m.sku): m
            for m in session.query(DailyMetric)
            .filter(
                DailyMetric.shop == shop,
                DailyMetric.date >= date.fromisoformat(date_from[:10]),
                DailyMetric.date <= date.fromisoformat(date_to[:10])
            )
            .all()
        }

        for row in rows:
            dimensions = row.get("dimensions", [])
            metrics = row.get("metrics", [])
            sku = dimensions[0].get("id", "") if len(dimensions) > 0 else ""
            product_name = dimensions[0].get("name", "") if len(dimensions) > 0 else ""
            day_str = dimensions[1].get("id", "") if len(dimensions) > 1 else ""
            try:
                day = date.fromisoformat(day_str)
            except (ValueError, TypeError):
                day = date.today()

            existing = existing_metrics.get((day, sku))

            if existing:
                existing.revenue = float(metrics[0]) if len(metrics) > 0 else 0
                existing.ordered_units = int(metrics[1]) if len(metrics) > 1 else 0
                existing.returns = 0
                existing.cancellations = 0
                existing.product_name = product_name
                existing.raw_json = json.dumps(row, ensure_ascii=False)
            else:
                m = DailyMetric(
                    shop=shop,
                    date=day,
                    sku=sku,
                    product_name=product_name,
                    revenue=float(metrics[0]) if len(metrics) > 0 else 0,
                    ordered_units=int(metrics[1]) if len(metrics) > 1 else 0,
                    returns=0,
                    cancellations=0,
                    raw_json=json.dumps(row, ensure_ascii=False),
                )
                session.add(m)
                existing_metrics[(day, sku)] = m

        session.commit()
        _set_last_sync(session, key, today)
        session.commit()


def sync_stocks(session, shops: list[str] = None):
    """Синхронизация остатков на складах."""
    if shops is None:
        shops = list(DEFAULT_SHOPS)

    today = date.today()

    for shop in shops:
        try:
            client = OzonClient(shop)
        except ValueError:
            continue

        rows = client.get_stock_on_warehouses()
        logger.info("[%s] Fetched %d stock rows", shop, len(rows))

        existing_stocks = {
            (s.sku, s.warehouse_name): s
            for s in session.query(StockSnapshot)
            .filter_by(shop=shop, snapshot_date=today)
            .all()
        }

        for row in rows:
            sku = row.get("sku", "")
            wh = row.get("warehouse_name", "")
            existing = existing_stocks.get((sku, wh))
            if existing:
                existing.quantity = int(row.get("quantity", 0) or 0)
                existing.reserved = int(row.get("reserved", 0) or 0)
                existing.in_transit = int(row.get("in_transit", 0) or 0)
                existing.product_name = row.get("product_name", "") or row.get("name", "")
            else:
                session.add(
                    StockSnapshot(
                        shop=shop,
                        snapshot_date=today,
                        sku=sku,
                        product_name=row.get("product_name", "") or row.get("name", ""),
                        warehouse_name=wh,
                        quantity=int(row.get("quantity", 0) or 0),
                        reserved=int(row.get("reserved", 0) or 0),
                        in_transit=int(row.get("in_transit", 0) or 0),
                    )
                )

        session.commit()


def sync_finance(session, shops: list[str] = None, days_back: int = 30):
    """Синхронизация финансовых транзакций."""
    if shops is None:
        shops = list(DEFAULT_SHOPS)

    today = date.today()
    full_from, full_to = _date_range(days_back)

    for shop in shops:
        if not is_premium(shop):
            logger.info("[%s] Skipping finance: no Premium", shop)
            continue

        try:
            client = OzonClient(shop)
        except ValueError:
            continue

        # Инкрементальная догрузка транзакций от последнего запуска (с перекрытием).
        # Cash flow ниже всегда тянем за полное окно — его дедуп завязан на period_start/end.
        key = f"finance:{shop}"
        last = _get_last_sync(session, key)
        if last is not None:
            date_from = (last - timedelta(days=WATERMARK_OVERLAP_DAYS)).isoformat() + "T00:00:00Z"
        else:
            date_from = full_from
        date_to = full_to

        # Транзакции — по месяцам (ограничение API: 30 дней)
        current = date.fromisoformat(date_from[:10])
        dt_end = date.fromisoformat(date_to[:10])
        while current <= dt_end:
            month_end = min(
                date(current.year, current.month, cal.monthrange(current.year, current.month)[1]),
                dt_end,
            )
            m_from = current.isoformat() + "T00:00:00Z"
            m_to = month_end.isoformat() + "T23:59:59Z"

            transactions = client.get_finance_transactions(m_from, m_to)
            if transactions is None:
                logger.info("[%s] Finance tx %s: no access", shop, current.isoformat()[:7])
            else:
                logger.info("[%s] Fetched %d tx for %s", shop, len(transactions), current.isoformat()[:7])

                # Use operation_id parsed from raw_json as the absolute unique key
                existing_tx = {}
                for t in (
                    session.query(FinanceTransaction)
                    .filter(
                        FinanceTransaction.shop == shop,
                        FinanceTransaction.operation_date >= current,
                        FinanceTransaction.operation_date <= month_end,
                    )
                    .all()
                ):
                    try:
                        op_id = json.loads(t.raw_json).get("operation_id")
                        if op_id:
                            existing_tx[op_id] = t
                    except Exception:
                        pass

                for tx in transactions:
                    op_id = tx.get("operation_id")
                    if op_id and op_id in existing_tx:
                        continue  # Already exists

                    op_date_str = tx.get("operation_date", "")
                    try:
                        op_date = date.fromisoformat(str(op_date_str).split(" ")[0])
                    except (ValueError, TypeError):
                        op_date = date.today()

                    posting = tx.get("posting", {}) or {}
                    posting_number = posting.get("posting_number", "")
                    amount = float(tx.get("amount", 0) or 0)

                    session.add(
                        FinanceTransaction(
                            shop=shop,
                            operation_date=op_date,
                            operation_type=tx.get("operation_type", ""),
                            operation_type_name=tx.get("operation_type_name", ""),
                            posting_number=posting_number,
                            amount=amount,
                            raw_json=json.dumps(tx, ensure_ascii=False),
                        )
                    )

            # переход к первому дню следующего месяца
            current = date(month_end.year, month_end.month, 1)
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        # Движение денежных средств — всегда за полное окно.
        cash_flows = client.get_cash_flow(full_from, full_to)
        if cash_flows is None:
            logger.info("[%s] Cash flow: no access", shop)
        else:
            logger.info("[%s] Fetched %d cash flow entries", shop, len(cash_flows))
            ps = date.fromisoformat(full_from[:10])
            pe = date.fromisoformat(full_to[:10])

            existing_cfs = {
                (c.cash_flow_type, c.operation, c.amount): c
                for c in session.query(CashFlow)
                .filter_by(shop=shop, period_start=ps, period_end=pe)
                .all()
            }

            for cf in cash_flows:
                cf_type = cf.get("cash_flow_type", "")
                operation = cf.get("operation", "")
                amount = float(cf.get("amount", 0) or 0)
                existing = existing_cfs.get((cf_type, operation, amount))
                if existing is None:
                    session.add(
                        CashFlow(
                            shop=shop,
                            period_start=ps,
                            period_end=pe,
                            cash_flow_type=cf_type,
                            operation=operation,
                            amount=amount,
                            raw_json=json.dumps(cf, ensure_ascii=False),
                        )
                    )

        session.commit()
        _set_last_sync(session, key, today)
        session.commit()


def sync_ads(session, shop: str = "ozon_stylint", days_back: int = 30):
    """Синхронизация рекламной статистики из Performance API."""
    from .performance_client import PerformanceClient

    # Данные Performance API за текущий день предварительные и завышенные
    # (пустые SKU со сводными значениями), Ozon корректирует их на следующий день.
    # Поэтому тянем статистику только до вчерашнего дня включительно.
    today = date.today()
    date_to = today - timedelta(days=1)
    date_from = date_to - timedelta(days=days_back)

    try:
        client = PerformanceClient(shop)
    except Exception as e:
        logger.warning("Performance API: auth failed — %s", e)
        return

    try:
        rows = client.get_stats(date_from.isoformat(), date_to.isoformat(), max_wait=300)
    except Exception as e:
        logger.warning("[%s] Performance API stats failed — %s", shop, e)
        return
    logger.info("Ads: fetched %d stats rows", len(rows))

    def _parse_date(date_str: str):
        date_str = (date_str or "").replace("\xa0", "").replace("\u00a0", "").strip()
        try:
            return date.fromisoformat(date_str)
        except (ValueError, TypeError):
            try:
                return datetime.strptime(date_str, "%d.%m.%Y").date()
            except (ValueError, TypeError):
                return None

    # Агрегируем строки по (date, campaign_id, sku): отчёт может отдавать
    # несколько строк на один ключ (например, положительный расход и строку
    # корректировки с отрицательным значением). Иначе получаются дубликаты.
    SUM_FIELDS = (
        "impressions", "clicks", "cart_adds", "units_sold",
        "spend", "promo_revenue", "total_order_amount",
    )
    RATE_FIELDS = ("ctr", "avg_cpc", "promo_acos", "overall_acos")

    merged: dict[tuple, dict] = {}
    for row in rows:
        d = _parse_date(row.get("date", ""))
        if d is None:
            continue
        key = (
            d,
            row.get("campaign_id") or "",
            row.get("sku") or "",
        )
        if key not in merged:
            merged[key] = dict(row)
        else:
            tgt = merged[key]
            for f in SUM_FIELDS:
                tgt[f] = (tgt.get(f) or 0) + (row.get(f) or 0)
            for f in RATE_FIELDS:
                try:
                    new_v = float(row.get(f) or 0)
                    old_v = float(tgt.get(f) or 0)
                except (ValueError, TypeError):
                    new_v = old_v = 0.0
                tgt[f] = row.get(f) if abs(new_v) > abs(old_v) else tgt.get(f)

    if not merged:
        return

    # Полное обновление для успешно выгруженных кампаний: удаляем старые строки
    # этих кампаний в окне и вставляем свежие. Иначе «протухшие» строки
    # (например, ошибочно завышенные значения за день, которые Ozon потом исправил)
    # оставались бы в базе навсегда.
    fetched_cids = {cid for (_, cid, _) in merged}
    deleted = (
        session.query(AdDailyStats)
        .filter(
            AdDailyStats.shop == shop,
            AdDailyStats.date >= date_from,
            AdDailyStats.date <= date_to,
            AdDailyStats.campaign_id.in_(fetched_cids),
        )
        .delete(synchronize_session=False)
    )
    logger.info("Ads: deleted %d stale rows for %d campaigns", deleted, len(fetched_cids))

    # Убираем строки за «сегодня» и позже — это предварительные завышенные данные.
    session.query(AdDailyStats).filter(
        AdDailyStats.shop == shop,
        AdDailyStats.date >= today,
        AdDailyStats.campaign_id != "cpo",
    ).delete(synchronize_session=False)

    for (d, cid, sku), row in merged.items():
        session.add(
            AdDailyStats(
                shop=shop,
                date=d,
                campaign_id=cid,
                campaign_name=row.get("campaign_name", ""),
                sku=sku,
                product_name=row.get("product_name", ""),
                impressions=row.get("impressions", 0),
                clicks=row.get("clicks", 0),
                ctr=row.get("ctr", 0.0),
                cart_adds=row.get("cart_adds", 0),
                avg_cpc=row.get("avg_cpc", 0.0),
                spend=row.get("spend", 0.0),
                units_sold=row.get("units_sold", 0),
                promo_revenue=row.get("promo_revenue", 0.0),
                total_order_amount=row.get("total_order_amount", 0.0),
                promo_acos=row.get("promo_acos", 0.0),
                overall_acos=row.get("overall_acos", 0.0),
            )
        )

    session.commit()


def sync_cpo_ads(session, shop: str = "ozon_stylint", days_back: int = 30):
    """Синхронизация расхода «Оплата за заказ» (CPO/SEARCH_PROMO) из Performance API.

    Стандартный /api/client/statistics отклоняет кампании «Оплата за заказ», поэтому
    расход берётся из двух отдельных отчётов и складывается в AdDailyStats с
    campaign_id='cpo' (по дням).
    """
    from .performance_client import PerformanceClient

    date_to = date.today()
    date_from = date_to - timedelta(days=days_back)

    try:
        client = PerformanceClient(shop)
    except Exception as e:
        logger.warning("Performance API: auth failed — %s", e)
        return

    rows = []
    try:
        rows = client.get_cpo_orders(date_from.isoformat(), date_to.isoformat(), max_wait=300)
    except Exception as e:
        logger.warning("[%s] CPO orders failed — %s", shop, e)
    try:
        rows += client.get_cpo_all_products(date_from.isoformat(), date_to.isoformat(), max_wait=300)
    except Exception as e:
        logger.warning("[%s] CPO all products failed — %s", shop, e)

    daily: dict = {}
    for r in rows:
        daily[r["date"]] = daily.get(r["date"], 0.0) + (r.get("spend") or 0.0)
    logger.info("[%s] CPO: %d rows → %d days", shop, len(rows), len(daily))

    existing = {
        (a.date, a.campaign_id or "", a.sku or ""): a
        for a in session.query(AdDailyStats).filter(
            AdDailyStats.shop == shop,
            AdDailyStats.campaign_id == "cpo",
            AdDailyStats.date >= date_from,
            AdDailyStats.date <= date_to,
        ).all()
    }
    for d, spend in daily.items():
        key = (d, "cpo", "")
        e = existing.get(key)
        if e:
            e.spend = spend
        else:
            session.add(
                AdDailyStats(
                    shop=shop, date=d, campaign_id="cpo",
                    campaign_name="Оплата за заказ", sku="", spend=spend,
                )
            )
    session.commit()


def sync_balance(session, shops: list[str] = None):
    """Синхронизация текущего баланса кабинетов (из /v1/finance/balance)."""
    if shops is None:
        shops = list(DEFAULT_SHOPS)

    today = date.today()

    for shop in shops:
        try:
            client = OzonClient(shop)
        except ValueError:
            continue

        try:
            data = client.get_finance_balance(today.isoformat(), today.isoformat())
        except Exception as e:
            logger.warning("[%s] Finance balance failed — %s", shop, e)
            continue

        total = data.get("total", {})
        balance = total.get("closing_balance", {}).get("value", 0) or 0

        existing = (
            session.query(FinanceBalance)
            .filter_by(shop=shop, snapshot_date=today)
            .first()
        )
        if existing:
            existing.balance = balance
            existing.raw_json = json.dumps(data, ensure_ascii=False)
        else:
            session.add(
                FinanceBalance(
                    shop=shop,
                    snapshot_date=today,
                    balance=balance,
                    raw_json=json.dumps(data, ensure_ascii=False),
                )
            )

        logger.info("[%s] Balance = %.2f", shop, balance)

    session.commit()


def sync_boost(session, shops: list[str] = None):
    """Синхронизация эластичного бустинга (акция ELASTIC_BOOSTING)."""
    if shops is None:
        shops = list(DEFAULT_SHOPS)

    today = date.today()

    for shop in shops:
        try:
            client = OzonClient(shop)
        except ValueError as e:
            logger.warning("Skip %s: %s", shop, e)
            continue

        try:
            action_id = client.get_elastic_boosting_action_id()
        except Exception as e:
            logger.warning("[%s] Boost actions failed — %s", shop, e)
            continue

        if action_id is None:
            logger.info("[%s] Boost: акция ELASTIC_BOOSTING не найдена", shop)
            continue

        try:
            products = client.get_action_products(action_id)
        except Exception as e:
            logger.warning("[%s] Boost products failed — %s", shop, e)
            continue

        product_ids = [p.get("id") for p in products if p.get("id") is not None]
        mapping = {}
        if product_ids:
            try:
                mapping = client.get_product_info_by_product_id(product_ids)
            except Exception as e:
                logger.warning("[%s] Boost product info failed — %s", shop, e)

        logger.info("[%s] Boost: %d товаров в акции %s", shop, len(products), action_id)

        existing = {
            (b.offer_id, b.product_id): b
            for b in session.query(BoostSnapshot)
            .filter_by(shop=shop, snapshot_date=today)
            .all()
        }

        for p in products:
            pid = p.get("id")
            product_id = str(pid) if pid is not None else ""
            info = mapping.get(int(pid)) if pid is not None else {}
            offer_id = info.get("offer_id") or ""
            key = (offer_id, product_id)
            b = existing.get(key)

            values = dict(
                sku=str(info.get("sku") or ""),
                name=info.get("name") or "",
                current_boost=float(p.get("current_boost") or 0),
                min_boost=float(p.get("min_boost") or 0),
                max_boost=float(p.get("max_boost") or 0),
                price_min_elastic=float(p.get("price_min_elastic") or 0),
                price_max_elastic=float(p.get("price_max_elastic") or 0),
                action_price=float(p.get("action_price") or 0),
                price=float(p.get("price") or 0),
                max_action_price=float(p.get("max_action_price") or 0),
                stock=int(p.get("stock") or 0),
                add_mode=p.get("add_mode"),
            )

            if b:
                for k, v in values.items():
                    setattr(b, k, v)
            else:
                session.add(
                    BoostSnapshot(
                        shop=shop,
                        snapshot_date=today,
                        product_id=product_id,
                        offer_id=offer_id,
                        **values,
                    )
                )

        session.commit()


def sync_prices(session, shops: list[str] = None):
    """Синхронизация текущих цен и ценового индекса (из /v5/product/info/prices)."""
    if shops is None:
        shops = list(DEFAULT_SHOPS)

    today = date.today()

    for shop in shops:
        try:
            client = OzonClient(shop)
        except ValueError:
            continue

        rows = client.get_product_prices()
        logger.info("[%s] Fetched %d price rows", shop, len(rows))

        existing_prices = {
            (p.offer_id, p.product_id): p
            for p in session.query(ProductPrice)
            .filter_by(shop=shop, snapshot_date=today)
            .all()
        }

        for row in rows:
            offer_id = row.get("offer_id", "")
            product_id = str(row.get("product_id", ""))
            price_info = row.get("price") or {}
            commissions = row.get("commissions") or {}
            price_indexes = row.get("price_indexes") or {}
            ozon_index = price_indexes.get("ozon_index_data") or {}

            key = (offer_id, product_id)
            existing = existing_prices.get(key)
            if existing:
                existing.price = float(price_info.get("price", 0) or 0)
                existing.old_price = float(price_info.get("old_price", 0) or 0)
                existing.min_price = float(price_info.get("min_price", 0) or 0)
                existing.marketing_price = float(price_info.get("marketing_seller_price", 0) or 0)
                existing.retail_price = float(price_info.get("retail_price", 0) or 0)
                existing.net_price = float(price_info.get("net_price", 0) or 0)
                existing.price_index = float(ozon_index.get("price_index_value", 0) or 0)
                existing.color_index = price_indexes.get("color_index", "")
                existing.commission_fbo = float(commissions.get("sales_percent_fbo", 0) or 0)
                existing.commission_fbs = float(commissions.get("sales_percent_fbs", 0) or 0)
                existing.raw_json = json.dumps(row, ensure_ascii=False)
            else:
                session.add(
                    ProductPrice(
                        shop=shop,
                        snapshot_date=today,
                        offer_id=offer_id,
                        product_id=product_id,
                        price=float(price_info.get("price", 0) or 0),
                        old_price=float(price_info.get("old_price", 0) or 0),
                        min_price=float(price_info.get("min_price", 0) or 0),
                        marketing_price=float(price_info.get("marketing_seller_price", 0) or 0),
                        retail_price=float(price_info.get("retail_price", 0) or 0),
                        net_price=float(price_info.get("net_price", 0) or 0),
                        price_index=float(ozon_index.get("price_index_value", 0) or 0),
                        color_index=price_indexes.get("color_index", ""),
                        commission_fbo=float(commissions.get("sales_percent_fbo", 0) or 0),
                        commission_fbs=float(commissions.get("sales_percent_fbs", 0) or 0),
                        raw_json=json.dumps(row, ensure_ascii=False),
                    )
                )

        session.commit()


def sync_product_mapping(session, shops: list[str] = None):
    """Мост sku (числовой) ↔ offer_id (артикул) из /v3/product/info/list."""
    if shops is None:
        shops = list(DEFAULT_SHOPS)

    for shop in shops:
        try:
            client = OzonClient(shop)
        except ValueError:
            continue

        skus = set()
        for (sku,) in session.query(DailyMetric.sku).filter(DailyMetric.shop == shop).distinct().all():
            if sku:
                skus.add(sku)
        for (sku,) in session.query(AdDailyStats.sku).filter(AdDailyStats.shop == shop).distinct().all():
            if sku:
                skus.add(sku)

        if not skus:
            logger.info("[%s] Product mapping: no skus found", shop)
            continue

        rows = client.get_product_info_list(list(skus))
        logger.info("[%s] Fetched %d product info rows for mapping", shop, len(rows))

        existing = {
            m.sku: m
            for m in session.query(ProductMapping).filter(ProductMapping.shop == shop).all()
        }

        for row in rows:
            sku = str(row.get("sku", ""))
            if not sku:
                continue
            offer_id = row.get("offer_id", "")
            name = row.get("name", "")
            product_id = str(row.get("id", "") or row.get("product_id", ""))
            m = existing.get(sku)
            if m:
                m.offer_id = offer_id
                m.product_id = product_id
                m.name = name
            else:
                session.add(
                    ProductMapping(
                        shop=shop, sku=sku, offer_id=offer_id,
                        product_id=product_id, name=name,
                    )
                )

        session.commit()


def run_pipeline(shops: list[str] = None, days_back: int = 30, fast: bool = False):
    """Запуск полного ETL-пайплайна. fast=True — без аналитики и рекламы (медленные/rate-limited).

    Кабинеты синхронизируются параллельно (у каждого свой rate limit и своя сессия БД).
    """
    from dotenv import load_dotenv; load_dotenv()
    from concurrent.futures import ThreadPoolExecutor
    from sqlalchemy.orm import sessionmaker
    from .models import init_db

    if shops is None:
        shops = list(DEFAULT_SHOPS)

    engine = init_db()
    Session = sessionmaker(bind=engine)

    def _run_shop(shop: str):
        session = Session()
        try:
            logger.info("[%s] pipeline start", shop)
            sync_orders(session, [shop], days_back)
            if not fast:
                sync_analytics(session, [shop], days_back)
            sync_stocks(session, [shop])
            sync_finance(session, [shop], days_back)
            sync_balance(session, [shop])
            sync_boost(session, [shop])
            sync_prices(session, [shop])

            if not fast and has_performance_api(shop):
                sync_ads(session, shop, days_back)
                sync_cpo_ads(session, shop, days_back)

            sync_product_mapping(session, [shop])
            logger.info("[%s] pipeline done", shop)
        except Exception:
            logger.exception("[%s] pipeline failed", shop)
            session.rollback()
        finally:
            session.close()

    logger.info("=== ETL START ===")
    try:
        with ThreadPoolExecutor(max_workers=len(shops)) as ex:
            list(ex.map(_run_shop, shops))
    finally:
        engine.dispose()
    logger.info("=== ETL DONE ===")