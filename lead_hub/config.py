from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    spreadsheet_id: str
    sheet_name: str
    credentials_file: str
    result_header: str
    db_path: Path
    amo_token: str
    amo_base_domain: str
    amo_api_domain: str
    source_id_header: str
    phone_header: str
    channel_header: str
    source_header: str
    pipeline_id: int = 11105258
    status_id: int = 87201098
    comment_field_id: int = 1461977

    @classmethod
    def from_env(cls, root: Path) -> "Config":
        required = {
            "SPREADSHEET_LR163": os.getenv("SPREADSHEET_LR163", "").strip(),
            "SHEET_LR163": os.getenv("SHEET_LR163", "").strip(),
            "GOOGLE_CREDENTIALS_FILE": os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip(),
            "AMO_TOKEN": os.getenv("AMO_TOKEN", "").strip(),
            "AMO_BASE_DOMAIN": os.getenv("AMO_BASE_DOMAIN", "").strip(),
            "AMO_API_DOMAIN": os.getenv("AMO_API_DOMAIN", "").strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigError(f"Не заполнены переменные окружения: {', '.join(missing)}")

        db_path = Path(os.getenv("LEADS_DB", "data/leads.sqlite3").strip())
        if not db_path.is_absolute():
            db_path = root / db_path

        credentials_file = Path(required["GOOGLE_CREDENTIALS_FILE"])
        if not credentials_file.is_absolute():
            credentials_file = root / credentials_file

        return cls(
            spreadsheet_id=required["SPREADSHEET_LR163"],
            sheet_name=required["SHEET_LR163"],
            credentials_file=str(credentials_file),
            result_header=os.getenv("RESULT_COLUMN_LR163", "Ссылка_AmoCRM").strip(),
            db_path=db_path,
            amo_token=required["AMO_TOKEN"],
            amo_base_domain=required["AMO_BASE_DOMAIN"],
            amo_api_domain=required["AMO_API_DOMAIN"],
            source_id_header=os.getenv("SOURCE_ID_COLUMN", "ID").strip(),
            phone_header=os.getenv("PHONE_COLUMN", "Номера").strip(),
            channel_header=os.getenv("CHANNEL_COLUMN", "Канал").strip(),
            source_header=os.getenv("SOURCE_COLUMN", "Источник").strip(),
        )
