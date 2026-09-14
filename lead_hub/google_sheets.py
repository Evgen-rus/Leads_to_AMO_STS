from __future__ import annotations

import re
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from lead_hub.config import Config
from lead_hub.models import SheetData


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def normalize_header(value: str) -> str:
    return re.sub(r"[_\s]+", " ", str(value or "").strip().lower().replace("ё", "е"))


def find_header(headers: list[str], name: str) -> int | None:
    wanted = normalize_header(name)
    return next((index for index, header in enumerate(headers) if normalize_header(header) == wanted), None)


def row_value(row: list[str], index: int | None) -> str:
    return str(row[index]).strip() if index is not None and index < len(row) else ""


def column_to_a1(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def quote_sheet_name(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


class GoogleSheetsGateway:
    def __init__(self, config: Config, service: Any | None = None):
        self.config = config
        self._provided_service = service
        self._cached_service: Any | None = None

    def _service(self) -> Any:
        if self._provided_service is not None:
            return self._provided_service
        if self._cached_service is None:
            credentials = service_account.Credentials.from_service_account_file(
                self.config.credentials_file, scopes=SCOPES
            )
            self._cached_service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return self._cached_service

    def read(self, *, create_result_column: bool) -> SheetData:
        service = self._service()
        result = service.spreadsheets().values().get(
            spreadsheetId=self.config.spreadsheet_id,
            range=quote_sheet_name(self.config.sheet_name),
        ).execute()
        values = result.get("values") or []
        headers = [str(value) for value in (values[0] if values else [])]
        rows = [[str(value) for value in row] for row in values[1:]] if values else []
        result_index = find_header(headers, self.config.result_header)
        if result_index is None:
            result_index = len(headers)
            if create_result_column:
                cell = f"{column_to_a1(result_index)}1"
                service.spreadsheets().values().update(
                    spreadsheetId=self.config.spreadsheet_id,
                    range=f"{quote_sheet_name(self.config.sheet_name)}!{cell}",
                    valueInputOption="RAW",
                    body={"values": [[self.config.result_header]]},
                ).execute()
            headers.append(self.config.result_header)
        return SheetData(self.config.spreadsheet_id, self.config.sheet_name, headers, rows, result_index)

    def write_result(self, sheet: SheetData, row_number: int, value: str) -> None:
        cell = f"{column_to_a1(sheet.result_column_index)}{row_number}"
        self._service().spreadsheets().values().update(
            spreadsheetId=sheet.spreadsheet_id,
            range=f"{quote_sheet_name(sheet.sheet_name)}!{cell}",
            valueInputOption="RAW",
            body={"values": [[value]]},
        ).execute()
