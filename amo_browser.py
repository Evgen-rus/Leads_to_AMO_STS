from __future__ import annotations

import argparse
import contextvars
import json
import logging
import logging.handlers
import mimetypes
import re
import shutil
import sqlite3
import threading
import time as clock
import uuid
import zipfile
from datetime import date, datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from lead_hub.amo import AmoGateway
from lead_hub.config import Config, ConfigError

ROOT = Path(__file__).resolve().parent
EXPORT_ROOT = ROOT / "data" / "amo_exports"
AUDIO_ROOT = ROOT / "data" / "research_audio"
WEB_ROOT = ROOT / "amo_browser_web"
AUDIO_HOST = "sound.lptracker.ru"
AUDIO_MAX_BYTES = 32 * 1024 * 1024
AUDIO_NOTE_LIMIT = 250
TRANSCRIPTION_FIELD_ID = 1550725
MATERIALS = {"data", "history", "comments", "transcription", "audio"}
LOCAL_TZ = timezone(timedelta(hours=7))
OPERATION_ID = contextvars.ContextVar("amo_browser_operation_id", default="-")
LOGGER = logging.getLogger("amo_browser")


def configure_logging(root: Path = ROOT) -> None:
    if LOGGER.handlers:
        return
    log_path = root / "logs" / "amo_browser.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s op=%(operation)s %(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False


class LoggingSession(requests.Session):
    def reset_attempts(self) -> None:
        self._last_key = None

    def request(self, method, url, **kwargs):
        started = clock.monotonic()
        path = urlparse(url).path
        method = str(method).upper()
        params = kwargs.get("params") or {}
        request_key = (method, path, str(params.get("page", "")))
        if getattr(self, "_last_key", None) == request_key:
            self._attempt += 1
        else:
            self._last_key, self._attempt = request_key, 1
        try:
            response = super().request(method, url, **kwargs)
        except requests.RequestException:
            LOGGER.warning("crm method=%s path=%s attempt=%d elapsed_ms=%d network_error=1", method, path, self._attempt, int((clock.monotonic() - started) * 1000), extra={"operation": OPERATION_ID.get()})
            raise
        LOGGER.info("crm method=%s path=%s attempt=%d status=%d elapsed_ms=%d", method, path, self._attempt, response.status_code, int((clock.monotonic() - started) * 1000), extra={"operation": OPERATION_ID.get()})
        return response


class BrowserError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def request_error_message(error: requests.RequestException) -> tuple[int, str]:
    status = getattr(getattr(error, "response", None), "status_code", 0)
    if status == 429:
        return 429, "amoCRM временно ограничила частоту запросов; подождите и повторите"
    if status in {401, 403}:
        return status, "amoCRM отказала в доступе; проверьте настройки доступа"
    return 502, "Не удалось получить данные amoCRM"


def valid_id(value: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]{0,17}", str(value or "")):
        raise BrowserError("Некорректный ID")
    return int(value)


def parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise BrowserError("Дата должна быть в формате YYYY-MM-DD") from error


def date_filters(created_from: str, created_to: str) -> dict[str, int]:
    start, end = parse_date(created_from), parse_date(created_to)
    if start and end and start > end:
        raise BrowserError("Начальная дата позже конечной")
    filters: dict[str, int] = {}
    if start:
        filters["filter[created_at][from]"] = int(datetime.combine(start, time.min, LOCAL_TZ).timestamp())
    if end:
        try:
            exclusive = datetime.combine(end + timedelta(days=1), time.min, LOCAL_TZ)
        except OverflowError as error:
            raise BrowserError("Дата вне допустимого диапазона") from error
        filters["filter[created_at][to]"] = int(exclusive.timestamp()) - 1
    return filters


