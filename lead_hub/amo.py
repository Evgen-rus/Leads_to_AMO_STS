from __future__ import annotations

from typing import Any

import requests

from lead_hub.config import Config
from lead_hub.models import CreatedLead, Lead


class AmoGateway:
    def __init__(self, config: Config, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()

    def _url(self, path: str) -> str:
        return f"https://{self.config.amo_api_domain}.{self.config.amo_base_domain}/api/v4/{path}"

    @staticmethod
    def comment(lead: Lead) -> str:
        return f"ID: {lead.source_id}\nКанал: {lead.channel}\nИсточник: {lead.source}"

    def lead_url(self, lead_id: str) -> str:
        return f"https://{self.config.amo_api_domain}.{self.config.amo_base_domain}/leads/detail/{lead_id}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.session.request(
            method,
            self._url(path),
            headers={"Authorization": f"Bearer {self.config.amo_token}"},
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()
        return response.json() if response.content else {}

    def find(self, lead: Lead) -> CreatedLead | None:
        result = self._request("GET", "leads", params={"query": f"ID: {lead.source_id}", "limit": 250})
        for item in (result.get("_embedded", {}).get("leads", []) if isinstance(result, dict) else []):
            fields = item.get("custom_fields_values") or []
            values = next(
                (field.get("values") or [] for field in fields if field.get("field_id") == self.config.comment_field_id),
                [],
            )
            if any(str(value.get("value") or "") == self.comment(lead) for value in values):
                lead_id = str(item["id"])
                return CreatedLead(lead_id, None, self.lead_url(lead_id), recovered=True)
        return None

    def create(self, lead: Lead) -> CreatedLead:
        payload = {
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
        result = self._request("POST", "leads/complex", json=[payload])
        if not isinstance(result, list) or not result or not result[0].get("id"):
            raise RuntimeError("amoCRM не вернула ID созданной сделки")
        item = result[0]
        lead_id = str(item["id"])
        contact_id = str(item["contact_id"]) if item.get("contact_id") else None
        return CreatedLead(lead_id, contact_id, self.lead_url(lead_id))
