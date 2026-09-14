"""
СКРИПТ ДЛЯ СОЗДАНИЯ ЛИДА В AMOCRM ПО ТЗ

Требования:
- Воронка: Идентификация
- Статус: НОВАЯ Заявка
- Название лида: "конкурент лид {телефон}"
- Комментарий: Дата поступления и сайт заявки
- Ответственный: Старший специалист
- UTM Source: "competitors"

Тестовые данные:
- Телефон: 70001231212
- Сайт: test.com
- Дата/время: 2025-11-21 9:49:45
"""
from amo_api import get, post
from amo_auth import get_amo_api_domain

# === КОНФИГУРАЦИЯ ПРОЕКТА ===
# Эти ID нужно изменить для вашего AmoCRM аккаунта!
# Получите их через скрипт get_fields.py

PHONE_FIELD_ID = 484547  # ID поля телефона для контакта (PHONE)
PIPELINE_ID = 8913170  # ID воронки "Идентификация"
STATUS_ID = 71936170  # ID этапа "НОВАЯ Заявка"
RESPONSIBLE_USER_ID = 7186279  # ID ответственного "Старший специалист"
UTM_SOURCE_FIELD_ID = 548653  # ID поля utm_source
TAG_NAME = "Конкурент"
SOURCE_NAME = "Конкурент"

# === ПРИМЕРЫ ДРУГИХ ПОЛЕЙ ДЛЯ РАЗНЫХ ПРОЕКТОВ ===
# Раскомментируйте и настройте под ваши нужды:

# EMAIL_FIELD_ID = 555666  # Для проектов где нужен email
# BUDGET_FIELD_ID = 777888  # Для проектов с бюджетом
# SOURCE_FIELD_ID = 999000  # Для источников трафика
# UTM_FIELD_ID = 111222     # Для UTM меток

def create_contact_with_phone(phone, name=None):
    """
    Создание контакта с номером телефона

    АДАПТАЦИЯ: Добавьте другие поля в custom_fields_values
    Примеры разных типов полей см. ниже

    Args:
        phone (str): Номер телефона
        name (str, optional): Имя контакта

    Returns:
        int: ID созданного контакта или None в случае ошибки
    """
    if not name:
        name = f"Контакт {phone}"

    # === БАЗОВАЯ СТРУКТУРА КОНТАКТА ===
    # Всегда есть name и custom_fields_values
    contact_data = {
        "name": name,
        "custom_fields_values": [
            # === ПОЛЕ ТЕЛЕФОНА (ОБЯЗАТЕЛЬНО ДЛЯ ЭТОГО ШАБЛОНА) ===
            {
                "field_id": PHONE_FIELD_ID,
                "values": [
                    {
                        "value": phone,
                        "enum_code": "WORK"  # Рабочий телефон
                    }
                ]
            }

            # === ПРИМЕРЫ ДОБАВЛЕНИЯ ДРУГИХ ПОЛЕЙ ===
            # Раскомментируйте и настройте под ваш проект:

            # {
            #     "field_id": EMAIL_FIELD_ID,
            #     "values": [{"value": "email@example.com"}]
            # },
            # {
            #     "field_id": BUDGET_FIELD_ID,
            #     "values": [{"value": 100000}]
            # },
            # {
            #     "field_id": SOURCE_FIELD_ID,
            #     "values": [{"value": "сайт"}]
            # }
        ]
    }

    # === ПРИМЕРЫ РАЗНЫХ ТИПОВ ПОЛЕЙ ДЛЯ АДАПТАЦИИ ===
    """
    Текстовое поле:
    {"field_id": TEXT_FIELD_ID, "values": [{"value": "текст"}]}

    Числовое поле:
    {"field_id": NUMBER_FIELD_ID, "values": [{"value": 123}]}

    Дата:
    {"field_id": DATE_FIELD_ID, "values": [{"value": "2024-01-01"}]}

    Выпадающий список:
    {"field_id": SELECT_FIELD_ID, "values": [{"value": "опция1"}]}

    Множественный выбор:
    {"field_id": MULTI_FIELD_ID, "values": [
        {"value": "опция1"},
        {"value": "опция2"}
    ]}
    """
    
    # Создаем контакт
    result = post('contacts', [contact_data])
    
    if not result or '_embedded' not in result or 'contacts' not in result['_embedded']:
        return None
        
    contact_id = result['_embedded']['contacts'][0]['id']
    return contact_id

def get_lead_url(lead_id):
    """
    Генерирует ссылку на лид в интерфейсе AmoCRM

    Args:
        lead_id (int): ID лида

    Returns:
        str: Полная ссылка на лид
    """
    api_domain = get_amo_api_domain()
    return f"https://{api_domain}.amocrm.ru/leads/detail/{lead_id}"

def add_comment_to_lead(lead_id, datetime_str, site):
    """
    Добавление комментария к лиду с датой, временем и сайтом

    Args:
        lead_id (int): ID лида
        datetime_str (str): Дата и время поступления
        site (str): Сайт заявки

    Returns:
        bool: True если комментарий добавлен успешно
    """
    comment_data = {
        "note_type": "common",
        "params": {
            "text": f"Дата поступления: {datetime_str}\nСайт: {site}"
        }
    }

    result = post(f"leads/{lead_id}/notes", [comment_data])

    if result and '_embedded' in result and 'notes' in result['_embedded']:
        print(f"✅ Комментарий добавлен к лиду {lead_id}")
        return True
    else:
        print(f"❌ Ошибка добавления комментария к лиду {lead_id}")
        return False

