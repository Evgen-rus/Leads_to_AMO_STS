from __future__ import annotations

import sqlite3
from pathlib import Path

from lead_hub.models import CreatedLead, Lead


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                source_id TEXT PRIMARY KEY,
                sheet_name TEXT NOT NULL,
                sheet_row INTEGER NOT NULL,
                phone TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'pending'
                    CHECK(state IN ('pending', 'created', 'completed', 'failed')),
                amo_lead_id TEXT,
                amo_contact_id TEXT,
                amo_url TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.connection.commit()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *_: object) -> None:
        self.connection.close()

    @staticmethod
    def _lead(row: sqlite3.Row | None) -> Lead | None:
        return Lead(**dict(row)) if row else None

    def get(self, source_id: str) -> Lead | None:
        row = self.connection.execute(
            """SELECT source_id, sheet_name, sheet_row, phone, channel, source, state,
                      amo_lead_id, amo_contact_id, amo_url, attempts, last_error
               FROM leads WHERE source_id = ?""",
            (source_id,),
        ).fetchone()
        return self._lead(row)

    def upsert(self, lead: Lead) -> Lead:
        self.connection.execute(
            """
            INSERT INTO leads(source_id, sheet_name, sheet_row, phone, channel, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                sheet_name = excluded.sheet_name,
                sheet_row = excluded.sheet_row,
                phone = excluded.phone,
                channel = excluded.channel,
                source = excluded.source,
                updated_at = CURRENT_TIMESTAMP
            """,
            (lead.source_id, lead.sheet_name, lead.sheet_row, lead.phone, lead.channel, lead.source),
        )
        self.connection.commit()
        saved = self.get(lead.source_id)
        if saved is None:
            raise RuntimeError("Не удалось сохранить строку в SQLite")
        return saved

    def mark_attempt(self, source_id: str) -> None:
        self.connection.execute(
            "UPDATE leads SET attempts = attempts + 1, last_error = NULL, updated_at = CURRENT_TIMESTAMP WHERE source_id = ?",
            (source_id,),
        )
        self.connection.commit()

    def mark_created(self, source_id: str, created: CreatedLead) -> None:
        self.connection.execute(
            """UPDATE leads SET state = 'created', amo_lead_id = ?, amo_contact_id = ?,
                      amo_url = ?, last_error = NULL, updated_at = CURRENT_TIMESTAMP
               WHERE source_id = ?""",
            (created.lead_id, created.contact_id, created.url, source_id),
        )
        self.connection.commit()

    def mark_completed(self, source_id: str) -> None:
        self.connection.execute(
            "UPDATE leads SET state = 'completed', last_error = NULL, updated_at = CURRENT_TIMESTAMP WHERE source_id = ?",
            (source_id,),
        )
        self.connection.commit()

    def mark_failed(self, source_id: str, error: str) -> None:
        self.connection.execute(
            "UPDATE leads SET state = 'failed', last_error = ?, updated_at = CURRENT_TIMESTAMP WHERE source_id = ?",
            (error[:1000], source_id),
        )
        self.connection.commit()

    def record_error(self, source_id: str, error: str) -> None:
        self.connection.execute(
            "UPDATE leads SET last_error = ?, updated_at = CURRENT_TIMESTAMP WHERE source_id = ?",
            (error[:1000], source_id),
        )
        self.connection.commit()
