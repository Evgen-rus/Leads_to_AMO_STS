from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from lead_hub.config import Config
from lead_hub.models import CreatedLead, Lead

AMO_REQUESTS_PER_SECOND = 5.0
AMO_MIN_REQUEST_INTERVAL = 1 / AMO_REQUESTS_PER_SECOND
AMO_RETRY_MAX_ATTEMPTS = 4
AMO_RETRYABLE_GET_STATUSES = {429, 502, 503, 504}


class AmoGateway:
    def __init__(self, config: Config, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()
        self._last_request_at: float | None = None
        self._rate_lock = threading.Lock()

    def _url(self, path: str) -> str:
        return f"https://{self.config.amo_api_domain}.{self.config.amo_base_domain}/api/v4/{path}"

    @staticmethod
    def comment(lead: Lead) -> str:
        return f"ID: {lead.source_id}\nКанал: {lead.channel}\nИсточник: {lead.source}"

    @staticmethod
    def normalize_comment(value: str) -> str:
        return " ".join(str(value or "").split())

    def lead_url(self, lead_id: str) -> str:
        return f"https://{self.config.amo_api_domain}.{self.config.amo_base_domain}/leads/detail/{lead_id}"

    def _wait_for_rate_limit(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            if self._last_request_at is not None:
                delay = AMO_MIN_REQUEST_INTERVAL - (now - self._last_request_at)
                if delay > 0:
                    time.sleep(delay)
                    now = time.monotonic()
            self._last_request_at = now

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                return None

    @staticmethod
    def _backoff(attempt: int) -> float:
        return 2 ** attempt + random.uniform(0, 0.25)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        method = method.upper()
        for attempt in range(AMO_RETRY_MAX_ATTEMPTS):
            self._wait_for_rate_limit()
            try:
                response = self.session.request(
                    method,
                    self._url(path),
                    headers={"Authorization": f"Bearer {self.config.amo_token}"},
                    timeout=30,
                    **kwargs,
                )
            except requests.RequestException:
                if method != "GET" or attempt + 1 >= AMO_RETRY_MAX_ATTEMPTS:
                    raise
                time.sleep(self._backoff(attempt))
                continue

            retryable = response.status_code == 429 or (
                method == "GET" and response.status_code in AMO_RETRYABLE_GET_STATUSES
            )
            if retryable and attempt + 1 < AMO_RETRY_MAX_ATTEMPTS:
                delay = self._retry_after(response) if response.status_code == 429 else None
                time.sleep(delay if delay is not None else self._backoff(attempt))
                continue

            response.raise_for_status()
            return response.json() if response.content else {}
        raise RuntimeError("Исчерпаны попытки запроса к amoCRM")

    def find_all(self, lead: Lead) -> list[CreatedLead]:
        result = self._request("GET", "leads", params={"query": lead.source_id, "limit": 250})
        matches: list[CreatedLead] = []
        for item in (result.get("_embedded", {}).get("leads", []) if isinstance(result, dict) else []):
            fields = item.get("custom_fields_values") or []
            values = next(
                (field.get("values") or [] for field in fields if field.get("field_id") == self.config.comment_field_id),
                [],
            )
            expected = self.normalize_comment(self.comment(lead))
            if any(self.normalize_comment(value.get("value")) == expected for value in values):
                lead_id = str(item["id"])
                matches.append(CreatedLead(lead_id, None, self.lead_url(lead_id), recovered=True))
        return matches

    def find(self, lead: Lead) -> CreatedLead | None:
        matches = self.find_all(lead)
        if len(matches) > 1:
            raise RuntimeError(
                f"В amoCRM найдено несколько сделок для исходного ID {lead.source_id}"
            )
        return matches[0] if matches else None

    def list_created_since(self, created_from: int) -> list[dict[str, Any]]:
        leads: list[dict[str, Any]] = []
        page = 1
        while True:
            result = self._request(
                "GET",
                "leads",
                params={
                    "filter[created_at][from]": created_from,
                    "limit": 250,
                    "page": page,
                },
            )
            rows = result.get("_embedded", {}).get("leads", []) if isinstance(result, dict) else []
            leads.extend(rows)
            if len(rows) < 250:
                return leads
            page += 1

    def _payload(self, lead: Lead) -> dict[str, Any]:
        return {
            "name": f"{lead.source} {lead.phone}".strip(),
            "pipeline_id": self.config.pipeline_id,
            "status_id": self.config.status_id,
            "request_id": f"sts:{lead.source_id}",
            "custom_fields_values": [
                {"field_id": self.config.comment_field_id, "values": [{"value": self.comment(lead)}]}
            ],
            "_embedded": {
                "contacts": [
                    {
                        "name": f"Контакт {lead.phone}",
                        "custom_fields_values": [
                            {
                                "field_code": "PHONE",
                                "values": [{"value": lead.phone, "enum_code": "WORK"}],
                            }
                        ],
                    }
                ]
            },
        }

    def create_many(self, leads: list[Lead]) -> dict[str, CreatedLead]:
        result = self._request("POST", "leads/complex", json=[self._payload(lead) for lead in leads])
        if not isinstance(result, list):
            raise RuntimeError("amoCRM не вернула список созданных сделок")
        by_request_id: dict[str, dict[str, Any]] = {}
        for item in result:
            request_ids = item.get("request_id")
            if not isinstance(request_ids, list):
                request_ids = [request_ids]
            for request_id in request_ids:
                by_request_id[str(request_id)] = item
        created: dict[str, CreatedLead] = {}
        for lead in leads:
            item = by_request_id.get(f"sts:{lead.source_id}")
            if not item or not item.get("id"):
                raise RuntimeError(f"amoCRM не вернула созданную сделку для ID {lead.source_id}")
            lead_id = str(item["id"])
            contact_id = str(item["contact_id"]) if item.get("contact_id") else None
            created[lead.source_id] = CreatedLead(lead_id, contact_id, self.lead_url(lead_id))
        return created

    def create(self, lead: Lead) -> CreatedLead:
        return self.create_many([lead])[lead.source_id]
