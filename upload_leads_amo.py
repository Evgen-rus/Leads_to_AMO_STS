"""
Скрипт для загрузки лидов из Excel-файла в AmoCRM.

Основные функции:
1. Выбор Excel файла через диалоговое окно
2. Чтение телефонов и доменов из файла
3. Отправка лидов в AmoCRM
4. Итоговый отчёт: сколько лидов создано и ссылки на них
"""

import os
import time
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd
from tkinter import Tk, filedialog

from logging_setup import configure_logging
from amo_api import get, post
from amo_auth import get_amo_api_domain


logger = configure_logging("upload_leads_amo")


# === КОНФИГУРАЦИЯ ДЛЯ СОЗДАНИЯ ЛИДОВ В AMOCRM ===

PHONE_FIELD_ID = 484547  # ID поля телефона для контакта (PHONE)
PIPELINE_ID = 8913170  # ID воронки "Идентификация"
STATUS_ID = 71936170  # ID этапа "НОВАЯ Заявка"
RESPONSIBLE_USER_ID = 7186279  # ID ответственного "Старший специалист"
UTM_SOURCE_FIELD_ID = 548653  # ID поля utm_source
PARTNER_LEAD_ID_FIELD_ID = 548655  # ID поля partner_lead_id
TAG_NAME = "Конкурент"
SOURCE_NAME = "Конкурент"


def create_contact_with_phone(phone: str, name: str | None = None) -> int | None:
    """
    Создание контакта с номером телефона.

    Args:
        phone: Номер телефона.
        name: Имя контакта (если не задано — используется "Контакт {phone}").

    Returns:
        ID созданного контакта или None в случае ошибки.
    """
    if not name:
        name = f"Контакт {phone}"

    contact_data = {
        "name": name,
        "custom_fields_values": [
            {
                "field_id": PHONE_FIELD_ID,
                "values": [
                    {
                        "value": phone,
                        "enum_code": "WORK",
                    }
                ],
            }
        ],
    }

    result = post("contacts", [contact_data])

    if not result or "_embedded" not in result or "contacts" not in result["_embedded"]:
        return None

    contact_id = result["_embedded"]["contacts"][0]["id"]
    return int(contact_id)


def get_lead_url(lead_id: int) -> str:
    """
    Генерирует ссылку на лид в интерфейсе AmoCRM.
    """
    api_domain = get_amo_api_domain()
    return f"https://{api_domain}.amocrm.ru/leads/detail/{lead_id}"


def add_comment_to_lead(lead_id: int, datetime_str: str, site: str | None) -> bool:
    """
    Добавление комментария к лиду с датой, временем и (опционально) сайтом.
    """
    # Если домен не передан, пишем только дату/время
    if site:
        text = f"Дата поступления: {datetime_str}\nСайт: {site}"
    else:
        text = f"Дата поступления: {datetime_str}"

    comment_data = {
        "note_type": "common",
        "params": {
            "text": text,
        },
    }

    result = post(f"leads/{lead_id}/notes", [comment_data])

    if result and "_embedded" in result and "notes" in result["_embedded"]:
        logger.info("Комментарий добавлен к лиду %s", lead_id)
        return True

    logger.error("Ошибка добавления комментария к лиду %s", lead_id)
    return False


