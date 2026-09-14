from __future__ import annotations

import unittest
from pathlib import Path

from lead_hub.amo import AmoGateway
from lead_hub.config import Config
from lead_hub.models import Lead


class FakeResponse:
    content = b"json"

    def raise_for_status(self):
        return None

    def json(self):
        return [{"id": 100, "contact_id": 200}]


class FakeSession:
    def __init__(self):
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return FakeResponse()


class AmoGatewayTest(unittest.TestCase):
    def test_payload_matches_sts_contract(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        session = FakeSession()
        created = AmoGateway(config, session).create(
            Lead("42", "Лист", 2, "+70000000000", "Сайт", "Источник A")
        )
        payload = session.calls[0][1]["json"][0]
        self.assertEqual(payload["pipeline_id"], 11105258)
        self.assertEqual(payload["status_id"], 87201098)
        self.assertEqual(payload["name"], "Источник A +70000000000")
        self.assertEqual(payload["custom_fields_values"][0]["field_id"], 1461977)
        self.assertEqual(
            payload["custom_fields_values"][0]["values"][0]["value"],
            "ID: 42\nКанал: Сайт\nИсточник: Источник A",
        )
        self.assertEqual(
            payload["_embedded"]["contacts"][0]["custom_fields_values"][0]["field_code"],
            "PHONE",
        )
        self.assertEqual(created.lead_id, "100")
        self.assertEqual(created.contact_id, "200")


if __name__ == "__main__":
    unittest.main()
