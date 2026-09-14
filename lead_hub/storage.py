from __future__ import annotations

import sqlite3
from pathlib import Path

from lead_hub.models import CreatedLead, Lead


def normalize_phone(phone: str) -> str:
    return "".join(character for character in str(phone or "") if character.isdigit())


CREATE_LEADS_SQL = """
    CREATE TABLE {table} (
        source_id TEXT PRIMARY KEY,
        sheet_name TEXT NOT NULL,
        sheet_row INTEGER NOT NULL,
        phone TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        state TEXT NOT NULL DEFAULT 'pending'
            CHECK(state IN ('pending', 'created', 'completed', 'failed', 'duplicate')),
        amo_lead_id TEXT,
        amo_contact_id TEXT,
        amo_url TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        normalized_phone TEXT NOT NULL DEFAULT '',
        duplicate_of_source_id TEXT
    )
"""


class Storage:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode = WAL")
        if self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'leads'"
        ).fetchone():
            self._migrate()
        else:
            self.connection.execute(CREATE_LEADS_SQL.format(table="leads"))
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_normalized_phone ON leads(normalized_phone)"
        )
        self.connection.commit()

    def _migrate(self) -> None:
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(leads)")}
        table_sql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'leads'"
        ).fetchone()[0]
        if not ({"normalized_phone", "duplicate_of_source_id"} <= columns and "'duplicate'" in table_sql):
            normalized_phone = "normalized_phone" if "normalized_phone" in columns else "''"
            duplicate_of = "duplicate_of_source_id" if "duplicate_of_source_id" in columns else "NULL"
            with self.connection:
                self.connection.execute(CREATE_LEADS_SQL.format(table="leads_migrated"))
                self.connection.execute(
                    f"""
                    INSERT INTO leads_migrated(
                        source_id, sheet_name, sheet_row, phone, channel, source, state,
                        amo_lead_id, amo_contact_id, amo_url, attempts, last_error,
                        created_at, updated_at, normalized_phone, duplicate_of_source_id
                    )
                    SELECT source_id, sheet_name, sheet_row, phone, channel, source, state,
                           amo_lead_id, amo_contact_id, amo_url, attempts, last_error,
                           created_at, updated_at, {normalized_phone}, {duplicate_of}
                    FROM leads ORDER BY rowid
                    """
                )
                self.connection.execute("DROP TABLE leads")
                self.connection.execute("ALTER TABLE leads_migrated RENAME TO leads")

        rows = self.connection.execute(
            "SELECT source_id, phone FROM leads WHERE normalized_phone = ''"
        ).fetchall()
        self.connection.executemany(
            "UPDATE leads SET normalized_phone = ? WHERE source_id = ?",
            [(normalize_phone(row["phone"]), row["source_id"]) for row in rows],
        )

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
                      amo_lead_id, amo_contact_id, amo_url, attempts, last_error,
                      normalized_phone, duplicate_of_source_id
               FROM leads WHERE source_id = ?""",
            (source_id,),
        ).fetchone()
        return self._lead(row)

    def upsert(self, lead: Lead) -> Lead:
        self.connection.execute(
            """
            INSERT INTO leads(source_id, sheet_name, sheet_row, phone, channel, source, normalized_phone)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                sheet_name = excluded.sheet_name,
                sheet_row = excluded.sheet_row,
                phone = excluded.phone,
                channel = excluded.channel,
                source = excluded.source,
                normalized_phone = excluded.normalized_phone,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                lead.source_id, lead.sheet_name, lead.sheet_row, lead.phone,
                lead.channel, lead.source, normalize_phone(lead.phone),
            ),
        )
        self.connection.commit()
        saved = self.get(lead.source_id)
        if saved is None:
            raise RuntimeError("Не удалось сохранить строку в SQLite")
        return saved

    def phone_owner(self, normalized_phone: str) -> Lead | None:
        if not normalized_phone:
            return None
        row = self.connection.execute(
            """SELECT source_id, sheet_name, sheet_row, phone, channel, source, state,
                      amo_lead_id, amo_contact_id, amo_url, attempts, last_error,
                      normalized_phone, duplicate_of_source_id
               FROM leads
               WHERE normalized_phone = ? AND state != 'duplicate'
               ORDER BY rowid LIMIT 1""",
            (normalized_phone,),
        ).fetchone()
        return self._lead(row)

    def mark_duplicate(self, source_id: str, original_source_id: str) -> None:
        self.connection.execute(
            """UPDATE leads SET state = 'duplicate', duplicate_of_source_id = ?,
                      last_error = NULL, updated_at = CURRENT_TIMESTAMP
               WHERE source_id = ?""",
            (original_source_id, source_id),
        )
        self.connection.commit()

    def mark_attempt(self, source_id: str) -> None:
        self.connection.execute(
            "UPDATE leads SET attempts = attempts + 1, last_error = NULL, updated_at = CURRENT_TIMESTAMP WHERE source_id = ?",
            (source_id,),
        )
        self.connection.commit()

    def mark_created(self, source_id: str, created: CreatedLead) -> None:
        self.connection.execute(
            """UPDATE leads SET state = 'created', amo_lead_id = ?, amo_contact_id = ?,
                      amo_url = ?, duplicate_of_source_id = NULL, last_error = NULL,
                      updated_at = CURRENT_TIMESTAMP
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
