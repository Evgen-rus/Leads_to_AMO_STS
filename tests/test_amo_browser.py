from __future__ import annotations

import io
import logging
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import requests

from amo_browser import AmoBrowser, AmoReadClient, BrowserError, Handler, LoopbackServer, LoggingSession, OPERATION_ID, LOGGER, date_filters, extract_source


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, params or {}))
        if path == "leads/pipelines":
            return {"_embedded": {"pipelines": [
                {"id": 10, "name": "Покупка/Новостройки", "_embedded": {"statuses": [{"id": 20, "name": "Новый"}]}},
                {"id": 11, "name": "Архив", "is_archive": True},
            ]}}
        if path == "leads/pipelines/10/statuses":
            return {"_embedded": {"statuses": [{"id": 20}]}}
        if path == "leads":
            return {"_embedded": {"leads": [{"id": 77, "name": "Сделка: 1", "pipeline_id": 10, "status_id": 20}]}}
        if path == "leads/77":
            return {"id": 77, "name": "Сделка: 1", "pipeline_id": 10, "status_id": 20, "responsible_user_id": 1, "created_at": 1, "custom_fields_values": []}
        if path == "users/1":
            return {"id": 1, "name": "Тестовый ответственный"}
        if path == "leads/77/notes":
            return {"_embedded": {"notes": [{"id": 9, "note_type": "common", "params": {"text": "Комментарий"}}]}}
        if path == "events":
            return {"_embedded": {"events": [{"id": "ev1", "entity_id": 77, "entity_type": "lead", "type": "lead_status_changed"}]}}
        if path == "tasks":
            return {"_embedded": {"tasks": [{"id": 1, "entity_id": 77}]}}
        raise AssertionError(f"unexpected GET {path}")


class AmoBrowserTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = SimpleNamespace(db_path=self.root / "absent.sqlite", pipeline_id=10, status_id=20, comment_field_id=1461977)
        self.client = FakeClient()
        self.app = AmoBrowser(self.client, self.config, self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_date_filters_are_inclusive_novosibirsk_dates(self):
        got = date_filters("2026-10-01", "2026-10-01")
        self.assertEqual(set(got), {"filter[created_at][from]", "filter[created_at][to]"})
        self.assertEqual(got["filter[created_at][to]"] - got["filter[created_at][from]"], 86399)
        with self.assertRaises(BrowserError):
            date_filters("2026-10-02", "2026-10-01")

    def test_pipeline_status_filters_and_page_size(self):
        self.assertEqual([item["id"] for item in self.app.pipelines()["items"]], [10])
        response = self.app.leads({"pipeline_id": ["10"], "status_ids": ["20"], "page": ["2"]})
        self.assertEqual(response["items"][0]["id"], 77)
        path, params = next(call for call in self.client.calls if call[0] == "leads")
        self.assertEqual((path, params["limit"], params["page"]), ("leads", 10, 2))
        self.assertEqual(params["filter[statuses][0][pipeline_id]"], 10)
        self.assertEqual(params["filter[statuses][0][status_id]"], 20)
        with self.assertRaises(BrowserError):
            self.app.leads({"pipeline_id": ["10"], "status_ids": ["999"]})

    def test_source_comment_format_is_explicit(self):
        lead = {"custom_fields_values": [{"field_id": "1461977", "values": [{"value": "ID: 1800108546 Канал: B Источник: Эталон"}]}]}
        self.assertEqual(extract_source(lead, 1461977), {"id": "1800108546", "channel": "B", "name": "Эталон"})
        multiline = {"custom_fields_values": [{"field_id": 1461977, "values": [{"value": "ID: 52\nКанал: B\nИсточник: Эталон"}]}]}
        self.assertEqual(extract_source(multiline, 1461977), {"id": "52", "channel": "B", "name": "Эталон"})
        partial = {"custom_fields_values": [{"field_id": 1461977, "values": [{"value": "ID: 52"}]}]}
        self.assertIsNone(extract_source(partial, 1461977))
        self.assertIsNone(extract_source({"tags": [{"name": "парсер"}]}, 1461977))

    def test_detail_reads_endpoint_specific_embedded_collections(self):
        detail = self.app.lead_detail("77")
        self.assertEqual([n["id"] for n in detail["notes"]], [9])
        self.assertEqual([event["id"] for event in detail["events"]], ["ev1"])
        self.assertEqual([task["id"] for task in detail["tasks"]], [1])
        self.assertEqual(detail["responsible_name"], "Тестовый ответственный")

    def test_export_contains_only_selected_materials_and_safe_paths(self):
        result = self.app.export({"lead_ids": ["77"], "materials": ["data", "comments"]})
        archive = self.app.download_path(Path(result["download_url"].split("name=")[1]).name)
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        self.assertTrue(any(name.endswith("/lead.json") for name in names))
        self.assertTrue(any(name.endswith("/comments.json") for name in names))
        self.assertFalse(any("events" in name or "transcription" in name or "audio" in name for name in names))
        self.assertIn("Сделка_ 1_77", "\n".join(names))
        with self.assertRaises(BrowserError):
            self.app.download_path("..\\outside.zip")
        with self.assertRaises(BrowserError):
            self.app.export({"lead_ids": ["77", "77"], "materials": ["data"]})

    def test_audio_export_keeps_every_recording(self):
        self.app.lead_detail = lambda lead_id: {
            "lead": {"id": 77, "name": "A deal", "pipeline_id": 10, "status_id": 20},
            "notes": [{"id": 9, "params": {"link": "x"}}, {"id": 10, "params": {"link": "y"}}],
            "events": [], "tasks": [], "source": None, "is_ours": False, "warnings": [],
        }
        audio_files = []
        for number in range(2):
            path = self.root / f"source{number}.wav"
            path.write_bytes(b"audio")
            audio_files.append(path)
        with patch.object(self.app, "audio", side_effect=audio_files):
            result = self.app.export({"lead_ids": ["77"], "materials": ["audio"]})
        archive = self.app.download_path(Path(result["download_url"].split("name=")[1]).name)
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
        self.assertTrue(any(name.endswith("audio_9.wav") for name in names))
        self.assertTrue(any(name.endswith("audio_10.wav") for name in names))

    def test_request_logs_path_and_status_but_not_query_or_body(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        previous_level = LOGGER.level
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        token = OPERATION_ID.set("test-op")
        try:
            with patch.object(requests.Session, "request", return_value=SimpleNamespace(status_code=200)):
                LoggingSession().request("GET", "https://example.test/api/v4/leads?phone=private")
        finally:
            OPERATION_ID.reset(token)
            LOGGER.removeHandler(handler)
            LOGGER.setLevel(previous_level)
        self.assertIn("/api/v4/leads", output.getvalue())
        self.assertIn("status=200", output.getvalue())
        self.assertNotIn("private", output.getvalue())

    def test_gateway_facade_never_uses_non_get_methods(self):
        class Gateway:
            def __init__(self): self.calls = []
            def _request(self, method, path, **kwargs):
                self.calls.append(method)
                return {}
        gateway = Gateway()
        AmoReadClient(gateway).get("leads")
        self.assertEqual(gateway.calls, ["GET"])

    def test_loopback_host_guard_rejects_untrusted_host(self):
        server = LoopbackServer(("127.0.0.1", 0), Handler)
        server.app = self.app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(f"http://127.0.0.1:{server.server_port}/api/pipelines", headers={"Host": "evil.example"})
            with self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 403)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
