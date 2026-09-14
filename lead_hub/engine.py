from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lead_hub.config import Config
from lead_hub.google_sheets import find_header, row_value
from lead_hub.models import CreatedLead, Lead, SheetData
from lead_hub.storage import Storage


@dataclass
class RunStats:
    scanned: int = 0
    skipped_marked: int = 0
    skipped_invalid: int = 0
    imported: int = 0
    planned: int = 0
    created: int = 0
    recovered: int = 0
    completed: int = 0
    failed: int = 0


class Sheets(Protocol):
    def read(self, *, create_result_column: bool) -> SheetData: ...
    def write_result(self, sheet: SheetData, row_number: int, value: str) -> None: ...


class Amo(Protocol):
    def find(self, lead: Lead) -> CreatedLead | None: ...
    def create(self, lead: Lead) -> CreatedLead: ...


class DeliveryEngine:
    def __init__(self, config: Config, storage: Storage, sheets: Sheets, amo: Amo):
        self.config = config
        self.storage = storage
        self.sheets = sheets
        self.amo = amo

    def _lead(self, sheet: SheetData, row: list[str], row_number: int, indexes: dict[str, int]) -> Lead:
        return Lead(
            source_id=row_value(row, indexes["source_id"]),
            sheet_name=sheet.sheet_name,
            sheet_row=row_number,
            phone=row_value(row, indexes["phone"]),
            channel=row_value(row, indexes.get("channel")),
            source=row_value(row, indexes["source"]),
        )

    def run(self, *, dry_run: bool = False, limit: int | None = None) -> RunStats:
        stats = RunStats()
        sheet = self.sheets.read(create_result_column=not dry_run)
        names = {
            "source_id": self.config.source_id_header,
            "phone": self.config.phone_header,
            "channel": self.config.channel_header,
            "source": self.config.source_header,
        }
        indexes = {key: find_header(sheet.headers, name) for key, name in names.items()}
        missing = [names[key] for key in ("source_id", "phone", "source") if indexes[key] is None]
        if missing:
            raise ValueError(f"В Google Sheets не найдены колонки: {', '.join(missing)}")
        resolved_indexes = {key: value for key, value in indexes.items() if value is not None}

        leads: list[Lead] = []
        seen_source_ids: set[str] = set()
        for row_number, row in enumerate(sheet.rows, start=2):
            stats.scanned += 1
            if row_value(row, sheet.result_column_index):
                stats.skipped_marked += 1
                continue
            lead = self._lead(sheet, row, row_number, resolved_indexes)
            if not lead.source_id or not lead.phone or not lead.source:
                stats.skipped_invalid += 1
                continue
            if lead.source_id in seen_source_ids:
                stats.skipped_invalid += 1
                continue
            if limit is not None and len(leads) >= limit:
                break
            seen_source_ids.add(lead.source_id)
            leads.append(lead)
            if dry_run:
                stats.planned += 1
            else:
                self.storage.upsert(lead)
                stats.imported += 1

        if dry_run:
            return stats

        for imported in leads:
            lead = self.storage.get(imported.source_id)
            if lead is None:
                stats.failed += 1
                continue
            try:
                if lead.amo_url:
                    self.sheets.write_result(sheet, lead.sheet_row, lead.amo_url)
                    self.storage.mark_completed(lead.source_id)
                    stats.completed += 1
                    continue
                self.storage.mark_attempt(lead.source_id)
                created = self.amo.find(lead)
                if created is None:
                    created = self.amo.create(lead)
                    stats.created += 1
                else:
                    stats.recovered += 1
                self.storage.mark_created(lead.source_id, created)
                self.sheets.write_result(sheet, lead.sheet_row, created.url)
                self.storage.mark_completed(lead.source_id)
                stats.completed += 1
            except Exception as error:
                current = self.storage.get(lead.source_id)
                if current and current.amo_url:
                    self.storage.record_error(lead.source_id, str(error))
                else:
                    self.storage.mark_failed(lead.source_id, str(error))
                stats.failed += 1
        return stats


def build_engine(config: Config, storage: Storage) -> DeliveryEngine:
    from lead_hub.amo import AmoGateway
    from lead_hub.google_sheets import GoogleSheetsGateway

    return DeliveryEngine(config, storage, GoogleSheetsGateway(config), AmoGateway(config))
