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
        by_request_id = {str(item.get("request_id")): item for item in result}
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
