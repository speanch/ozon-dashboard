"""
Ozon Seller API client — адаптирован из calculator_MP/utils/ozon_api.py.
Добавлены эндпоинты аналитики, финансов, остатков.
"""
import os
import re
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api-seller.ozon.ru"

SHOPS_CONFIG = {
    "ozon_stylint": {
        "client_id_env": "OZON_STYLINT_CLIENT_ID",
        "client_id_default": "443362",
        "api_key_env": "OZON_STYLINT_API_KEY",
    },
    "ozon_rs": {
        "client_id_env": "OZON_RS_CLIENT_ID",
        "client_id_default": "3201725",
        "api_key_env": "OZON_RS_API_KEY",
    },
}


class OzonClient:
    # Лимит Seller API — 2 запроса/сек на кабинет (client_id). Держим чуть ниже
    # с запасом, чтобы не упираться в 429 и не тратить время на ретраи.
    MIN_REQUEST_INTERVAL = 0.55

    def __init__(self, shop_name: str):
        if shop_name not in SHOPS_CONFIG:
            raise ValueError(f"Unknown shop: {shop_name}")
        cfg = SHOPS_CONFIG[shop_name]
        # Читаем креды лениво (в момент создания клиента), а не на импорте модуля:
        # иначе load_dotenv() после импорта не подхватит ключи.
        client_id = os.getenv(cfg["client_id_env"], cfg["client_id_default"])
        api_key = os.getenv(cfg["api_key_env"], "")
        if not api_key:
            logger.warning("OzonClient %s: API key is empty — requests will fail", shop_name)
        self.shop_name = shop_name
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Client-Id": client_id,
                "Api-Key": api_key,
                "Content-Type": "application/json",
            }
        )
        self._last_request = 0.0
        self._throttle_lock = threading.Lock()

    def _throttle(self):
        """Держит не чаще MIN_REQUEST_INTERVAL между запросами к одному кабинету."""
        with self._throttle_lock:
            now = time.monotonic()
            wait = self._last_request + self.MIN_REQUEST_INTERVAL - now
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _post_raw(self, path: str, payload: dict, timeout: int = 30, retries: int = 3):
        """POST с ретраями на 429 (rate limit Seller API: 2 req/sec)."""
        url = urljoin(BASE_URL, path)
        for attempt in range(retries):
            self._throttle()
            resp = self.session.post(url, json=payload, timeout=timeout)
            if resp.status_code == 429:
                wait = 2.0 * (attempt + 1)
                try:
                    ra = resp.headers.get("Retry-After")
                    if ra:
                        wait = float(ra)
                except (ValueError, TypeError):
                    pass
                logger.warning(
                    "%s %s → 429, retry %d/%d after %.1fs",
                    path, self.shop_name, attempt + 1, retries, wait,
                )
                time.sleep(wait)
                continue
            return resp
        return resp

    def _post(self, path: str, payload: dict, timeout: int = 30) -> dict:
        resp = self._post_raw(path, payload, timeout)
        resp.raise_for_status()
        return resp.json()

    def _post_safe(self, path: str, payload: dict, timeout: int = 30) -> dict | None:
        """Безопасный POST — возвращает None при ошибке вместо падения."""
        resp = self._post_raw(path, payload, timeout)
        if resp.status_code >= 400:
            logger.warning(
                "%s %s → %d %s", path, self.shop_name, resp.status_code, resp.text[:200]
            )
            return None
        return resp.json()

    def _get(self, path: str, timeout: int = 30, retries: int = 3):
        """GET с ретраями на 429 (rate limit Seller API: 2 req/sec)."""
        url = urljoin(BASE_URL, path)
        for attempt in range(retries):
            self._throttle()
            resp = self.session.get(url, timeout=timeout)
            if resp.status_code == 429:
                wait = 2.0 * (attempt + 1)
                try:
                    ra = resp.headers.get("Retry-After")
                    if ra:
                        wait = float(ra)
                except (ValueError, TypeError):
                    pass
                logger.warning(
                    "%s %s → 429, retry %d/%d after %.1fs",
                    path, self.shop_name, attempt + 1, retries, wait,
                )
                time.sleep(wait)
                continue
            return resp
        return resp

    # ── adapted from calculator_MP/utils/ozon_api.py ──────────────────────

    def fetch_fbs_postings(
        self, days_back: int = 30, skip_details_for: set = None
    ) -> list[dict]:
        """
        Список FBS-отправлений. Взято из calculator_MP/utils/ozon_api.py:20-193.
        Расширено: финансовые данные парсятся, а не кладутся сырым JSON.
        """
        if skip_details_for is None:
            skip_details_for = set()

        date_to = datetime.utcnow()
        date_from = date_to - timedelta(days=days_back)

        all_postings = []
        limit = 100
        cursor = ""

        while True:
            payload = {
                "sort_dir": "ASC",
                "filter": {
                    "since": date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to": date_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                "limit": limit,
                "with": {"analytics_data": True, "barcodes": False, "financial_data": True},
            }
            if cursor:
                payload["cursor"] = cursor

            data = self._post("/v4/posting/fbs/list", payload)
            postings_list = data.get("postings", [])
            all_postings.extend(postings_list)

            if not data.get("has_next"):
                break
            cursor = data.get("cursor", "")
            if not cursor:
                break

        parsed = []
        for posting in all_postings:
            order_date_str = posting.get("in_process_at") or posting.get("created_at")
            order_date = (
                order_date_str.split(".")[0].replace("T", " ").replace("Z", "")
                if order_date_str
                else None
            )

            customer = posting.get("customer") or {}
            address = customer.get("address") or {}

            delivery_method = posting.get("delivery_method") or {}
            delivery_name = delivery_method.get("name") or ""

            distance_mkad = 0.0
            m = re.search(r"(\d+)\s*км", delivery_name.lower())
            if m:
                distance_mkad = float(m.group(1))

            posting_number = posting.get("posting_number")
            status = posting.get("status")

            prr_option = {}
            detailed_financial = None
            # v4 list: prr_option — строка ("lift"/"stairs"/"none"/"delivery_default").
            list_prr = posting.get("prr_option") or ""
            elevator_map = {
                "lift": "passenger",
                "cargo_lift": "cargo",
                "stairs": "no",
                "none": "no",
                "delivery_default": "no",
            }
            elevator = elevator_map.get(list_prr, "no")
            prr_floor = ""

            if (
                posting_number
                and posting_number not in skip_details_for
                and status not in ("cancelled", "delivered")
            ):
                try:
                    detail = self._post(
                        "/v3/posting/fbs/get",
                        {
                            "posting_number": posting_number,
                            "with": {
                                "analytics_data": True,
                                "financial_data": True,
                            },
                        },
                        timeout=10,
                    )
                    get_result = detail.get("result", {})
                    prr_option = get_result.get("prr_option") or {}
                    detailed_financial = get_result.get("financial_data")
                    # Детальный ответ точнее (может быть cargo_lift) и содержит этаж.
                    if prr_option.get("code"):
                        elevator = elevator_map.get(prr_option.get("code"), elevator)
                    if prr_option.get("floor"):
                        prr_floor = str(prr_option.get("floor"))
                    cust = get_result.get("customer") or {}
                    if cust.get("phone"):
                        customer["phone"] = cust["phone"]
                    if cust.get("name"):
                        customer["name"] = cust["name"]
                    a = cust.get("address") or {}
                    if a.get("address_tail"):
                        address["address_tail"] = a["address_tail"]
                except Exception as e:
                    logger.error("fbs/get failed for %s: %s", posting_number, e)

            financial = detailed_financial or posting.get("financial_data") or {}
            products_data = []
            for product in posting.get("products", []):
                products_data.append(
                    {
                        "name": product.get("name", ""),
                        "sku": product.get("offer_id"),
                        "quantity": product.get("quantity"),
                    }
                )

            parsed.append(
                {
                    "posting_number": posting_number,
                    "status": status,
                    "order_date": order_date,
                    "marketplace": self.shop_name,
                    "customer_name": customer.get("name", ""),
                    "customer_phone": customer.get("phone", ""),
                    "customer_email": customer.get("customer_email", ""),
                    "delivery_address": address.get("address_tail", ""),
                    "customer_comment": address.get("comment", ""),
                    "distance_mkad": distance_mkad,
                    "elevator": elevator,
                    "floor": prr_floor,
                    "products": products_data,
                    "financial_data": financial,
                    "order_source": "fbs",
                }
            )

        return parsed

    def fetch_fbo_postings(
        self, days_back: int = 30
    ) -> list[dict]:
        """
        Список FBO-отправлений из /v2/posting/fbo/list.
        """
        date_to = datetime.utcnow()
        date_from = date_to - timedelta(days=days_back)

        all_postings = []
        limit = 1000
        offset = 0

        while True:
            payload = {
                "dir": "ASC",
                "filter": {
                    "since": date_from.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to": date_to.strftime("%Y-%m-%dT%H:%M:%SZ"),
                },
                "limit": limit,
                "offset": offset,
                "with": {"analytics_data": True, "financial_data": True},
            }

            resp = self._post("/v2/posting/fbo/list", payload)
            postings_list = resp.get("result", [])
            if not isinstance(postings_list, list):
                if isinstance(postings_list, dict):
                    postings_list = postings_list.get("postings", []) or postings_list.get("rows", [])
                else:
                    postings_list = []

            all_postings.extend(postings_list)

            if len(postings_list) < limit:
                break
            offset += limit

        parsed = []
        for posting in all_postings:
            posting_number = posting.get("posting_number")
            order_date_str = posting.get("created_at")
            order_date = (
                order_date_str.split(".")[0].replace("T", " ").replace("Z", "")
                if order_date_str
                else None
            )

            financial = posting.get("financial_data") or {}
            products_data = []
            for product in posting.get("products", []):
                products_data.append(
                    {
                        "name": product.get("name", ""),
                        "sku": product.get("sku") or product.get("offer_id"),
                        "quantity": product.get("quantity"),
                    }
                )

            parsed.append(
                {
                    "posting_number": posting_number,
                    "status": posting.get("status"),
                    "order_date": order_date,
                    "marketplace": self.shop_name,
                    "customer_name": "Ozon FBO Customer",
                    "customer_phone": "",
                    "customer_email": "",
                    "delivery_address": "Ozon FBO Warehouse",
                    "customer_comment": "",
                    "distance_mkad": 0.0,
                    "elevator": "no",
                    "floor": "",
                    "products": products_data,
                    "financial_data": financial,
                    "order_source": "fbo",
                }
            )

        return parsed

    # ── новые эндпоинты для дашборда ──────────────────────────────────────

    def get_analytics_data(
        self,
        date_from: str,
        date_to: str,
        dimensions: list[str],
        metrics: list[str],
        filters: list[dict] = None,
    ) -> dict:
        """
        POST /v1/analytics/data — аналитика продаж.
        dimensions: ["sku", "day", "brand"]
        metrics: ["revenue", "ordered_units", "returns", "cancellations"]
        """
        all_data = []
        limit = 1000
        offset = 0
        first_resp = None
        while True:
            payload = {
                "date_from": date_from,
                "date_to": date_to,
                "dimension": dimensions,
                "metrics": metrics,
                "filters": filters or [],
                "sort": [],
                "limit": limit,
                "offset": offset,
            }
            resp = self._post_safe("/v1/analytics/data", payload)
            if resp is None:
                break
            if first_resp is None:
                first_resp = resp
            result = resp.get("result") or {}
            rows = result.get("data", []) or result.get("rows", [])
            if not rows:
                break
            all_data.extend(rows)
            if len(rows) < limit:
                break
            offset += limit

        if first_resp and "result" in first_resp:
            if "data" in first_resp["result"]:
                first_resp["result"]["data"] = all_data
            elif "rows" in first_resp["result"]:
                first_resp["result"]["rows"] = all_data
        return first_resp

    def get_stock_on_warehouses(self) -> list[dict]:
        """
        POST /v2/analytics/stock_on_warehouses — остатки на складах.
        """
        rows = []
        limit = 1000
        offset = 0
        while True:
            payload = {"warehouse_type": "ALL", "limit": limit, "offset": offset}
            data = self._post_safe("/v2/analytics/stock_on_warehouses", payload)
            if data is None:
                break
            result = data.get("result") or {}
            batch = result.get("rows", [])
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < limit:
                break
            offset += limit
        return rows

    def get_finance_transactions(
        self, date_from: str, date_to: str
    ) -> list[dict]:
        """
        POST /v3/finance/transaction/list — список финансовых транзакций.
        """
        operations = []
        page = 1
        page_size = 1000
        while True:
            payload = {
                "filter": {"date": {"from": date_from, "to": date_to}},
                "page": page,
                "page_size": page_size,
            }
            data = self._post_safe("/v3/finance/transaction/list", payload)
            if data is None:
                break
            result = data.get("result") or {}
            ops = result.get("operations", [])
            if not ops:
                break
            operations.extend(ops)
            page_count = result.get("page_count", 1)
            if page >= page_count:
                break
            page += 1
        return operations

    def get_finance_totals(self, date_from: str, date_to: str) -> dict:
        """
        POST /v3/finance/transaction/totals — сводка по транзакциям.
        """
        payload = {"date": {"from": date_from, "to": date_to}}
        return self._post("/v3/finance/transaction/totals", payload)

    def get_finance_balance(self, date_from: str, date_to: str) -> dict:
        """
        POST /v1/finance/balance — текущий баланс и движение средств за период.
        date_from/date_to в формате YYYY-MM-DD.
        """
        return self._post(
            "/v1/finance/balance", {"date_from": date_from, "date_to": date_to}
        )

    def get_cash_flow(
        self, date_from: str, date_to: str
    ) -> list[dict]:
        """
        POST /v1/finance/cash-flow-statement/list — отчёт о движении денежных средств.
        """
        payload = {
            "date": {"from": date_from, "to": date_to},
            "page_size": 999,
            "page": 1,
        }
        data = self._post_safe("/v1/finance/cash-flow-statement/list", payload)
        if data is None:
            return []
        result = data.get("result") or {}
        return result.get("cash_flows", [])

    def get_realization_report(
        self, date_from: str, date_to: str
    ) -> list[dict]:
        """
        POST /v2/finance/realization — отчёт о реализации товаров.
        """
        payload = {
            "date_from": date_from,
            "date_to": date_to,
            "limit": 1000,
        }
        data = self._post("/v2/finance/realization", payload)
        result = data.get("result") or {}
        return result.get("rows", [])

    def get_product_prices(self) -> list[dict]:
        """
        POST /v5/product/info/prices — цены, ценовой индекс и комиссии по товарам.
        Пагинация курсором (cursor), возвращает все страницы.
        """
        items = []
        cursor = None
        limit = 1000
        while True:
            payload = {
                "filter": {"offer_id": [], "product_id": [], "visibility": "ALL"},
                "limit": limit,
            }
            if cursor:
                payload["cursor"] = cursor
            data = self._post_safe("/v5/product/info/prices", payload, timeout=60)
            if data is None:
                break
            batch = data.get("items", [])
            items.extend(batch)
            cursor = data.get("cursor")
            if not cursor or len(batch) < limit:
                break
        return items

    def get_product_info_list(self, skus: list[str]) -> list[dict]:
        """
        POST /v3/product/info/list — мост sku (числовой) ↔ offer_id ↔ name.
        Принимает список числовых SKU, возвращает items с offer_id/sku/id/name.
        """
        items = []
        batch_size = 500
        for i in range(0, len(skus), batch_size):
            batch = skus[i : i + batch_size]
            data = self._post_safe(
                "/v3/product/info/list",
                {"offer_id": [], "product_id": [], "sku": batch},
                timeout=60,
            )
            if data is None:
                continue
            items.extend(data.get("items", []) or data.get("result", {}).get("items", []))
        return items

    def get_product_list(self, visibility: str = "ALL") -> list[dict]:
        """
        POST /v2/product/list — список товаров (топ товары).
        """
        payload = {
            "filter": {"visibility": visibility},
            "limit": 1000,
        }
        data = self._post("/v2/product/list", payload)
        result = data.get("result") or {}
        return result.get("items", [])

    def get_seller_ratings(self) -> dict[str, float]:
        """
        POST /v1/seller/info — рейтинги продавца.
        Возвращает словарь {название_метрики: значение}, включая оценку товаров.
        """
        data = self._post("/v1/seller/info", {})
        ratings = data.get("ratings", [])
        out = {}
        for r in ratings:
            name = r.get("name", "")
            cv = r.get("current_value") or {}
            out[name] = cv.get("value")
        return out

    # ── эластичный бустинг ────────────────────────────────────────────────

    def get_actions(self) -> list[dict]:
        """GET /v1/actions — список акций."""
        resp = self._get("/v1/actions")
        resp.raise_for_status()
        data = resp.json()
        return data.get("result", []) or []

    def get_elastic_boosting_action_id(self) -> int | None:
        """Возвращает id акции с action_type == "ELASTIC_BOOSTING"."""
        for action in self.get_actions():
            if action.get("action_type") == "ELASTIC_BOOSTING":
                return action.get("id")
        return None

    def get_action_products(self, action_id) -> list[dict]:
        """POST /v1/actions/products — товары в акции, пагинация через last_id."""
        products = []
        last_id = None
        while True:
            payload = {"action_id": action_id, "limit": 1000}
            if last_id:
                payload["last_id"] = last_id
            data = self._post_safe("/v1/actions/products", payload)
            if data is None:
                break
            result = data.get("result", {}) or {}
            batch = result.get("products", [])
            if not isinstance(batch, list) or not batch:
                break
            products.extend(batch)
            last_id = result.get("last_id")
            if not last_id:
                break
        return products

    def get_product_info_by_product_id(self, product_ids: list) -> dict:
        """
        POST /v3/product/info/list — маппинг product_id (OzonID) → {offer_id, sku, name}.
        Батчами по 500, с паузой 0.6 c между запросами (rate limit 2 req/sec).
        """
        mapping: dict = {}
        batch_size = 500
        for i in range(0, len(product_ids), batch_size):
            batch = product_ids[i : i + batch_size]
            data = self._post_safe(
                "/v3/product/info/list",
                {"offer_id": [], "product_id": [str(p) for p in batch], "sku": []},
                timeout=60,
            )
            if data is not None:
                for item in data.get("items", []) or []:
                    pid = item.get("id")
                    if pid is not None:
                        mapping[int(pid)] = {
                            "offer_id": item.get("offer_id"),
                            "sku": item.get("sku"),
                            "name": item.get("name"),
                        }
        return mapping

    def update_prices(self, prices: list[dict]) -> list[dict]:
        """
        POST /v1/product/import/prices — обновить цены (включая min_price).
        Батчами по 1000 товаров за запрос.
        prices: список словарей {"offer_id", "price", "min_price", "currency_code", "auto_action_enabled", ...}.
        Возвращает список ответов API (по одному на батч).
        """
        responses = []
        for i in range(0, len(prices), 1000):
            batch = prices[i:i + 1000]
            responses.append(self._post("/v1/product/import/prices", {"prices": batch}))
        return responses