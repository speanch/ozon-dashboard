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
from datetime import date, timedelta

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
    def __init__(self, shop: str = "ozon_stylint"):
        if shop == "ozon_rs":
            self._client_id = os.getenv("PERFORMANCE_RS_CLIENT_ID")
            self._client_secret = os.getenv("PERFORMANCE_RS_CLIENT_SECRET")
        else:
            self._client_id = os.getenv("PERFORMANCE_STYLINT_CLIENT_ID")
            self._client_secret = os.getenv("PERFORMANCE_STYLINT_CLIENT_SECRET")
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

    def get_campaigns(self) -> list[dict]:
        resp = _request_with_retry(
            "GET",
            f"{PERF_BASE}/api/client/campaign",
            params={"advObjectType": "SKU"},
            headers=self._headers({"Accept": "application/json"}),
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("list", [])

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

    def _poll_report(self, uuid: str, timeout: int = 120) -> dict | None:
        headers = self._headers({"Accept": "application/json"})
        waited = 0
        while waited < timeout:
            time.sleep(4)
            waited += 4
            resp = _request_with_retry(
                "GET",
                f"{PERF_BASE}/api/client/statistics/{uuid}",
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            state = data.get("state")
            if state == "OK":
                return data
            if state in ("ERROR", "CANCELED"):
                logger.warning("Stats state: %s", state)
                return None
            # NOT_STARTED, PROCESSING — continue polling
        logger.warning("Stats timeout after %ds", timeout)
        return None

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

        # Filter out archived, finished, draft, or unknown inactive campaigns to reduce batch counts
        active_states = {"CAMPAIGN_STATE_RUNNING", "CAMPAIGN_STATE_STOPPED", "CAMPAIGN_STATE_MODERATION"}
        filtered_campaigns = [c for c in campaigns if c.get("state") in active_states]
        if not filtered_campaigns:
            filtered_campaigns = campaigns

        campaigns_by_id = {c["id"]: c for c in filtered_campaigns}
        campaign_ids = list(campaigns_by_id.keys())

        d_start = date.fromisoformat(date_from)
        d_end = date.fromisoformat(date_to)
        all_rows = []

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
                    report = self._poll_report(uuid, timeout=max_wait)
                    if report:
                        link = report.get("link", "")
                        if link:
                            rows = self._download_csv(link, campaigns_by_id)
                            all_rows.extend(rows)
                            logger.info("Batch %d/%d: %d rows",
                                        i // batch_size + 1,
                                        (len(campaign_ids) + batch_size - 1) // batch_size,
                                        len(rows))
                    time.sleep(2)
                except Exception as e:
                    logger.warning("Batch %d failed: %s", i // batch_size + 1, e)

            d_start = chunk_end + timedelta(days=1)

        logger.info("Total ad stats: %d rows", len(all_rows))
        return all_rows