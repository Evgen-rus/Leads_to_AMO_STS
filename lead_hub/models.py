from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SheetData:
    spreadsheet_id: str
    sheet_name: str
    headers: list[str]
    rows: list[list[str]]
    result_column_index: int


@dataclass(frozen=True)
class Lead:
    source_id: str
    sheet_name: str
    sheet_row: int
    phone: str
    channel: str
    source: str
    state: str = "pending"
    amo_lead_id: str | None = None
    amo_contact_id: str | None = None
    amo_url: str | None = None
    attempts: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class CreatedLead:
    lead_id: str
    contact_id: str | None
    url: str
    recovered: bool = False
