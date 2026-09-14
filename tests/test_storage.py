from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from lead_hub.models import Lead
from lead_hub.storage import Storage, normalize_phone


class StorageTest(unittest.TestCase):
    def test_normalize_phone_keeps_only_digits(self):
        self.assertEqual(normalize_phone("+7 900 123-45-67"), "79001234567")
        self.assertEqual(normalize_phone("89001234567"), "89001234567")

    def test_migrates_old_database_without_losing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE leads (
                    source_id TEXT PRIMARY KEY, sheet_name TEXT NOT NULL, sheet_row INTEGER NOT NULL,
                    phone TEXT NOT NULL, channel TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK(state IN ('pending', 'created', 'completed', 'failed')),
                    amo_lead_id TEXT, amo_contact_id TEXT, amo_url TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            connection.execute(
                """INSERT INTO leads(source_id, sheet_name, sheet_row, phone, source, state, amo_url)
                   VALUES ('100', 'Лист', 2, '+7 900 000-00-01', 'Источник', 'completed', 'https://amo/100')"""
            )
            connection.commit()
            connection.close()

            with Storage(path) as storage:
                saved = storage.get("100")
                self.assertEqual(saved.normalized_phone, "79000000001")
                self.assertEqual(saved.amo_url, "https://amo/100")
                storage.upsert(Lead("200", "Лист", 3, "79000000001", "", "Источник"))
                storage.mark_duplicate("200", "100")
                self.assertEqual(storage.get("200").state, "duplicate")


if __name__ == "__main__":
    unittest.main()