def create_lead_with_contact(
    name: str,
    contact_id: int,
    utm_source: str | None = None,
    partner_lead_id: str | None = None,
) -> int | None:
    """
    Создание лида и привязка к нему контакта.

    Returns:
        ID созданного лида или None в случае ошибки.
    """
    lead_data = {
        "name": name,
        "pipeline_id": PIPELINE_ID,
        "status_id": STATUS_ID,
        "responsible_user_id": RESPONSIBLE_USER_ID,
        "tags": [TAG_NAME],
        "source": SOURCE_NAME,
    }

    custom_fields_values: list[dict] = []

    # В utm_source записываем домен (если он есть)
    if utm_source:
        custom_fields_values.append(
            {
                "field_id": UTM_SOURCE_FIELD_ID,
                "values": [{"value": utm_source}],
            }
        )

    # В partner_lead_id записываем маркер источника "competitors"
    if partner_lead_id:
        custom_fields_values.append(
            {
                "field_id": PARTNER_LEAD_ID_FIELD_ID,
                "values": [{"value": partner_lead_id}],
            }
        )

    if custom_fields_values:
        lead_data["custom_fields_values"] = custom_fields_values

    result = post("leads", [lead_data])

    if not result or "_embedded" not in result or "leads" not in result["_embedded"]:
        return None

    lead_id = result["_embedded"]["leads"][0]["id"]

    link_data = [
        {
            "to_entity_id": contact_id,
            "to_entity_type": "contacts",
        }
    ]

    link_result = post(f"leads/{lead_id}/link", link_data)

    if not link_result:
        logger.error(
            "Ошибка при связывании лида %s с контактом %s", lead_id, contact_id
        )

    return int(lead_id)


def create_lead_with_phone(
    phone: str,
    datetime_str: str | None = None,
    site: str | None = None,
) -> tuple[int | None, int | None]:
    """
    Создание лида через создание контакта по номеру телефона.

    Args:
        phone: Номер телефона.
        datetime_str: Дата и время поступления заявки.
        site: Домен (сайт заявки), может быть None.

    Returns:
        (lead_id, contact_id) или (None, None) в случае ошибки.
    """
    contact_id = create_contact_with_phone(phone)

    if not contact_id:
        logger.error("Ошибка создания контакта для телефона %s", phone)
        return None, None

    lead_name = f"конкурент лид {phone}"
    partner_lead_id = "competitors"

    lead_id = create_lead_with_contact(
        lead_name,
        contact_id,
        utm_source=site,
        partner_lead_id=partner_lead_id,
    )

    if not lead_id:
        logger.error("Ошибка создания лида для контакта %s", contact_id)
        return None, contact_id

    # Если есть дата/время — добавляем комментарий (с доменом, если есть)
    if datetime_str:
        add_comment_to_lead(lead_id, datetime_str, site)

    return lead_id, contact_id


def select_excel_file() -> str:
    """
    Открывает диалоговое окно для выбора Excel файла.

    Returns:
        str: Путь к выбранному файлу или пустая строка, если файл не выбран.
    """
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    file_path = filedialog.askopenfilename(
        title="Выберите Excel файл с лидами для AmoCRM",
        filetypes=[("Excel files", "*.xlsx *.xls")],
        initialdir=os.path.dirname(os.path.abspath(__file__)),
    )

    root.destroy()
    return file_path


def read_leads_from_excel(file_path: str) -> List[Dict[str, Any]]:
    """
    Читает данные лидов из Excel-файла.

    Ожидаемые колонки:
    - 'Телефон' (обязательно)
    - 'Домен' (необязательно, если пусто — лид всё равно создаётся)

    Args:
        file_path (str): Путь к Excel-файлу с лидами.

    Returns:
        list[dict]: Список словарей с полями:
            - phone: str
            - domain: str | None
    """
    leads: List[Dict[str, Any]] = []

    try:
        df = pd.read_excel(file_path)

        # Проверяем обязательную колонку
        required_columns = ["Телефон"]
        for column in required_columns:
            if column not in df.columns:
                raise ValueError(f"В файле отсутствует колонка '{column}'")

        # Наличие колонки домена не обязательно
        has_domain_column = "Домен" in df.columns

        for _, row in df.iterrows():
            phone_raw = str(row["Телефон"]).strip()

            # Пропускаем пустые и nan
            if not phone_raw or phone_raw.lower() == "nan":
                continue

            # Убираем .0, если номер считался числом
            phone = phone_raw.replace(".0", "")

            domain: str | None = None
            if has_domain_column:
                value = row["Домен"]
                if pd.notna(value):
                    domain_str = str(value).strip()
                    if domain_str and domain_str.lower() != "nan":
                        domain = domain_str

            leads.append(
                {
                    "phone": phone,
                    "domain": domain,
                }
            )

        logger.info("Прочитано %s лидов из файла", len(leads))
        return leads

    except Exception as exc:
        print(f"Ошибка при чтении Excel-файла: {exc}")
        logger.exception("Ошибка при чтении Excel-файла")
        return []


