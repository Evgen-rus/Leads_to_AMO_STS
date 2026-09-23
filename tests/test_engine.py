from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lead_hub.config import Config
from lead_hub.engine import DeliveryEngine
from lead_hub.models import CreatedLead, Lead, SheetData
from lead_hub.storage import Storage


class FakeSheets:
    def __init__(self, fail_writes: int = 0, rows=None):
        self.data = SheetData(
            "sheet",
            "Лист",
            ["ID", "Номера", "Канал", "Источник", "Ссылка_AmoCRM"],
            rows or [
                ["1", "+70000000001", "Сайт", "Источник A", ""],
                ["2", "+70000000002", "", "B", "готово"],
            ],
            4,
        )
        self.fail_writes = fail_writes
        self.writes: list[tuple[int, str]] = []

    def read(self, *, create_result_column: bool):
        return self.data

    def write_result(self, sheet, row_number, value):
        if self.fail_writes:
            self.fail_writes -= 1
            raise RuntimeError("Google временно недоступен")
        self.writes.append((row_number, value))
        self.data.rows[row_number - 2][self.data.result_column_index] = value

    def flush(self):
        return None


class FakeAmo:
    def __init__(self):
        self.created = 0
        self.batches: list[list[str]] = []
        self.find_calls: list[str] = []

    def find(self, lead):
        self.find_calls.append(lead.source_id)
        return None

    def create_many(self, leads):
        self.batches.append([lead.source_id for lead in leads])
        self.created += len(leads)
        return {
            lead.source_id: CreatedLead(
                lead.source_id, None, f"https://example.amocrm.ru/leads/detail/{lead.source_id}"
            )
            for lead in leads
        }


