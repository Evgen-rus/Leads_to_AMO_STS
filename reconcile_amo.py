from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from lead_hub.amo import AmoGateway
from lead_hub.config import Config, ConfigError
from lead_hub.google_sheets import GoogleSheetsGateway, row_value
from lead_hub.models import CreatedLead
from lead_hub.storage import Storage


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Безопасная сверка SQLite с amoCRM")
    parser.add_argument("--apply", action="store_true", help="Восстановить однозначные ссылки")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    config = Config.from_env(ROOT)
    sheets = GoogleSheetsGateway(config)
    amo = AmoGateway(config)
    sheet = sheets.read(create_result_column=False)
    missing = unique = duplicates = restored = marked = failed = 0
    duplicate_rows: list[list[str | int]] = []

    with Storage(config.db_path) as storage:
        leads = storage.unfinished()
        earliest = storage.earliest_unfinished_created_at()
        if not earliest:
            print("Незавершённых записей нет")
            return 0
        created_from = int(
            (datetime.fromisoformat(earliest).replace(tzinfo=timezone.utc) - timedelta(hours=1)).timestamp()
        )
        amo_rows = amo.list_created_since(created_from)
        by_comment: dict[str, list] = {}
        for item in amo_rows:
            values = next(
                (
                    field.get("values") or []
                    for field in item.get("custom_fields_values") or []
                    if field.get("field_id") == config.comment_field_id
                ),
                [],
            )
            for value in values:
                comment = amo.normalize_comment(value.get("value"))
                if comment:
                    lead_id = str(item["id"])
                    by_comment.setdefault(comment, []).append(
                        (lead_id, amo.lead_url(lead_id))
                    )
        for index, lead in enumerate(leads, start=1):
            try:
                matches = by_comment.get(amo.normalize_comment(amo.comment(lead)), [])
                if not matches:
                    missing += 1
                    continue
                if len(matches) > 1:
                    duplicates += 1
                    duplicate_rows.append(
                        [lead.source_id, len(matches), ",".join(item[0] for item in matches),
                         ",".join(item[1] for item in matches)]
                    )
                    continue
                unique += 1
                if not args.apply:
                    continue
                created = CreatedLead(matches[0][0], None, matches[0][1], recovered=True)
                storage.mark_created(lead.source_id, created)
                sheet_row = sheet.rows[lead.sheet_row - 2] if 2 <= lead.sheet_row <= len(sheet.rows) + 1 else []
                if row_value(sheet_row, sheet.result_column_index):
                    marked += 1
                else:
                    sheets.write_result(sheet, lead.sheet_row, created.url)
                    restored += 1
                storage.mark_completed(lead.source_id)
            except Exception as error:
                failed += 1
                if args.apply:
                    storage.record_error(lead.source_id, str(error))
            if index % 25 == 0:
                print(f"Проверено {index}/{len(leads)}", flush=True)
        sheets.flush()

    report = ROOT / "data" / "amo_duplicate_audit.csv"
    if duplicate_rows:
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.writer(file, delimiter=";")
            writer.writerow(["source_id", "count", "amo_lead_ids", "amo_urls"])
            writer.writerows(duplicate_rows)

    print(
        f"Сверено: {len(leads)}; не найдено: {missing}; одна сделка: {unique}; "
        f"с дублями: {duplicates}; восстановлено ссылок: {restored}; "
        f"уже отмечено в Google: {marked}; ошибок проверки: {failed}"
    )
    if duplicate_rows:
        print(f"Отчёт по дублям: {report}")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as error:
        print(f"Ошибка конфигурации: {error}", file=sys.stderr)
        sys.exit(2)
