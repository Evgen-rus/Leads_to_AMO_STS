from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from lead_hub.config import Config
from lead_hub.google_sheets import find_header, row_value
from lead_hub.models import CreatedLead, Lead, SheetData
from lead_hub.storage import Storage

AMO_BATCH_SIZE = 40
AMO_CREATE_ATTEMPTS = 1


def result_cell(url: str, source: str | None = None) -> str:
    if source == "bd":
        return f"дубль в bd {url}"
    if source == "ama":
        return f"дубль в ama {url}"
    return url


@dataclass
class RunStats:
    scanned: int = 0
    skipped_marked: int = 0
    skipped_invalid: int = 0
    imported: int = 0
    planned: int = 0
    created: int = 0
    recovered: int = 0
    duplicates: int = 0
    completed: int = 0
    failed: int = 0


class Sheets(Protocol):
    def read(self, *, create_result_column: bool) -> SheetData: ...
    def write_result(self, sheet: SheetData, row_number: int, value: str) -> None: ...
    def flush(self) -> None: ...


class Amo(Protocol):
    def find(self, lead: Lead) -> CreatedLead | None: ...
    def create_many(self, leads: list[Lead]) -> dict[str, CreatedLead]: ...


class DeliveryEngine:
    def __init__(self, config: Config, storage: Storage, sheets: Sheets, amo: Amo, *, debug: bool = False):
        self.config = config
        self.storage = storage
        self.sheets = sheets
        self.amo = amo
        self.debug = debug

    def _log(self, message: str) -> None:
        print(message, flush=True)

    def _lead(self, sheet: SheetData, row: list[str], row_number: int, indexes: dict[str, int]) -> Lead:
        return Lead(
            source_id=row_value(row, indexes["source_id"]),
            sheet_name=sheet.sheet_name,
            sheet_row=row_number,
            phone=row_value(row, indexes["phone"]),
            channel=row_value(row, indexes.get("channel")),
            source=row_value(row, indexes["source"]),
        )

    def _complete(
        self, sheet: SheetData, lead: Lead, created: CreatedLead, stats: RunStats, *, source: str | None = None
    ) -> None:
        self.storage.mark_created(lead.source_id, created)
        self.sheets.write_result(sheet, lead.sheet_row, result_cell(created.url, source))
        self.storage.mark_completed(lead.source_id)
        stats.completed += 1

    def _fail(self, lead: Lead, error: Exception | str, stats: RunStats) -> None:
        current = self.storage.get(lead.source_id)
        if current and (current.amo_url or current.state == "duplicate"):
            self.storage.record_error(lead.source_id, str(error))
        else:
            self.storage.mark_failed(lead.source_id, str(error))
        stats.failed += 1

    def _create_batch(self, sheet: SheetData, batch: list[Lead], stats: RunStats) -> None:
        pending = batch
        last_error: Exception | str = "Не удалось создать сделки в amoCRM"
        for attempt in range(AMO_CREATE_ATTEMPTS):
            try:
                created = self.amo.create_many(pending)
            except Exception as error:
                last_error = error
                absent: list[Lead] = []
                for lead in pending:
                    try:
                        recovered = self.amo.find(lead)
                    except Exception as check_error:
                        self._fail(lead, f"{error}; не удалось проверить создание: {check_error}", stats)
                        continue
                    if recovered is None:
                        absent.append(lead)
                    else:
                        stats.recovered += 1
                        try:
                            self._complete(sheet, lead, recovered, stats, source="ama")
                        except Exception as write_error:
                            self._fail(lead, write_error, stats)
                pending = absent
                if not pending:
                    return
                if attempt + 1 < AMO_CREATE_ATTEMPTS:
                    continue
                for lead in pending:
                    self._fail(lead, last_error, stats)
                return

            stats.created += len(created)
            for lead in pending:
                try:
                    self._complete(sheet, lead, created[lead.source_id], stats)
                except Exception as error:
                    self._fail(lead, error, stats)
            return

    def run(self, *, dry_run: bool = False, limit: int | None = None) -> RunStats:
        try:
            return self._run_once(dry_run=dry_run, limit=limit)
        finally:
            self.sheets.flush()

    def _run_once(self, *, dry_run: bool = False, limit: int | None = None) -> RunStats:
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
            selected = stats.planned if dry_run else stats.imported
            if limit is not None and selected >= limit:
                break
            seen_source_ids.add(lead.source_id)
            if dry_run:
                self._log(
                    f"В работу: строка {lead.sheet_row}, ID {lead.source_id}, "
                    f"телефон {lead.phone}, канал {lead.channel}, источник {lead.source}"
                )
                leads.append(lead)
                stats.planned += 1
            else:
                saved = self.storage.upsert(lead)
                stats.imported += 1
                owner = self.storage.phone_owner(saved.normalized_phone)
                if owner and owner.source_id != saved.source_id:
                    self._log(
                        f"Строка {saved.sheet_row}, ID {saved.source_id}: дубль телефона, "
                        f"владелец {owner.source_id}. В amoCRM не отправляем."
                    )
                    self.storage.mark_duplicate(saved.source_id, owner.source_id)
                    stats.duplicates += 1
                    try:
                        self.sheets.write_result(
                            sheet, saved.sheet_row, f"Дубль — ID {owner.source_id}"
                        )
                    except Exception as error:
                        self._fail(saved, error, stats)
                    continue
                self._log(
                    f"В работу: строка {saved.sheet_row}, ID {saved.source_id}, "
                    f"телефон {saved.phone}, канал {saved.channel}, источник {saved.source}"
                )
                leads.append(saved)

        self._log(
            f"Проверка листа закончена. К отправке: {len(leads)}. "
            f"Уже отмечено: {stats.skipped_marked}. Невалидно: {stats.skipped_invalid}."
        )
        if dry_run:
            return stats

        missing_in_amo: list[Lead] = []
        for imported in leads:
            lead = self.storage.get(imported.source_id)
            if lead is None:
                stats.failed += 1
                continue
            try:
                if lead.amo_url:
                    self._log(
                        f"ID {lead.source_id} уже есть в локальной базе: {lead.amo_url}. "
                        "Новую сделку не создаём, записываем эту ссылку в таблицу."
                    )
                    self.sheets.write_result(sheet, lead.sheet_row, result_cell(lead.amo_url, "bd"))
                    self.storage.mark_completed(lead.source_id)
                    stats.completed += 1
                    continue
                self.storage.mark_attempt(lead.source_id)
                created = self.amo.find(lead)
                if created is None:
                    self._log(f"ID {lead.source_id} в amoCRM не найден, создаём сделку.")
                    missing_in_amo.append(lead)
                    continue
                self._log(f"ID {lead.source_id} найден в amoCRM, сделка {created.lead_id}. Новую не создаём.")
                stats.recovered += 1
                self._complete(sheet, lead, created, stats, source="ama")
            except Exception as error:
                self._fail(lead, error, stats)

        for start in range(0, len(missing_in_amo), AMO_BATCH_SIZE):
            self._create_batch(sheet, missing_in_amo[start:start + AMO_BATCH_SIZE], stats)
        return stats


def build_engine(config: Config, storage: Storage, *, debug: bool = False) -> DeliveryEngine:
    from lead_hub.amo import AmoGateway
    from lead_hub.google_sheets import GoogleSheetsGateway

    return DeliveryEngine(
        config,
        storage,
        GoogleSheetsGateway(config),
        AmoGateway(config, debug=debug),
        debug=debug,
    )