def pages(client, path: str, params: dict | None = None, limit: int = 250) -> list[dict]:
    page, out = 1, []
    while True:
        query = {**(params or {}), "limit": limit, "page": page}
        result = client.get(path, query)
        key = "notes" if path.endswith("/notes") else path if path in {"events", "tasks"} else "items"
        items = result.get("_embedded", {}).get(key, [])
        out.extend(items)
        if len(items) < limit:
            return out
        page += 1


def extract_source(lead: dict, field_id: int) -> dict | None:
    for field in lead.get("custom_fields_values") or []:
        if str(field.get("field_id")) != str(field_id):
            continue
        for item in field.get("values") or []:
            raw = str(item.get("value") or "")
            values = {}
            for label, value in re.findall(r"\b(ID|Канал|Источник)\s*:\s*(.*?)(?=\s+(?:ID|Канал|Источник)\s*:|$)", raw, re.I):
                values[label.lower()] = value.strip()
            if all(values.get(key) for key in ("id", "канал", "источник")):
                return {"id": values["id"], "channel": values["канал"], "name": values["источник"]}
    return None


def load_local_sources(db_path: Path) -> tuple[dict[str, dict], set[str]]:
    by_lead, source_ids = {}, set()
    if not db_path.is_file():
        return by_lead, source_ids
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(leads)")}
            if not {"amo_lead_id", "source_id", "channel", "source"} <= columns:
                return by_lead, source_ids
            for lead_id, source_id, channel, source in db.execute(
                "SELECT amo_lead_id, source_id, channel, source FROM leads WHERE amo_lead_id IS NOT NULL"
            ):
                by_lead[str(lead_id)] = {"id": str(source_id), "channel": channel or "", "name": source or ""}
                source_ids.add(str(lead_id))
    except (sqlite3.Error, OSError):
        return {}, set()
    return by_lead, source_ids


class AmoReadClient:
    """Read-only facade over this project's rate-limited and authenticated gateway."""
    def __init__(self, gateway: AmoGateway):
        self.gateway = gateway
        self.lock = threading.Lock()

    def get(self, path: str, params: dict | None = None) -> dict:
        # ponytail: one CRM request at a time; keep shared Session safe and existing 5 rps limit authoritative.
        with self.lock:
            reset_attempts = getattr(getattr(self.gateway, "session", None), "reset_attempts", None)
            if reset_attempts:
                reset_attempts()
            result = self.gateway._request("GET", path, params=params or {})
        if not isinstance(result, dict):
            raise BrowserError("amoCRM вернула неожиданный ответ", 502)
        return result


