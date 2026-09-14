from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from lead_hub.config import ConfigError
from lead_hub.google_sheets import column_to_a1, find_header, normalize_header, quote_sheet_name, row_value


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class TransferConfig:
    spreadsheet_id: str
    credentials_file: str
    source_sheet: str
    destination_sheet: str
    start_date: date
    not_called_workdays: int
    no_answer_workdays: int
    no_answer_statuses: frozenset[str]
    source_transferred_status: str
    destination_not_called_status: str
    destination_no_answer_status: str
    headers: dict[str, str]

    @classmethod
    def from_env(cls) -> "TransferConfig":
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ConfigError(f"Не заполнена переменная окружения {name}")
            return value

        def positive_int(name: str, default: str) -> int:
            try:
                value = int(os.getenv(name, default))
            except ValueError as error:
                raise ConfigError(f"{name} должна быть целым числом") from error
            if value < 1:
                raise ConfigError(f"{name} должна быть больше нуля")
            return value

        credentials = Path(required("GOOGLE_CREDENTIALS_FILE"))
        if not credentials.is_absolute():
            credentials = ROOT / credentials
        try:
            start_date = date.fromisoformat(os.getenv("TRANSFER_START_DATE", "2026-08-27").strip())
        except ValueError as error:
            raise ConfigError("TRANSFER_START_DATE должна иметь формат YYYY-MM-DD") from error

        statuses = frozenset(
            normalize_header(value)
            for value in os.getenv(
                "NO_ANSWER_STATUSES",
                "Недозвон|Автоответчик / помощник|Сброс / молчит|Не удалось связаться 9 раз",
            ).split("|")
            if value.strip()
        )
        if not statuses:
            raise ConfigError("NO_ANSWER_STATUSES не должна быть пустой")

        return cls(
            spreadsheet_id=required("SPREADSHEET_LR163"),
            credentials_file=str(credentials),
            source_sheet=os.getenv("SOURCE_SHEET_LR163", "Данные").strip(),
            destination_sheet=required("SHEET_LR163"),
            start_date=start_date,
            not_called_workdays=positive_int("NOT_CALLED_WORKDAYS", "3"),
            no_answer_workdays=positive_int("NO_ANSWER_WORKDAYS", "4"),
            no_answer_statuses=statuses,
            source_transferred_status=os.getenv(
                "SOURCE_TRANSFERRED_STATUS", "Передали AI — статус не менять"
            ).strip(),
            destination_not_called_status=os.getenv(
                "DEST_NOT_CALLED_STATUS", "Передали AI — менеджеры не звонили"
            ).strip(),
            destination_no_answer_status=os.getenv(
                "DEST_NO_ANSWER_STATUS", "Передали AI — недозвон"
            ).strip(),
            headers={
                "source_id": os.getenv("SOURCE_ID_COLUMN", "ID").strip(),
                "date": os.getenv("DATE_COLUMN", "Дата").strip(),
                "phone": os.getenv("PHONE_COLUMN", "Номера").strip(),
                "channel": os.getenv("CHANNEL_COLUMN", "Канал").strip(),
                "source": os.getenv("SOURCE_COLUMN", "Источник").strip(),
                "status": os.getenv(
                    "STATUS_COLUMN", "Статус (позвонил - выбери нужный)"
                ).strip(),
            },
        )


@dataclass(frozen=True)
class Transfer:
    source_row: int
    source_id: str
    values: list[str]
    already_in_destination: bool


@dataclass
class Plan:
    scanned: int = 0
    before_start: int = 0
    invalid: int = 0
    other_status: int = 0
    too_recent: int = 0
    duplicate_source_id: int = 0
    not_called: int = 0
    no_answer: int = 0
    already_in_destination: int = 0
    source_status_column_index: int = 0
    transfers: list[Transfer] = field(default_factory=list)


def parse_date(value: str) -> date | None:
    text = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def previous_workday(today: date, number: int) -> date:
    current = today
    found = 0
    while found < number:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            found += 1
    return current


