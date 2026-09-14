from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lead_hub.config import Config
from lead_hub.engine import DeliveryEngine
from lead_hub.models import CreatedLead, SheetData
from lead_hub.storage import Storage


class FakeSheets:
    def __init__(self, fail_writes: int = 0):
        self.data = SheetData(
            "sheet",
            "Лист",
            ["ID", "Номера", "Канал", "Источник", "Ссылка_AmoCRM"],
            [
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


class FakeAmo:
    def __init__(self):
        self.created = 0

    def find(self, lead):
        return None

    def create(self, lead):
        self.created += 1
        return CreatedLead("100", "200", "https://example.amocrm.ru/leads/detail/100")


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
        self.assertEqual(sheets.writes, [(2, "https://example.amocrm.ru/leads/detail/100")])


if __name__ == "__main__":
    unittest.main()
