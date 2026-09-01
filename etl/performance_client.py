"""
Ozon Performance API client — OAuth2 + кампании + асинхронная статистика.
Base: https://api-performance.ozon.ru
"""
import csv
import io
import json
import logging
import os
import time
import zipfile
from datetime import date, datetime, timedelta

import requests

logger = logging.getLogger(__name__)
PERF_BASE = "https://api-performance.ozon.ru"


def _request_with_retry(method: str, url: str, retries: int = 5, backoff: float = 10.0, **kwargs) -> requests.Response:
    for attempt in range(retries):
        resp = requests.request(method, url, **kwargs)
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = backoff * (2 ** attempt)
            else:
                wait = backoff * (2 ** attempt)
            logger.warning("Rate-limited (429), retry %d/%d after %.1fs", attempt + 1, retries, wait)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


class PerformanceClient:
    def __init__(self, shop: str = "shop_a"):
        if shop == "shop_b":
            self._client_id = os.getenv("PERFORMANCE_SHOP_B_CLIENT_ID")
            self._client_secret = os.getenv("PERFORMANCE_SHOP_B_CLIENT_SECRET")
        else:
            self._client_id = os.getenv("PERFORMANCE_SHOP_A_CLIENT_ID")
            self._client_secret = os.getenv("PERFORMANCE_SHOP_A_CLIENT_SECRET")
        self._token = None
        self._token_expires = 0

    def _auth(self):
        if self._token and time.time() < self._token_expires - 60:
            return
        resp = _request_with_retry(
            "POST",
            f"{PERF_BASE}/api/client/token",
            json={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = time.time() + data.get("expires_in", 1800)

    def _headers(self, extra: dict = None):
        self._auth()
        h = {"Authorization": f"Bearer {self._token}"}
        if extra:
            h.update(extra)
        return h

    ADV_OBJECT_TYPES = ("SKU", "SEARCH_PROMO")

    def get_campaigns(self) -> list[dict]:
        campaigns: list[dict] = []
        for adv_type in self.ADV_OBJECT_TYPES:
            resp = _request_with_retry(
                "GET",
                f"{PERF_BASE}/api/client/campaign",
                params={"advObjectType": adv_type},
                headers=self._headers({"Accept": "application/json"}),
                timeout=30,
            )
            resp.raise_for_status()
            campaigns.extend(resp.json().get("list", []))
        return campaigns

    def get_active_campaign_names(self) -> set[str]:
        """Возвращает названия активных (запущенных) кампаний."""
        campaigns = self.get_campaigns()
        return {c["title"] for c in campaigns if c.get("state") == "CAMPAIGN_STATE_RUNNING"}

    def get_active_campaign_ids(self) -> set[str]:
        """Возвращает ID активных кампаний."""
        campaigns = self.get_campaigns()
        return {c["id"] for c in campaigns if c.get("state") == "CAMPAIGN_STATE_RUNNING"}

    def _create_stats_report(
        self, campaign_ids: list[str], date_from: str, date_to: str
    ) -> str:
        payload = {
            "campaigns": campaign_ids,
            "dateFrom": date_from,
            "dateTo": date_to,
            "groupBy": "DATE",
        }
        resp = _request_with_retry(
            "POST",
            f"{PERF_BASE}/api/client/statistics",
            json=payload,
            headers=self._headers({"Content-Type": "application/json", "Accept": "application/json"}),
            timeout=30,
            retries=10,
        )
        return resp.json()["UUID"]

    def _poll_report_once(self, uuid: str, timeout: int = 10) -> dict:
        headers = self._headers({"Accept": "application/json"})
        resp = _request_with_retry(
            "GET",
            f"{PERF_BASE}/api/client/statistics/{uuid}",
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _poll_report(self, uuid: str, timeout: int = 120) -> dict | None:
        waited = 0
        while waited < timeout:
            time.sleep(4)
            waited += 4
            data = self._poll_report_once(uuid)
            state = data.get("state")
            if state == "OK":
                return data
            if state in ("ERROR", "CANCELED"):
                logger.warning("Stats state: %s", state)
                return None
            # NOT_STARTED, PROCESSING — continue polling
        logger.warning("Stats timeout after %ds", timeout)
        return None

    def _poll_reports(self, jobs: list, max_wait: int = 120) -> list:
        """Опрашивает сразу все отчёты параллельно.

        jobs: список (uuid, campaigns_by_id).
        Возвращает список готовых (report_dict, campaigns_by_id).
        """
        pending = list(jobs)
        results = []
        waited = 0
        while pending and waited < max_wait:
            time.sleep(4)
            waited += 4
            still = []
            for uuid, cbid in pending:
                try:
                    data = self._poll_report_once(uuid)
                except Exception as e:
                    logger.warning("Poll %s failed: %s", uuid[:8], e)
                    still.append((uuid, cbid))
                    continue
                state = data.get("state")
                if state == "OK":
                    results.append((data, cbid))
                elif state in ("ERROR", "CANCELED"):
                    logger.warning("Stats %s state: %s", uuid[:8], state)
                else:
                    still.append((uuid, cbid))
            pending = still
        if pending:
            logger.warning("Stats timeout: %d reports still pending", len(pending))
        return results

    def _download_csv(self, link: str, campaigns_by_id: dict = None) -> list[dict]:
        resp = _request_with_retry(
            "GET",
            f"{PERF_BASE}{link}",
            headers=self._headers({"Accept": "*/*"}),
            timeout=60,
        )
        resp.raise_for_status()

        rows = []
        ct = resp.headers.get("Content-Type", "")

        def _extract_campaign_id(filename: str) -> str:
            return filename.split("_")[0]

        if "application/zip" in ct:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                for name in zf.namelist():
                    cid = _extract_campaign_id(name)
                    cname = (campaigns_by_id or {}).get(cid, {}).get("title", "") if campaigns_by_id else ""
                    with zf.open(name) as f:
                        parsed = self._parse_csv_lines(f.read().decode("utf-8-sig"), cid, cname)
                        rows.extend(parsed)
        elif "text/csv" in ct:
            rows.extend(self._parse_csv_lines(resp.text))

        return rows

    def _parse_csv_lines(self, text: str, campaign_id: str = "", campaign_name: str = "") -> list[dict]:
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len(lines) < 3:
            return []

        rows = []
        for line in lines[2:]:  # skip metadata + header
            parts = line.split(";")
            if len(parts) < 17:
                continue
            try:
                date_str = parts[0].strip().replace("\xa0", "").replace("\u00a0", "")
                # Пропускаем итоговую строку «Всего» и любые строки без валидной даты,
                # иначе они попадают в «сегодня» через fallback при парсинге даты.
                if not date_str or date_str.lower().startswith("всего"):
                    continue
                try:
                    date.fromisoformat(date_str)
                except ValueError:
                    datetime.strptime(date_str, "%d.%m.%Y")
                rows.append({
                    "campaign_id": campaign_id,
                    "campaign_name": campaign_name,
                    "date": date_str,
                    "sku": parts[1].strip(),
                    "product_name": parts[2].strip(),
                    "price": float(parts[3].replace(",", ".").replace("\xa0", "")) if parts[3].strip() else 0,
                    "impressions": int(parts[4].strip()) if parts[4].strip() else 0,
                    "clicks": int(parts[5].strip()) if parts[5].strip() else 0,
                    "ctr": float(parts[6].replace(",", ".")) if parts[6].strip() else 0.0,
                    "cart_adds": int(parts[7].strip()) if parts[7].strip() else 0,
                    "avg_cpc": float(parts[8].replace(",", ".").replace("\xa0", "")) if parts[8].strip() else 0.0,
                    "spend": float(parts[9].replace(",", ".").replace("\xa0", "")) if parts[9].strip() else 0.0,
                    "units_sold": int(parts[10].strip()) if parts[10].strip() else 0,
                    "promo_revenue": float(parts[11].replace(",", ".").replace("\xa0", "")) if parts[11].strip() else 0.0,
                    "model_units": int(parts[12].strip()) if parts[12].strip() else 0,
                    "model_revenue": float(parts[13].replace(",", ".").replace("\xa0", "")) if parts[13].strip() else 0.0,
                    "promo_acos": float(parts[14].replace(",", ".")) if parts[14].strip() else 0.0,
                    "total_order_amount": float(parts[15].replace(",", ".").replace("\xa0", "")) if parts[15].strip() else 0.0,
                    "overall_acos": float(parts[16].replace(",", ".")) if parts[16].strip() else 0.0,
                })
            except (ValueError, IndexError) as e:
                logger.debug("Skip row: %s — %s", e, parts[:3])
                continue

        return rows

    def get_stats(
        self, date_from: str, date_to: str, max_wait: int = 120
    ) -> list[dict]:
        campaigns = self.get_campaigns()
        if not campaigns:
            return []

        # Не фильтруем кампании по состоянию: исторические данные нужны и по
        # архивным/остановленным кампаниям, иначе расход за прошлые дни теряется.
        # Пропускаем только черновики (у них нет статистики).
        #
        # В отчёт /api/client/statistics можно передавать только кампании
        # «Оплата за клик» (advObjectType=SKU). Кампании «Оплата за заказ»
        # (advObjectType=SEARCH_PROMO, PaymentType=CPO) этот эндпоинт отклоняет:
        #   "generation of this type of report is forbidden for the transferred list of campaigns"
        # Для них нужен отдельный метод выгрузки (см. TODO ниже).
        campaigns_by_id = {c["id"]: c for c in campaigns}
        campaign_ids = [
            cid for cid, c in campaigns_by_id.items()
            if c.get("state") != "CAMPAIGN_STATE_DRAFT"
            and c.get("advObjectType") == "SKU"
        ]

        d_start = date.fromisoformat(date_from)
        d_end = date.fromisoformat(date_to)

        # Фаза 1: отправляем все отчёты сразу (API асинхронный, возвращает UUID).
        # Озон генерирует их параллельно, поэтому не ждём каждый по очереди.
        jobs: list[tuple[str, dict]] = []
        while d_start < d_end:
            chunk_end = min(d_start + timedelta(days=60), d_end)
            chunk_from = d_start.isoformat()
            chunk_to = chunk_end.isoformat()
            logger.info("Submitting reports for %s – %s (%d campaigns)",
                        chunk_from, chunk_to, len(campaign_ids))

            batch_size = 10
            for i in range(0, len(campaign_ids), batch_size):
                batch = campaign_ids[i : i + batch_size]
                try:
                    uuid = self._create_stats_report(batch, chunk_from, chunk_to)
                    jobs.append((uuid, campaigns_by_id))
                except Exception as e:
                    logger.warning("Submit batch %s–%s failed: %s", chunk_from, chunk_to, e)
                # Не шлём отчёты вплотную: Performance API жёстко рейт-лимитит
                # генерацию, иначе получим 429 с долгим backoff.
                time.sleep(5)

            d_start = chunk_end + timedelta(days=1)

        logger.info("Submitted %d stats reports", len(jobs))

        # Фаза 2: опрашиваем все отчёты параллельно.
        reports = self._poll_reports(jobs, max_wait=max_wait)

        # Фаза 3: качаем готовые CSV.
        all_rows = []
        for report, cbid in reports:
            link = report.get("link", "")
            if not link:
                continue
            try:
                rows = self._download_csv(link, cbid)
                all_rows.extend(rows)
                logger.info("Downloaded %d rows", len(rows))
            except Exception as e:
                logger.warning("Download %s failed: %s", link, e)

        logger.info("Total ad stats: %d rows", len(all_rows))
        return all_rows

    # ── Оплата за заказ (CPO) ──────────────────────────────────────────────

    def _cpo_report_text(self, path: str, method: str, payload: dict = None,
                         params: dict = None, max_wait: int = 120) -> str | None:
        """Генерирует CPO-отчёт, дожидается готовности и возвращает текст CSV."""
        if method == "POST":
            headers = self._headers({"Content-Type": "application/json", "Accept": "application/json"})
            resp = _request_with_retry("POST", f"{PERF_BASE}{path}", json=payload or {}, headers=headers, timeout=30, retries=10)
        else:
            headers = self._headers({"Accept": "application/json"})
            resp = _request_with_retry("GET", f"{PERF_BASE}{path}", params=params or {}, headers=headers, timeout=30, retries=10)
        resp.raise_for_status()
        uuid = resp.json()["UUID"]
        report = self._poll_report(uuid, timeout=max_wait)
        if not report or not report.get("link"):
            logger.warning("CPO report %s: no link", path)
            return None
        dl = _request_with_retry("GET", f"{PERF_BASE}{report['link']}",
                                 headers=self._headers({"Accept": "*/*"}), timeout=60)
        dl.raise_for_status()
        return dl.text

    def _parse_cpo_csv(self, text: str) -> list[dict]:
        """Парсит CSV CPO-отчёта: ищет столбцы «Дата» и «Расход», возвращает [{date, spend}]."""
        reader = csv.reader(io.StringIO(text), delimiter=";")
        lines = [l for l in reader if l]
        header_idx = None
        for i, l in enumerate(lines):
            cells = [(c or "") for c in l]
            if any("Расход" in c for c in cells) and any("Дата" in c for c in cells):
                header_idx = i
                break
        if header_idx is None:
            return []
        header = lines[header_idx]
        i_date = next((i for i, c in enumerate(header) if "Дата" in (c or "")), -1)
        i_spend = next((i for i, c in enumerate(header) if "Расход" in (c or "")), -1)
        if i_date < 0 or i_spend < 0:
            return []
        rows = []
        for l in lines[header_idx + 1:]:
            if len(l) <= max(i_date, i_spend):
                continue
            date_cell = (l[i_date] or "").strip()
            spend_cell = (l[i_spend] or "").strip()
            if not date_cell or date_cell.lower().startswith("всего"):
                continue
            try:
                d = datetime.strptime(date_cell, "%d.%m.%Y").date()
            except (ValueError, TypeError):
                continue
            try:
                spend = float(spend_cell.replace("\xa0", "").replace(",", ".")) if spend_cell else 0.0
            except ValueError:
                spend = 0.0
            rows.append({"date": d, "spend": spend})
        return rows

    def get_cpo_orders(self, date_from: str, date_to: str, max_wait: int = 120) -> list[dict]:
        """Расход «Оплата за заказ» по выбранным товарам — отчёт по заказам (по дням)."""
        text = self._cpo_report_text(
            "/api/client/statistic/orders/generate", "POST",
            payload={"from": f"{date_from}T00:00:00Z", "to": f"{date_to}T23:59:59Z"},
            max_wait=max_wait,
        )
        return self._parse_cpo_csv(text) if text else []

    def get_cpo_all_products(self, date_from: str, date_to: str, max_wait: int = 120) -> list[dict]:
        """Расход «Оплата за заказ» по всем товарам — отчёт по товарам (по дням)."""
        text = self._cpo_report_text(
            "/api/client/statistics/all_sku_promo/products/generate", "GET",
            params={"timeBounds.from": f"{date_from}T00:00:00Z",
                    "timeBounds.to": f"{date_to}T23:59:59Z"},
            max_wait=max_wait,
        )
        return self._parse_cpo_csv(text) if text else []