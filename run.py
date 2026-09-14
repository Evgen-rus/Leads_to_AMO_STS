from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from lead_hub.config import Config, ConfigError
from lead_hub.engine import build_engine
from lead_hub.storage import Storage


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Google Sheets -> SQLite -> amoCRM")
    parser.add_argument("--dry-run", action="store_true", help="Только прочитать и проверить, без любых записей")
    parser.add_argument("--limit", type=int, help="Максимум строк за запуск")
    args = parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        raise ConfigError("--limit должен быть положительным")

    load_dotenv(ROOT / ".env")
    config = Config.from_env(ROOT)
    db_path = Path(":memory:") if args.dry_run else config.db_path
    with Storage(db_path) as storage:
        stats = build_engine(config, storage).run(dry_run=args.dry_run, limit=args.limit)
    print(
        f"Просмотрено: {stats.scanned}; уже отмечено: {stats.skipped_marked}; "
        f"невалидно: {stats.skipped_invalid}; импортировано: {stats.imported}; "
        f"план: {stats.planned}; создано: {stats.created}; восстановлено: {stats.recovered}; "
        f"дублей: {stats.duplicates}; завершено: {stats.completed}; ошибок: {stats.failed}"
    )
    return 1 if stats.failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as error:
        print(f"Ошибка конфигурации: {error}", file=sys.stderr)
        sys.exit(2)