class EngineTest(unittest.TestCase):
    def test_created_lead_is_saved_before_google_retry(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        sheets = FakeSheets(fail_writes=1)
        amo = FakeAmo()
        with tempfile.TemporaryDirectory() as directory, Storage(Path(directory) / "leads.sqlite3") as storage:
            first = DeliveryEngine(config, storage, sheets, amo).run()
            self.assertEqual(storage.get("1").state, "created")
            second = DeliveryEngine(config, storage, sheets, amo).run()
            self.assertEqual(storage.get("1").state, "completed")
        self.assertEqual(first.failed, 1)
        self.assertEqual(second.completed, 1)
        self.assertEqual(amo.created, 1)
        self.assertEqual(sheets.writes, [(2, "дубль в bd https://example.amocrm.ru/leads/detail/1")])

    def test_creates_leads_in_batches_of_40(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        rows = [[str(i), f"+7000000{i:04d}", "", "Источник", ""] for i in range(1, 42)]
        sheets = FakeSheets(rows=rows)
        amo = FakeAmo()
        with tempfile.TemporaryDirectory() as directory, Storage(Path(directory) / "leads.sqlite3") as storage:
            stats = DeliveryEngine(config, storage, sheets, amo).run()
        self.assertEqual(amo.batches, [[str(i) for i in range(1, 41)], ["41"]])
        self.assertEqual(stats.created, 41)
        self.assertEqual(stats.completed, 41)

    def test_after_ambiguous_error_does_not_repeat_post(self):
        class AmbiguousAmo(FakeAmo):
            def __init__(self):
                super().__init__()
                self.existing = {}

            def find(self, lead):
                return self.existing.get(lead.source_id)

            def create_many(self, leads):
                self.batches.append([lead.source_id for lead in leads])
                if len(self.batches) == 1:
                    lead = leads[0]
                    self.existing[lead.source_id] = CreatedLead(
                        lead.source_id, None, f"https://example.amocrm.ru/leads/detail/{lead.source_id}"
                    )
                    raise RuntimeError("Ответ потерян")
                self.created += len(leads)
                return {
                    lead.source_id: CreatedLead(
                        lead.source_id, None, f"https://example.amocrm.ru/leads/detail/{lead.source_id}"
                    )
                    for lead in leads
                }

        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        rows = [
            ["1", "+70000000001", "", "Источник", ""],
            ["2", "+70000000002", "", "Источник", ""],
        ]
        amo = AmbiguousAmo()
        with tempfile.TemporaryDirectory() as directory, Storage(Path(directory) / "leads.sqlite3") as storage:
            stats = DeliveryEngine(config, storage, FakeSheets(rows=rows), amo).run()
        self.assertEqual(amo.batches, [["1", "2"]])
        self.assertEqual(stats.recovered, 1)
        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.completed, 1)
        self.assertEqual(stats.failed, 1)

    def test_duplicate_from_previous_run_never_calls_amo(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        sheets = FakeSheets(rows=[["200", "7 900 000 00 01", "", "Источник", ""]])
        amo = FakeAmo()
        with tempfile.TemporaryDirectory() as directory, Storage(Path(directory) / "leads.sqlite3") as storage:
            storage.upsert(Lead("100", "Лист", 2, "+79000000001", "", "Источник"))
            storage.mark_created(
                "100", CreatedLead("amo-100", None, "https://example.amocrm.ru/leads/detail/amo-100")
            )
            storage.mark_completed("100")
            stats = DeliveryEngine(config, storage, sheets, amo).run()
            duplicate = storage.get("200")
            self.assertEqual(duplicate.state, "duplicate")
            self.assertEqual(duplicate.duplicate_of_source_id, "100")
        self.assertEqual(amo.find_calls, [])
        self.assertEqual(amo.batches, [])
        self.assertEqual(sheets.writes, [(2, "Дубль — ID 100")])
        self.assertEqual(stats.duplicates, 1)

    def test_duplicates_in_one_run_always_reference_first_original(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        rows = [
            ["100", "79001234567", "", "Источник", ""],
            ["200", "+79001234567", "", "Источник", ""],
            ["300", "7 900 123 45 67", "", "Источник", ""],
            ["400", "89001234567", "", "Источник", ""],
        ]
        sheets = FakeSheets(rows=rows)
        amo = FakeAmo()
        with tempfile.TemporaryDirectory() as directory, Storage(Path(directory) / "leads.sqlite3") as storage:
            stats = DeliveryEngine(config, storage, sheets, amo).run()
            self.assertEqual(storage.get("100").state, "completed")
            self.assertEqual(storage.get("200").duplicate_of_source_id, "100")
            self.assertEqual(storage.get("300").duplicate_of_source_id, "100")
            self.assertEqual(storage.get("400").state, "completed")
        self.assertEqual(amo.find_calls, ["100", "400"])
        self.assertEqual(amo.batches, [["100", "400"]])
        self.assertEqual(stats.created, 2)
        self.assertEqual(stats.duplicates, 2)
        self.assertIn((3, "Дубль — ID 100"), sheets.writes)
        self.assertIn((4, "Дубль — ID 100"), sheets.writes)

    def test_existing_sqlite_link_is_marked_in_the_same_cell(self):
        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        sheets = FakeSheets(rows=[["1", "+70000000001", "Сайт", "Источник A", ""]])
        amo = FakeAmo()
        with tempfile.TemporaryDirectory() as directory, Storage(Path(directory) / "leads.sqlite3") as storage:
            storage.upsert(Lead("1", "Лист", 2, "+70000000001", "Сайт", "Источник A"))
            storage.mark_created("1", CreatedLead("amo-1", None, "https://example.amocrm.ru/leads/detail/amo-1"))
            stats = DeliveryEngine(config, storage, sheets, amo).run()
        self.assertEqual(amo.find_calls, [])
        self.assertEqual(amo.batches, [])
        self.assertEqual(stats.completed, 1)
        self.assertEqual(sheets.writes, [(2, "дубль в bd https://example.amocrm.ru/leads/detail/amo-1")])

    def test_existing_amo_lead_is_marked_in_the_same_cell(self):
        class ExistingAmo(FakeAmo):
            def find(self, lead):
                self.find_calls.append(lead.source_id)
                return CreatedLead(lead.source_id, None, f"https://example.amocrm.ru/leads/detail/{lead.source_id}", recovered=True)

        config = Config(
            "sheet", "Лист", "credentials.json", "Ссылка_AmoCRM", Path("unused"),
            "token", "amocrm.ru", "example", "ID", "Номера", "Канал", "Источник",
        )
        sheets = FakeSheets(rows=[["1", "+70000000001", "Сайт", "Источник A", ""]])
        amo = ExistingAmo()
        with tempfile.TemporaryDirectory() as directory, Storage(Path(directory) / "leads.sqlite3") as storage:
            stats = DeliveryEngine(config, storage, sheets, amo).run()
        self.assertEqual(amo.batches, [])
        self.assertEqual(stats.recovered, 1)
        self.assertEqual(stats.created, 0)
        self.assertEqual(sheets.writes, [(2, "дубль в ama https://example.amocrm.ru/leads/detail/1")])


if __name__ == "__main__":
    unittest.main()