def upload_leads_to_amo(leads: List[Dict[str, Any]]) -> None:
    """
    Загружает лиды в AmoCRM.

    Args:
        leads: Список словарей с ключами 'phone' и 'domain'.
    """
    total = len(leads)
    success = 0
    created: List[Dict[str, Any]] = []

    for index, lead in enumerate(leads, start=1):
        phone = lead.get("phone")
        domain = lead.get("domain")

        # На всякий случай ещё раз проверяем телефон
        if not phone:
            print(f"Строка {index}: пустой телефон, пропускаем")
            logger.warning("Строка %s: пустой телефон, пропуск", index)
            continue

        # Время поступления заявки — текущее
        datetime_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            lead_id, contact_id = create_lead_with_phone(
                phone,
                datetime_str=datetime_str,
                site=domain,
            )

            if lead_id:
                success += 1
                lead_url = get_lead_url(lead_id)

                created.append(
                    {
                        "phone": phone,
                        "lead_id": lead_id,
                        "contact_id": contact_id,
                        "url": lead_url,
                    }
                )

                print(
                    f"✅ {index}/{total} Лид создан. Телефон: {phone}, "
                    f"ID лида: {lead_id}"
                )
                print(f"   Ссылка: {lead_url}")
            else:
                print(
                    f"❌ {index}/{total} Не удалось создать лид. Телефон: {phone}"
                )
                logger.error(
                    "Не удалось создать лид для телефона %s (index %s)",
                    phone,
                    index,
                )

        except Exception as exc:
            print(
                f"❌ {index}/{total} Ошибка при создании лида для телефона "
                f"{phone}: {exc}"
            )
            logger.exception(
                "Исключение при создании лида для телефона %s (index %s)",
                phone,
                index,
            )

        # Небольшая пауза, чтобы не завалить API запросами
        time.sleep(0.3)

    # Итоговый отчёт
    print("\n=== ИТОГОВЫЙ ОТЧЁТ ПО ЗАГРУЗКЕ В AMOCRM ===")
    print(f"Всего строк с телефонами: {total}")
    print(f"Успешно создано лидов: {success}")

    if created:
        print("\nСсылки на созданные лиды:")
        for item in created:
            print(
                f"- Телефон: {item['phone']}, "
                f"ID лида: {item['lead_id']}, "
                f"Ссылка: {item['url']}"
            )


def main() -> None:
    """
    Основной сценарий:
    1. Выбор Excel файла
    2. Проверка существования файла
    3. Чтение лидов (телефон + опционально домен)
    4. Показ примера первых 3 лидов
    5. Подтверждение
    6. Загрузка лидов в AmoCRM и вывод отчёта
    """
    file_path = select_excel_file()

    if not file_path:
        print("Файл не выбран. Загрузка отменена.")
        return

    if not os.path.exists(file_path):
        print(f"Файл не найден: {file_path}")
        return

    leads = read_leads_from_excel(file_path)

    if not leads:
        print("Не найдено лидов для загрузки (нет валидных телефонов).")
        return

    print(f"Найдено {len(leads)} лидов с телефонами.")
    print("\nПример первых 3 лидов:")
    for lead in leads[:3]:
        phone = lead.get("phone")
        domain = lead.get("domain")
        print(f"- Телефон: {phone}")
        if domain:
            print(f"  Домен: {domain}")
        else:
            print("  Домен: (пусто, будет создан лид без домена в комментарии)")
        print("-" * 50)

    print("\nНачать загрузку лидов в AmoCRM? (y/n)")
    answer = input().strip().lower()
    if answer != "y":
        print("Загрузка отменена пользователем.")
        return

    upload_leads_to_amo(leads)


if __name__ == "__main__":
    main()