def build_plan(
    config: TransferConfig,
    source_values: list[list[str]],
    destination_ids: set[str],
    *,
    today: date,
) -> Plan:
    if not source_values:
        raise ValueError(f"Лист {config.source_sheet!r} пуст")
    headers = [str(value) for value in source_values[0]]
    indexes = {key: find_header(headers, name) for key, name in config.headers.items()}
    missing = [config.headers[key] for key, index in indexes.items() if index is None]
    if missing:
        raise ValueError(f"Не найдены колонки источника: {', '.join(missing)}")
    resolved = {key: int(index) for key, index in indexes.items() if index is not None}
    no_call_cutoff = previous_workday(today, config.not_called_workdays)
    no_answer_cutoff = previous_workday(today, config.no_answer_workdays)
    plan = Plan()
    plan.source_status_column_index = resolved["status"]
    seen: set[str] = set()

    for row_number, row in enumerate(source_values[1:], start=2):
        plan.scanned += 1
        source_id = row_value(row, resolved["source_id"])
        event_date = parse_date(row_value(row, resolved["date"]))
        phone = row_value(row, resolved["phone"])
        if not source_id or not event_date or not phone:
            plan.invalid += 1
            continue
        if source_id in seen:
            plan.duplicate_source_id += 1
            continue
        seen.add(source_id)
        if event_date < config.start_date:
            plan.before_start += 1
            continue

        status = row_value(row, resolved["status"])
        normalized_status = normalize_header(status)
        destination_status = ""
        if not normalized_status:
            if event_date > no_call_cutoff:
                plan.too_recent += 1
                continue
            destination_status = config.destination_not_called_status
            plan.not_called += 1
        elif normalized_status in config.no_answer_statuses:
            if event_date > no_answer_cutoff:
                plan.too_recent += 1
                continue
            destination_status = config.destination_no_answer_status
            plan.no_answer += 1
        else:
            plan.other_status += 1
            continue

        already = source_id in destination_ids
        if already:
            plan.already_in_destination += 1
        plan.transfers.append(
            Transfer(
                source_row=row_number,
                source_id=source_id,
                values=[
                    source_id,
                    row_value(row, resolved["date"]),
                    phone,
                    row_value(row, resolved["channel"]),
                    row_value(row, resolved["source"]),
                    destination_status,
                ],
                already_in_destination=already,
            )
        )
    return plan


def sheets_service(config: TransferConfig, *, readonly: bool):
    scope = "https://www.googleapis.com/auth/spreadsheets.readonly" if readonly else "https://www.googleapis.com/auth/spreadsheets"
    credentials = service_account.Credentials.from_service_account_file(
        config.credentials_file, scopes=[scope]
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def read_inputs(service, config: TransferConfig) -> tuple[list[list[str]], set[str]]:
    metadata = service.spreadsheets().get(
        spreadsheetId=config.spreadsheet_id,
        fields="sheets.properties.title",
    ).execute()
    titles = {item["properties"]["title"] for item in metadata.get("sheets", [])}
    missing = {config.source_sheet, config.destination_sheet} - titles
    if missing:
        raise ValueError(f"Не найдены вкладки: {', '.join(sorted(missing))}")
    ranges = [
        f"{quote_sheet_name(config.source_sheet)}!A:F",
        f"{quote_sheet_name(config.destination_sheet)}!A:A",
    ]
    result = service.spreadsheets().values().batchGet(
        spreadsheetId=config.spreadsheet_id,
        ranges=ranges,
    ).execute()
    values = result.get("valueRanges", [])
    source_values = values[0].get("values", []) if values else []
    destination_values = values[1].get("values", []) if len(values) > 1 else []
    destination_ids = {
        str(row[0]).strip() for row in destination_values[1:] if row and str(row[0]).strip()
    }
    return source_values, destination_ids


def apply_plan(service, config: TransferConfig, plan: Plan) -> None:
    new_rows = [item.values for item in plan.transfers if not item.already_in_destination]
    if new_rows:
        service.spreadsheets().values().append(
            spreadsheetId=config.spreadsheet_id,
            range=f"{quote_sheet_name(config.destination_sheet)}!A:F",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": new_rows},
        ).execute()

    status_column = column_to_a1(plan.source_status_column_index)
    updates = [
        {
            "range": f"{quote_sheet_name(config.source_sheet)}!{status_column}{item.source_row}",
            "values": [[config.source_transferred_status]],
        }
        for item in plan.transfers
    ]
    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=config.spreadsheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Перенос строк менеджеров во вкладку AI")
    parser.add_argument("--dry-run", action="store_true", help="Только расчёт, без записей")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    config = TransferConfig.from_env()
    service = sheets_service(config, readonly=args.dry_run)
    source_values, destination_ids = read_inputs(service, config)
    plan = build_plan(config, source_values, destination_ids, today=date.today())
    if not args.dry_run:
        apply_plan(service, config, plan)

    new_count = len(plan.transfers) - plan.already_in_destination
    print(f"Режим: {'DRY RUN — без записей' if args.dry_run else 'ПЕРЕНОС'}")
    print(f"Проверено строк: {plan.scanned}")
    print(f"Подойдут 'менеджеры не звонили': {plan.not_called}")
    print(f"Подойдут 'недозвон': {plan.no_answer}")
    print(f"Уже есть во вкладке AI: {plan.already_in_destination}")
    print(f"Будет добавлено новых строк: {new_count}")
    print(f"Будет отмечено в исходной вкладке: {len(plan.transfers)}")
    print(
        f"Пропущено: до начальной даты={plan.before_start}, слишком новые={plan.too_recent}, "
        f"другие статусы={plan.other_status}, невалидные={plan.invalid}, "
        f"повторные ID источника={plan.duplicate_source_id}"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ConfigError, ValueError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        sys.exit(2)
