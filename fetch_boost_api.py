#!/usr/bin/env python3
"""
Выгрузка текущего «Эластичного бустинга» Ozon по Seller API и отслеживание изменений.

Не нужно парсить xlsx: API отдаёт те же значения в открытом виде:
    GET  /v1/actions            — список акций; ищем action_type == "ELASTIC_BOOSTING"
    POST /v1/actions/products   — товары в акции:
        current_boost      — текущий бустинг, %  (в xlsx: столбец O, = BI−1)
        min_boost/max_boost— диапазон бустинга, % (в xlsx: BD−1 / BE−1)
        price_min_elastic  — цена для минимального бустинга (в xlsx: P = BG)
        price_max_elastic  — цена для максимального бустинга (в xlsx: Q = BF)
        action_price       — текущая цена по акции
    POST /v3/product/info/list  — маппинг product_id → offer_id (артикул)

Каждый запуск сохраняет снапшот: snapshots/boost_<shop>_<timestamp>.json
С флагом --diff сравнивает с предыдущим снапшотом и показывает изменения по товарам.

Использование:
    python fetch_boost_api.py --shop stylint
    python fetch_boost_api.py --shop stylint --diff
    python fetch_boost_api.py --shop all
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

BASE_URL = "https://api-seller.ozon.ru"
HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

SHOPS = {
    "stylint": (os.getenv("OZON_STYLINT_CLIENT_ID", "443362"), os.getenv("OZON_STYLINT_API_KEY", "")),
    "rs": (os.getenv("OZON_RS_CLIENT_ID", "3201725"), os.getenv("OZON_RS_API_KEY", "")),
}


class OzonBoostClient:
    def __init__(self, shop):
        cid, key = SHOPS[shop]
        if not key:
            raise ValueError(f"нет API-ключа для магазина {shop} в .env")
        self.shop = shop
        self.headers = {"Client-Id": cid, "Api-Key": key, "Content-Type": "application/json"}

    def _get(self, path, **kw):
        return requests.get(BASE_URL + path, headers=self.headers, timeout=30, **kw)

    def _post(self, path, payload, **kw):
        r = requests.post(BASE_URL + path, headers=self.headers, json=payload, timeout=30, **kw)
        if r.status_code == 429:
            time.sleep(2)
            r = requests.post(BASE_URL + path, headers=self.headers, json=payload, timeout=30, **kw)
        r.raise_for_status()
        return r.json()

    def get_elastic_action_id(self):
        data = self._get("/v1/actions").json()
        for a in data.get("result", []):
            if a.get("action_type") == "ELASTIC_BOOSTING":
                return a.get("id")
        return None

    def get_boost_products(self, action_id):
        products = []
        last_id = None
        while True:
            payload = {"action_id": action_id, "limit": 1000}
            if last_id:
                payload["last_id"] = last_id
            data = self._post("/v1/actions/products", payload)
            result = data.get("result", {})
            batch = result.get("products", [])
            products.extend(batch)
            last_id = result.get("last_id")
            if not last_id or not batch:
                break
            time.sleep(0.6)
        return products

    def resolve_offer_ids(self, product_ids):
        """product_id (OzonID) → {offer_id, sku, name}."""
        mapping = {}
        for i in range(0, len(product_ids), 500):
            batch = product_ids[i : i + 500]
            data = self._post(
                "/v3/product/info/list",
                {"offer_id": [], "product_id": [str(p) for p in batch], "sku": []},
            )
            for it in data.get("items", []):
                pid = it.get("id")
                if pid is not None:
                    mapping[int(pid)] = {
                        "offer_id": it.get("offer_id"),
                        "sku": it.get("sku"),
                        "name": it.get("name"),
                    }
            time.sleep(0.6)
        return mapping


def collect(shop):
    client = OzonBoostClient(shop)
    action_id = client.get_elastic_action_id()
    if action_id is None:
        print(f"[{shop}] акция ELASTIC_BOOSTING не найдена")
        return None
    products = client.get_boost_products(action_id)
    mapping = client.resolve_offer_ids([p.get("id") for p in products if p.get("id")])
    rows = []
    for p in products:
        pid = p.get("id")
        info = mapping.get(pid, {}) if pid is not None else {}
        rows.append(
            {
                "product_id": pid,
                "offer_id": info.get("offer_id"),
                "sku": info.get("sku"),
                "name": info.get("name"),
                "price": p.get("price"),
                "action_price": p.get("action_price"),
                "max_action_price": p.get("max_action_price"),
                "current_boost": p.get("current_boost"),
                "min_boost": p.get("min_boost"),
                "max_boost": p.get("max_boost"),
                "price_min_elastic": p.get("price_min_elastic"),
                "price_max_elastic": p.get("price_max_elastic"),
                "stock": p.get("stock"),
                "add_mode": p.get("add_mode"),
            }
        )
    rows.sort(key=lambda r: (r["offer_id"] is None, r["offer_id"] or ""))
    return {"shop": shop, "action_id": action_id, "ts": datetime.now().isoformat(timespec="seconds"), "products": rows}


def snapshot_path(shop, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(out_dir, f"boost_{shop}_{ts}.json")


def diff(prev, curr):
    by_id = {p.get("product_id"): p for p in prev["products"]}
    changes = []
    for p in curr["products"]:
        old = by_id.get(p.get("product_id"))
        if old is None:
            changes.append({"offer_id": p["offer_id"], "event": "новый товар в акции", "p": p})
            continue
        d = {}
        for field in ("current_boost", "price_min_elastic", "price_max_elastic", "action_price"):
            if old.get(field) != p.get(field):
                d[field] = (old.get(field), p.get(field))
        if d:
            changes.append({"offer_id": p["offer_id"], "event": "изменение", "delta": d, "p": p})
    old_ids = {p.get("product_id") for p in prev["products"]}
    for p in prev["products"]:
        if p.get("product_id") not in {c.get("product_id") for c in curr["products"]}:
            changes.append({"offer_id": p["offer_id"], "event": "ушёл из акции", "p": p})
    return changes


def main():
    ap = argparse.ArgumentParser(description="Эластичный бустинг Ozon по API")
    ap.add_argument("--shop", default="stylint", choices=list(SHOPS) + ["all"])
    ap.add_argument("--diff", action="store_true", help="сравнить с предыдущим снапшотом")
    ap.add_argument("--out", default=os.path.join(HERE, "snapshots"))
    args = ap.parse_args()

    shops = list(SHOPS) if args.shop == "all" else [args.shop]
    for shop in shops:
        print(f"\n=== {shop} ===")
        data = collect(shop)
        if data is None:
            continue
        path = snapshot_path(shop, args.out)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        print(f"товаров в акции: {len(data['products'])}, снапшот: {path}")

        if args.diff:
            # загружаем предыдущий снапшот, исключая только что записанный
            prevs = sorted(
                (os.path.join(args.out, f) for f in os.listdir(args.out)
                 if f.startswith(f"boost_{shop}_") and f.endswith(".json") and f != os.path.basename(path)),
                reverse=True,
            )
            if not prevs:
                print("  предыдущих снапшотов нет — сравнение невозможно")
                continue
            with open(prevs[0], encoding="utf-8") as fh:
                prev = json.load(fh)
            changes = diff(prev, data)
            print(f"\n  Изменений относительно {os.path.basename(prevs[0])}: {len(changes)}")
            for c in changes[:50]:
                if c["event"] == "изменение":
                    parts = ", ".join(
                        f"{k}: {v[0]} → {v[1]}" for k, v in c["delta"].items()
                    )
                    print(f"    {c['offer_id']}: {parts}")
                else:
                    print(f"    {c['offer_id']}: {c['event']}")
            if len(changes) > 50:
                print(f"    … и ещё {len(changes) - 50}")


if __name__ == "__main__":
    main()