def create_lead_with_contact(name, contact_id):
    """
    Создание лида и привязка к нему контакта

    АДАПТАЦИЯ: Измените pipeline_id, status_id, responsible_user_id
    Получите их через: python get_fields.py pipelines

    Args:
        name (str): Название лида
        contact_id (int): ID контакта для привязки

    Returns:
        int: ID созданного лида или None в случае ошибки
    """
    # === СТРУКТУРА ДАННЫХ ЛИДА ===
    # Обязательные поля для создания лида в AmoCRM
    lead_data = {
        "name": name,  # Название лида
        "pipeline_id": PIPELINE_ID,  # ID воронки (ОБЯЗАТЕЛЬНО!)
        "status_id": STATUS_ID,      # ID статуса (ОБЯЗАТЕЛЬНО!)
        "responsible_user_id": RESPONSIBLE_USER_ID,  # Ответственный (ОБЯЗАТЕЛЬНО!)

        # Дополнительные поля (можно убрать если не нужны)
        "tags": [TAG_NAME],         # Теги для группировки
        "source": SOURCE_NAME,      # Источник лида

        # Заполнение UTM Source для конкурентных лидов
        "custom_fields_values": [
            {
                "field_id": UTM_SOURCE_FIELD_ID,
                "values": [{"value": "competitors"}]
            }
        ]
    }

    # === ПРИМЕРЫ ДОБАВЛЕНИЯ ПОЛЕЙ К ЛИДУ ===
    """
    Добавьте custom_fields_values если нужны дополнительные поля:

    lead_data["custom_fields_values"] = [
        {
            "field_id": BUDGET_FIELD_ID,
            "values": [{"value": 50000}]
        },
        {
            "field_id": UTM_SOURCE_ID,
            "values": [{"value": "google"}]
        }
    ]
    """
    
    # Создаем лид
    result = post('leads', [lead_data])
    
    if not result or '_embedded' not in result or 'leads' not in result['_embedded']:
        return None
        
    lead_id = result['_embedded']['leads'][0]['id']
    
    # Выводим краткую информацию о созданном лиде
    lead_url = get_lead_url(lead_id)
    print(f"Лид '{name}' создан (ID: {lead_id})")
    print(f"Ссылка: {lead_url}")

    
    # Связываем лид с контактом
    link_data = [{
        "to_entity_id": contact_id,
        "to_entity_type": "contacts"
    }]
    
    link_result = post(f"leads/{lead_id}/link", link_data)
    
    if link_result:
        print(f"Лид {lead_id} связан с контактом {contact_id}")
    else:
        print(f"Ошибка при связывании лида {lead_id} с контактом {contact_id}")
        
    return lead_id

def create_lead_with_phone(phone, datetime_str=None, site=None):
    """
    ОСНОВНАЯ ФУНКЦИЯ ШАБЛОНА
    Создание лида с номером телефона через создание контакта

    Args:
        phone (str): Номер телефона
        datetime_str (str, optional): Дата и время поступления
        site (str, optional): Сайт заявки

    Returns:
        tuple: (lead_id, contact_id) или (None, None) в случае ошибки
    """
    # === ШАГ 1: СОЗДАЕМ КОНТАКТ ===
    # Адаптируйте функцию create_contact_with_phone() для ваших полей
    contact_id = create_contact_with_phone(phone)

    if not contact_id:
        print(f"❌ Ошибка создания контакта для телефона {phone}")
        return None, None

    print(f"✅ Контакт создан (ID: {contact_id})")

    # === ШАГ 2: СОЗДАЕМ ЛИД ===
    # Название лида в формате "конкурент лид и сайт конкурента"
    lead_name = f"конкурент лид {phone}"

    lead_id = create_lead_with_contact(lead_name, contact_id)

    if not lead_id:
        print(f"❌ Ошибка создания лида для контакта {contact_id}")
        return None, contact_id

    print(f"✅ Лид создан (ID: {lead_id})")

    # Добавляем комментарий если переданы дата и сайт
    if datetime_str and site:
        add_comment_to_lead(lead_id, datetime_str, site)

    return lead_id, contact_id

if __name__ == "__main__":
    # Тестовый запуск с тестовыми данными

    phone_number = "70001231212"
    site = "test.com"
    datetime_str = "2025-11-21 9:49:45"  # Дата и время поступления заявки

    print(f"🧪 Тестирование создания лида:")
    print(f"   Телефон: {phone_number}")
    print(f"   Сайт: {site}")
    print(f"   Дата/время: {datetime_str}")
    print()

    lead_id, contact_id = create_lead_with_phone(phone_number, datetime_str, site)

    if lead_id:
        lead_url = get_lead_url(lead_id)
        print(f"\n✅ ТЕСТ ПРОЙДЕН! Создан лид (ID: {lead_id}) с контактом (ID: {contact_id})")
        print(f"🔗 Ссылка на лид: {lead_url}")
    elif contact_id:
        print(f"\n⚠️  Создан только контакт (ID: {contact_id}), но не удалось создать лид.")
    else:
        print(f"\n❌ ТЕСТ НЕ ПРОЙДЕН! Ошибка при создании лида и контакта.") 