class AmoBrowser:
    def __init__(self, client, config: Config, root: Path = ROOT, audio_session=None):
        self.client, self.config, self.root = client, config, root
        self.export_root = root / "data" / "amo_exports"
        self.audio_root = root / "data" / "research_audio"
        self.audio_session = audio_session or requests.Session()
        self._users: dict[str, str | None] = {}

    def _source(self, lead: dict) -> tuple[dict | None, bool]:
        saved, ours = load_local_sources(self.config.db_path)
        return saved.get(str(lead.get("id"))) or extract_source(lead, self.config.comment_field_id), str(lead.get("id")) in ours

    def pipelines(self) -> dict:
        result = self.client.get("leads/pipelines", {"limit": 250, "page": 1})
        items = [item for item in result.get("_embedded", {}).get("pipelines", []) if not item.get("is_archive", False)]
        return {"items": items, "delivery_pipeline_id": self.config.pipeline_id, "delivery_status_id": self.config.status_id}

    def leads(self, query: dict[str, list[str]]) -> dict:
        pipeline_id = valid_id(query.get("pipeline_id", [""])[0])
        pipelines = self.pipelines()["items"]
        pipeline = next((item for item in pipelines if str(item.get("id")) == str(pipeline_id)), None)
        if not pipeline:
            raise BrowserError("Воронка не найдена")
        page_value = query.get("page", ["1"])[0]
        if not re.fullmatch(r"[1-9][0-9]{0,5}", str(page_value)):
            raise BrowserError("Некорректный номер страницы")
        page = int(page_value)
        if page < 1 or page > 100000:
            raise BrowserError("Некорректный номер страницы")
        dates = date_filters(query.get("created_from", [""])[0], query.get("created_to", [""])[0])
        filters: dict = {"filter[pipeline_id]": pipeline_id, **dates}
        statuses = query.get("status_ids", [""])[0]
        if statuses:
            ids = list(dict.fromkeys(valid_id(part) for part in statuses.split(",") if part))
            if not ids or len(ids) > 100:
                raise BrowserError("Некорректный список этапов")
            stage_rows = pipeline.get("_embedded", {}).get("statuses", pipeline.get("statuses", []))
            if not stage_rows:
                stage_rows = self.client.get(f"leads/pipelines/{pipeline_id}/statuses", {"limit": 250, "page": 1}).get("_embedded", {}).get("statuses", [])
            available = {str(item.get("id")) for item in stage_rows}
            if any(str(status_id) not in available for status_id in ids):
                raise BrowserError("Этап не принадлежит выбранной воронке")
            for index, status_id in enumerate(ids):
                filters[f"filter[statuses][{index}][pipeline_id]"] = pipeline_id
                filters[f"filter[statuses][{index}][status_id]"] = status_id
        search = query.get("query", [""])[0].strip()
        if len(search) > 100:
            raise BrowserError("Поисковый запрос слишком длинный")
        if search:
            filters["query"] = search
        ours_only = query.get("ours", ["0"])[0] == "1"
        scan_page = page
        rows: list[dict] = []
        if ours_only:
            # ponytail: at most 20 CRM pages per request; the cursor advances so no scanned rows are silently skipped.
            for _ in range(20):
                result = self.client.get("leads", {**filters, "limit": 10, "page": scan_page})
                batch = result.get("_embedded", {}).get("leads", [])
                local, saved = load_local_sources(self.config.db_path)
                rows.extend(row for row in batch if str(row.get("id")) in saved or extract_source(row, self.config.comment_field_id))
                scan_page += 1
                if len(batch) < 10:
                    return self._lead_page(rows, None, page)
                if rows:
                    return self._lead_page(rows, scan_page, page)
            return self._lead_page(rows, scan_page, page)
        result = self.client.get("leads", {**filters, "limit": 10, "page": page})
        rows = result.get("_embedded", {}).get("leads", [])
        links = result.get("_links", {})
        more = bool(links["next"].get("href")) if "next" in links else len(rows) == 10
        return self._lead_page(rows, page + 1 if more else None, page)

    def _lead_page(self, rows: list[dict], next_page: int | None, page: int) -> dict:
        local, _ = load_local_sources(self.config.db_path)
        items = []
        for lead in rows:
            source = local.get(str(lead.get("id"))) or extract_source(lead, self.config.comment_field_id)
            items.append({**lead, "source": source, "is_ours": bool(source)})
        return {"items": items, "next_page": next_page, "page": page}

    def lead_detail(self, lead_id: str) -> dict:
        lead_id = str(valid_id(lead_id))
        response = self.client.get(f"leads/{lead_id}")
        lead = response.get("lead", response)
        if not lead:
            raise BrowserError("Сделка не найдена", 404)
        notes, events, tasks, warnings = self._read_related(lead_id)
        source, is_ours = self._source(lead)
        responsible_name = self._responsible_name(lead)
        if lead.get("responsible_user_id") and not responsible_name:
            warnings.append("Не удалось получить имя ответственного")
        return {"lead": lead, "notes": notes, "events": events, "tasks": tasks, "source": source,
                "is_ours": is_ours or bool(source), "responsible_name": responsible_name, "warnings": warnings}

    def _read_related(self, lead_id: str) -> tuple[list[dict], list[dict], list[dict], list[str]]:
        collections = []
        warnings = []
        specs = [
            (f"leads/{lead_id}/notes", None, AUDIO_NOTE_LIMIT, "Примечания"),
            ("events", {"filter[entity]": "lead", "filter[entity_id][]": lead_id}, 100, "История событий"),
            ("tasks", {"filter[entity_id]": lead_id, "filter[entity_type]": "leads"}, 250, "Задачи"),
        ]
        for path, params, limit, label in specs:
            try:
                rows = pages(self.client, path, params, limit=limit)
                if path == "events":
                    rows = [item for item in rows if str(item.get("entity_id")) == lead_id and item.get("entity_type", "lead") == "lead"]
                elif path == "tasks":
                    rows = [item for item in rows if str(item.get("entity_id")) == lead_id]
                collections.append(rows)
            except (BrowserError, requests.RequestException, RuntimeError) as error:
                collections.append([])
                warnings.append(f"{label}: {request_error_message(error)[1] if isinstance(error, requests.RequestException) else 'не удалось получить данные'}")
        return collections[0], collections[1], collections[2], warnings

    def _responsible_name(self, lead: dict) -> str | None:
        user_id = lead.get("responsible_user_id")
        if not user_id:
            return None
        if str(user_id) not in self._users:
            try:
                self._users[str(user_id)] = self.client.get(f"users/{valid_id(str(user_id))}").get("name")
            except (BrowserError, requests.RequestException, RuntimeError) as error:
                self._users[str(user_id)] = None
        return self._users[str(user_id)]

    def _audio_url(self, lead_id: str, note_id: str) -> str:
        valid_id(note_id)
        for page in range(1, 10):
            result = self.client.get(f"leads/{lead_id}/notes", {"limit": 250, "page": page})
            notes = result.get("_embedded", {}).get("notes", [])
            for note in notes:
                if str(note.get("id")) == note_id:
                    link = (note.get("params") or {}).get("link", "")
                    parsed = urlparse(link)
                    try:
                        safe_port = parsed.port in {None, 443}
                    except ValueError:
                        safe_port = False
                    if parsed.scheme != "https" or parsed.hostname != AUDIO_HOST or not safe_port or parsed.username or parsed.password:
                        raise BrowserError("Ссылка на аудио не прошла проверку", 400)
                    return link
            if len(notes) < 250:
                break
        raise BrowserError("Ссылка на запись не найдена", 404)

    def audio(self, lead_id: str, note_id: str) -> Path:
        valid_id(lead_id)
        valid_id(note_id)
        cached = self.audio_root / f"{lead_id}_{note_id}.wav"
        if cached.is_file():
            return cached
        url = self._audio_url(lead_id, note_id)
        for attempt in range(4):
            started = clock.monotonic()
            response = None
            try:
                response = self.audio_session.get(url, timeout=30, stream=True, allow_redirects=False)
                LOGGER.info("audio method=GET host=%s status=%d attempt=%d elapsed_ms=%d", AUDIO_HOST, response.status_code, attempt + 1, int((clock.monotonic() - started) * 1000), extra={"operation": OPERATION_ID.get()})
                if response.status_code == 429 or response.status_code in {502, 503, 504}:
                    if attempt < 3:
                        delay = AmoGateway._retry_after(response) if response.status_code == 429 else None
                        response.close()
                        clock.sleep(delay if delay is not None else AmoGateway._backoff(attempt))
                        continue
                response_url = urlparse(response.url)
                try:
                    response_port_ok = response_url.port in {None, 443}
                except ValueError:
                    response_port_ok = False
                if response.is_redirect or response.status_code != 200 or response_url.scheme != "https" or response_url.hostname != AUDIO_HOST or not response_port_ok:
                    raise BrowserError("Не удалось безопасно получить аудиозапись", 502)
                size = int(response.headers.get("Content-Length", "0") or 0)
                if size > AUDIO_MAX_BYTES:
                    raise BrowserError("Аудиозапись превышает лимит 32 МБ", 413)
                self.audio_root.mkdir(parents=True, exist_ok=True)
                temp = cached.with_suffix(".tmp")
                count = 0
                with temp.open("wb") as stream:
                    for chunk in response.iter_content(64 * 1024):
                        count += len(chunk)
                        if count > AUDIO_MAX_BYTES:
                            raise BrowserError("Аудиозапись превышает лимит 32 МБ", 413)
                        stream.write(chunk)
                temp.replace(cached)
                return cached
            except requests.RequestException:
                LOGGER.warning("audio method=GET host=%s attempt=%d elapsed_ms=%d network_error=1", AUDIO_HOST, attempt + 1, int((clock.monotonic() - started) * 1000), extra={"operation": OPERATION_ID.get()})
                if attempt == 3:
                    raise
                clock.sleep(AmoGateway._backoff(attempt))
            finally:
                if response is not None:
                    response.close()
        raise BrowserError("Не удалось безопасно получить аудиозапись", 502)

    def export(self, body: dict) -> dict:
        ids = body.get("lead_ids")
        materials = body.get("materials")
        if not isinstance(ids, list) or not ids or len(ids) > 10 or len(set(map(str, ids))) != len(ids):
            raise BrowserError("Выберите от 1 до 10 уникальных сделок")
        ids = [str(valid_id(str(item))) for item in ids]
        if not isinstance(materials, list) or not materials or any(not isinstance(item, str) for item in materials) or set(materials) - MATERIALS:
            raise BrowserError("Некорректный список материалов")
        pipelines = self.pipelines()["items"]
        pipe_names = {str(p.get("id")): p.get("name", "Воронка") for p in pipelines}
        status_names = {str(status.get("id")): status.get("name", "") for pipe in pipelines
                        for status in pipe.get("_embedded", {}).get("statuses", pipe.get("statuses", []))}
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        run_dir = self.export_root / run_id
        warnings: list[str] = []
        count = 0
        for lead_id in ids:
            try:
                detail = self.lead_detail(lead_id)
                lead = detail["lead"]
                pipe_id = str(lead.get("pipeline_id", "unknown"))
                deal_dir = run_dir / f"{safe_name(pipe_names.get(pipe_id, 'Воронка'))}_{pipe_id}" / f"{safe_name(lead.get('name', 'Сделка'))}_{lead_id}"
                deal_dir.mkdir(parents=True, exist_ok=True)
                if "data" in materials:
                    (deal_dir / "lead.json").write_text(json.dumps(lead, ensure_ascii=False, indent=2), encoding="utf-8")
                    (deal_dir / "summary.txt").write_text(readable_summary(lead, detail, pipe_names, status_names), encoding="utf-8")
                if "history" in materials:
                    if not detail["events"]:
                        warnings.append(f"Сделка {lead_id}: история событий пуста")
                    (deal_dir / "events.json").write_text(json.dumps(detail["events"], ensure_ascii=False, indent=2), encoding="utf-8")
                    (deal_dir / "history.txt").write_text(readable_history(detail["events"], status_names), encoding="utf-8")
                if "comments" in materials:
                    if not detail["notes"]:
                        warnings.append(f"Сделка {lead_id}: комментарии и примечания не найдены")
                    (deal_dir / "comments.json").write_text(json.dumps(detail["notes"], ensure_ascii=False, indent=2), encoding="utf-8")
                    (deal_dir / "comments.txt").write_text(readable_comments(detail["notes"]), encoding="utf-8")
                if "transcription" in materials:
                    transcript = transcription(lead, detail["notes"])
                    if transcript:
                        (deal_dir / "transcription.txt").write_text(transcript, encoding="utf-8")
                    else:
                        warnings.append(f"Сделка {lead_id}: транскрибация не найдена")
                if "audio" in materials:
                    audio_notes = [note for note in detail["notes"] if (note.get("params") or {}).get("link")]
                    for audio_note in audio_notes:
                        try:
                            shutil.copy2(self.audio(lead_id, str(audio_note["id"])), deal_dir / f"audio_{audio_note['id']}.wav")
                        except (BrowserError, requests.RequestException, OSError):
                            warnings.append(f"Сделка {lead_id}: не удалось выгрузить аудио note {audio_note.get('id')}")
                    if not audio_notes:
                        warnings.append(f"Сделка {lead_id}: аудио не найдено")
                count += 1
                warnings.extend(f"Сделка {lead_id}: {warning}" for warning in detail.get("warnings", []))
                LOGGER.info("export lead_id=%s files=%d", lead_id, sum(1 for path in deal_dir.iterdir() if path.is_file()), extra={"operation": OPERATION_ID.get()})
            except (BrowserError, requests.RequestException, OSError) as error:
                warning = request_error_message(error)[1] if isinstance(error, requests.RequestException) else "не удалось получить данные"
                warnings.append(f"Сделка {lead_id}: {warning}")
                error_dir = run_dir / "errors"
                error_dir.mkdir(parents=True, exist_ok=True)
                (error_dir / f"{lead_id}.txt").write_text("Не удалось получить сделку. Повторите выгрузку или проверьте доступ.\n", encoding="utf-8")
        self.export_root.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "manifest.json").write_text(json.dumps({"lead_ids": ids, "materials": materials, "exported_count": count, "warnings": warnings}, ensure_ascii=False, indent=2), encoding="utf-8")
        zip_path = self.export_root / f"{run_id}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in run_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(run_dir))
        for warning in warnings:
            LOGGER.warning("export_warning %s", warning[:240], extra={"operation": OPERATION_ID.get()})
        LOGGER.info("export_complete lead_ids=%s material_count=%d exported_count=%d files=%d warnings=%d", ",".join(ids), len(materials), count, sum(1 for path in run_dir.rglob("*") if path.is_file()), len(warnings), extra={"operation": OPERATION_ID.get()})
        return {"download_url": f"/api/download?name={quote(zip_path.name)}", "folder": str(run_dir),
                "exported_count": count, "warnings": warnings}

    def download_path(self, name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.zip", name):
            raise BrowserError("Некорректное имя архива")
        path = self.export_root / name
        if not path.is_file():
            raise BrowserError("Архив не найден", 404)
        return path


def safe_name(value: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", str(value or "")).strip(" .")
    return (value or "Без названия")[:60]


def transcription(lead: dict, notes: list[dict]) -> str:
    result = []
    for field in lead.get("custom_fields_values") or []:
        label = field.get("field_name") or field.get("name") or field.get("label") or ""
        if str(field.get("field_id")) == str(TRANSCRIPTION_FIELD_ID) or "транскриб" in str(label).lower():
            result.extend(str(value.get("value") or "") for value in field.get("values") or [])
    for note in notes:
        params = note.get("params") or {}
        if any(word in str(note.get("note_type", "")).lower() + " " + str(params.get("text", "")).lower() for word in ("транскриб", "расшифров", "диалог")):
            result.append(str(params.get("text") or params.get("text_transcription") or ""))
    return "\n\n".join(value for value in result if value.strip())


def display_time(value: object) -> str:
    try:
        return datetime.fromtimestamp(int(value), LOCAL_TZ).strftime("%d.%m.%Y %H:%M:%S")
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value or "")


def readable_summary(lead: dict, detail: dict, pipeline_names: dict[str, str], status_names: dict[str, str]) -> str:
    pipeline_id, status_id = str(lead.get("pipeline_id", "")), str(lead.get("status_id", ""))
    lines = [f"Сделка: {lead.get('name', '')}", f"ID: {lead.get('id', '')}",
             f"Воронка: {pipeline_names.get(pipeline_id, 'ID ' + pipeline_id)}",
             f"Этап: {status_names.get(status_id, 'ID ' + status_id)}",
             f"Создана: {display_time(lead.get('created_at'))}", f"Обновлена: {display_time(lead.get('updated_at'))}",
             f"Ответственный: {detail.get('responsible_name') or lead.get('responsible_user_id', '')}",
             f"Наша сделка: {'да' if detail.get('is_ours') else 'нет'}"]
    source = detail.get("source")
    if source:
        lines.extend([f"Исходный ID: {source.get('id', '')}", f"Канал: {source.get('channel', '')}", f"Источник: {source.get('name', '')}"])
    return "\n".join(lines) + "\n"


def readable_history(events: list[dict], status_names: dict[str, str]) -> str:
    lines = []
    for event in events:
        before, after = [], []
        for field, output in (("value_before", before), ("value_after", after)):
            for change in event.get(field) or []:
                candidates = change.get("lead_status") if isinstance(change, dict) else None
                if candidates is None and isinstance(change, dict):
                    candidates = change.get("status_id")
                candidates = candidates if isinstance(candidates, list) else [candidates]
                for value in candidates:
                    if isinstance(value, dict):
                        status_id = value.get("id", value.get("status_id"))
                        label = value.get("name") or status_names.get(str(status_id), "")
                        if label:
                            output.append(str(label))
                    elif value is not None:
                        output.append(status_names.get(str(value), str(value)))
        change_text = ""
        if before or after:
            change_text = f" — {', '.join(before) or '—'} → {', '.join(after) or '—'}"
        lines.append(f"{display_time(event.get('created_at'))}: {event.get('type', 'Событие')}{change_text}")
    return "\n".join(lines) + ("\n" if lines else "История событий пуста.\n")


def readable_comments(notes: list[dict]) -> str:
    parts = []
    for note in notes:
        params = note.get("params") or {}
        text = params.get("text") or params.get("text_transcription") or params.get("description") or ""
        parts.append(f"{display_time(note.get('created_at'))} [{note.get('note_type', 'примечание')}]\n{text}".rstrip())
    return "\n\n".join(parts) + ("\n" if parts else "Примечания отсутствуют.\n")


class LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(BaseHTTPRequestHandler):
    server_version = "AmoBrowser/1.0"
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> AmoBrowser:
        return self.server.app  # type: ignore[attr-defined]

    def _guard(self) -> None:
        host = self.headers.get("Host", "")
        if host not in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}:
            raise BrowserError("Доступ только с этого компьютера", 403)
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            try:
                origin_port = parsed.port
            except ValueError as error:
                raise BrowserError("Некорректный источник запроса", 403) from error
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or origin_port != self.server.server_port:
                raise BrowserError("Запрос с другого источника отклонён", 403)

    def _send(self, status: int, data: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, body: object) -> None:
        if status >= 400:
            message = body.get("error", "request_failed") if isinstance(body, dict) else "request_failed"
            LOGGER.warning("http_response status=%d error=%s", status, str(message)[:200], extra={"operation": OPERATION_ID.get()})
        else:
            LOGGER.info("http_response status=%d", status, extra={"operation": OPERATION_ID.get()})
        self._send(status, json.dumps(body, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        token = OPERATION_ID.set(uuid.uuid4().hex[:12])
        started = clock.monotonic()
        LOGGER.info("request_start method=GET path=%s", urlparse(self.path).path, extra={"operation": OPERATION_ID.get()})
        try:
            self._guard()
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            path = parsed.path
            if path == "/api/pipelines":
                return self._json(200, self.app.pipelines())
            if path == "/api/leads":
                return self._json(200, self.app.leads(query))
            match = re.fullmatch(r"/api/leads/([0-9]+)", path)
            if match:
                return self._json(200, self.app.lead_detail(match.group(1)))
            if path == "/api/audio":
                audio_path = self.app.audio(query.get("lead_id", [""])[0], query.get("note_id", [""])[0])
                return self._file(audio_path, "audio/wav", range_support=True)
            if path == "/api/download":
                archive = self.app.download_path(query.get("name", [""])[0])
                return self._file(archive, "application/zip", attachment=archive.name)
            if path == "/" or path in {"/index.html", "/app.js", "/styles.css"}:
                file_path = WEB_ROOT / ("index.html" if path == "/" else path.lstrip("/"))
                if not file_path.is_file():
                    raise BrowserError("Интерфейс ещё не собран", 404)
                content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
                return self._file(file_path, content_type)
            raise BrowserError("Маршрут не найден", 404)
        except BrowserError as error:
            self._json(error.status, {"error": str(error)})
        except requests.RequestException as error:
            # Never return exception bodies: HTTP errors may include credentials or remote data.
            status, message = request_error_message(error)
            self._json(status, {"error": message})
        except (RuntimeError, OSError):
            self._json(502, {"error": "Не удалось получить данные amoCRM"})
        finally:
            LOGGER.info("request method=GET path=%s elapsed_ms=%d", urlparse(self.path).path, int((clock.monotonic() - started) * 1000), extra={"operation": OPERATION_ID.get()})
            OPERATION_ID.reset(token)

    def do_POST(self) -> None:
        token = OPERATION_ID.set(uuid.uuid4().hex[:12])
        started = clock.monotonic()
        LOGGER.info("request_start method=POST path=%s", urlparse(self.path).path, extra={"operation": OPERATION_ID.get()})
        try:
            self._guard()
            if urlparse(self.path).path != "/api/export":
                raise BrowserError("Маршрут не найден", 404)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise BrowserError("Некорректный размер запроса") from error
            if length <= 0 or length > 64 * 1024:
                raise BrowserError("Некорректный размер запроса")
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise BrowserError("Ожидается JSON")
            if not isinstance(body, dict):
                raise BrowserError("Ожидается JSON-объект")
            return self._json(200, self.app.export(body))
        except BrowserError as error:
            self._json(error.status, {"error": str(error)})
        except requests.RequestException as error:
            status, message = request_error_message(error)
            self._json(status, {"error": message})
        except (RuntimeError, OSError):
            self._json(502, {"error": "Не удалось завершить выгрузку"})
        finally:
            LOGGER.info("request method=POST path=%s elapsed_ms=%d", urlparse(self.path).path, int((clock.monotonic() - started) * 1000), extra={"operation": OPERATION_ID.get()})
            OPERATION_ID.reset(token)

    def _file(self, path: Path, content_type: str, attachment: str | None = None, range_support: bool = False) -> None:
        size = path.stat().st_size
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get("Range") if range_support else None
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match or not any(match.groups()):
                return self._json(416, {"error": "Некорректный диапазон"})
            first, last = match.groups()
            if first:
                start = int(first)
                end = int(last) if last else size - 1
            else:
                suffix = int(last)
                start = max(0, size - suffix)
            if start >= size or end < start:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            status = 206
        with path.open("rb") as stream:
            stream.seek(start)
            data = stream.read(end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if status == 206:
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:
        # Request URLs can contain customer identifiers; keep local console quiet.
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Локальный просмотр сделок amoCRM, только чтение")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true", help="Открыть локальную страницу после запуска сервера")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port должен быть от 1 до 65535")
    load_dotenv(ROOT / ".env")
    config = Config.from_env(ROOT)
    configure_logging()
    session = LoggingSession()
    app = AmoBrowser(AmoReadClient(AmoGateway(config, session)), config)
    server = LoopbackServer(("127.0.0.1", args.port), Handler)
    server.app = app
    url = f"http://127.0.0.1:{args.port}"
    print(f"Откройте {url}")
    LOGGER.info("server_started address=127.0.0.1 port=%d account=%s rps=5 retry_attempts=4 timezone=UTC+07:00", args.port, config.amo_api_domain, extra={"operation": "startup"})
    if args.open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
