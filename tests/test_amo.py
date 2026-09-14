from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from lead_hub.amo import AmoGateway
from lead_hub.config import Config
from lead_hub.models import Lead


class FakeResponse:
    content = b"json"

    def __init__(self, status_code=200, headers=None, result=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.result = result or [{"id": 100, "contact_id": 200, "request_id": ["sts:42"]}]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)

    def json(self):
        return self.result


class FakeSession:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.responses.pop(0) if self.responses else FakeResponse()


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

    def test_batch_results_are_matched_by_request_id(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        session = FakeSession()
        session.request = lambda *args, **kwargs: FakeResponse(
            result=[
                {"id": 2, "request_id": ["sts:2"]},
                {"id": 1, "request_id": ["sts:1"]},
            ]
        )
        leads = [
            Lead("1", "Лист", 2, "+70000000001", "", "A"),
            Lead("2", "Лист", 3, "+70000000002", "", "B"),
        ]
        created = AmoGateway(config, session).create_many(leads)
        self.assertEqual(created["1"].lead_id, "1")
        self.assertEqual(created["2"].lead_id, "2")

    def test_find_accepts_amo_whitespace_normalization(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        lead = Lead("42", "Лист", 2, "+70000000000", "Сайт", "Источник A")
        result = {
            "_embedded": {
                "leads": [
                    {
                        "id": 100,
                        "custom_fields_values": [
                            {
                                "field_id": 1461977,
                                "values": [{"value": "ID: 42 Канал: Сайт Источник: Источник A"}],
                            }
                        ],
                    }
                ]
            }
        }
        found = AmoGateway(config, FakeSession([FakeResponse(result=result)])).find(lead)
        self.assertEqual(found.lead_id, "100")

    def test_find_refuses_ambiguous_duplicates(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        lead = Lead("42", "Лист", 2, "+70000000000", "Сайт", "Источник A")
        item = {
            "custom_fields_values": [
                {
                    "field_id": 1461977,
                    "values": [{"value": "ID: 42 Канал: Сайт Источник: Источник A"}],
                }
            ]
        }
        result = {"_embedded": {"leads": [{"id": 100, **item}, {"id": 101, **item}]}}
        with self.assertRaisesRegex(RuntimeError, "несколько сделок"):
            AmoGateway(config, FakeSession([FakeResponse(result=result)])).find(lead)

    def test_limits_all_requests_to_five_per_second(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        gateway = AmoGateway(config, FakeSession())
        with patch("lead_hub.amo.time.monotonic", side_effect=[0.0, 0.05, 0.2]), patch(
            "lead_hub.amo.time.sleep"
        ) as sleep:
            gateway.create(Lead("42", "Лист", 2, "+70000000000", "", "A"))
            gateway.create(Lead("42", "Лист", 2, "+70000000000", "", "A"))
        self.assertAlmostEqual(sleep.call_args.args[0], 0.15)

    def test_429_uses_retry_after(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        session = FakeSession([FakeResponse(429, {"Retry-After": "2"}), FakeResponse()])
        gateway = AmoGateway(config, session)
        with patch.object(gateway, "_wait_for_rate_limit"), patch("lead_hub.amo.time.sleep") as sleep:
            gateway.create(Lead("42", "Лист", 2, "+70000000000", "", "A"))
        sleep.assert_called_once_with(2.0)
        self.assertEqual(len(session.calls), 2)

    def test_post_timeout_is_not_retried_in_gateway(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        session = FakeSession()
        session.request = lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("timeout"))
        gateway = AmoGateway(config, session)
        with patch.object(gateway, "_wait_for_rate_limit"):
            with self.assertRaises(requests.Timeout):
                gateway.create(Lead("42", "Лист", 2, "+70000000000", "", "A"))


if __name__ == "__main__":
    unittest.main()
