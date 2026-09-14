"""
СКРИПТ АВТОМАТИЧЕСКОГО ИССЛЕДОВАНИЯ AMOCRM АККАУНТА

Запустите этот скрипт первым при адаптации проекта!
Он покажет все необходимые ID для create_one_lead_amo.py

Результат сохраняется в файле account_info.json
"""
import json
from amo_field_manager import (
    get_lead_fields,
    get_contact_fields,
    get_company_fields,
    get_pipelines,
    get_users
)

def explore_account():
    """Исследует аккаунт и сохраняет информацию в JSON"""

    print("🔍 Начинаем исследование аккаунта AmoCRM...")
    account_info = {
        "leads": {},
        "contacts": {},
        "companies": {},
        "pipelines": {},
        "users": {}
    }

    # Получаем поля лидов
    print("\n📋 Получаем поля лидов...")
    lead_fields = get_lead_fields()
    if lead_fields and '_embedded' in lead_fields:
        account_info["leads"]["fields"] = lead_fields['_embedded'].get('custom_fields', [])
        print(f"✅ Найдено {len(account_info['leads']['fields'])} полей лидов")

    # Получаем поля контактов
    print("\n👥 Получаем поля контактов...")
    contact_fields = get_contact_fields()
    if contact_fields and '_embedded' in contact_fields:
        account_info["contacts"]["fields"] = contact_fields['_embedded'].get('custom_fields', [])
        print(f"✅ Найдено {len(account_info['contacts']['fields'])} полей контактов")

    # Получаем поля компаний
    print("\n🏢 Получаем поля компаний...")
    company_fields = get_company_fields()
    if company_fields and '_embedded' in company_fields:
        account_info["companies"]["fields"] = company_fields['_embedded'].get('custom_fields', [])
        print(f"✅ Найдено {len(account_info['companies']['fields'])} полей компаний")

    # Получаем воронки и статусы
    print("\n📊 Получаем воронки и статусы...")
    pipelines = get_pipelines()
    if pipelines and '_embedded' in pipelines:
        account_info["pipelines"]["data"] = pipelines['_embedded'].get('pipelines', [])
        print(f"✅ Найдено {len(account_info['pipelines']['data'])} воронок")

    # Получаем пользователей
    print("\n👤 Получаем пользователей...")
    users = get_users()
    if users and '_embedded' in users:
        account_info["users"]["data"] = users['_embedded'].get('users', [])
        print(f"✅ Найдено {len(account_info['users']['data'])} пользователей")

    # Сохраняем в файл
    with open('account_info.json', 'w', encoding='utf-8') as f:
        json.dump(account_info, f, ensure_ascii=False, indent=2)

    print("\n💾 Информация сохранена в account_info.json")
    return account_info

def print_summary(account_info):
    """Выводит краткую сводку по аккаунту"""

    print("\n" + "="*50)
    print("📋 СВОДКА ПО АККАУНТУ")
    print("="*50)

    # Поля контактов (ищем телефон)
    print("\n📞 ПОЛЯ КОНТАКТОВ:")
    for field in account_info["contacts"]["fields"]:
        if 'phone' in field.get('name', '').lower():
            print(f"   Телефон: {field['id']} - {field['name']}")
        elif 'email' in field.get('name', '').lower():
            print(f"   Email: {field['id']} - {field['name']}")

    # Воронки и статусы
    print("\n📊 ВОРОНКИ:")
    for pipeline in account_info["pipelines"]["data"]:
        print(f"   {pipeline['id']}: {pipeline['name']}")
        if '_embedded' in pipeline and 'statuses' in pipeline['_embedded']:
            for status in pipeline['_embedded']['statuses']:
                print(f"      └─ {status['id']}: {status['name']}")

    # Пользователи
    print("\n👥 ПОЛЬЗОВАТЕЛИ:")
    for user in account_info["users"]["data"]:
        print(f"   {user['id']}: {user.get('name', '')} {user.get('last_name', '')}")

    print("\n" + "="*50)
    print("💡 СКОПИРУЙТЕ ЭТИ ID В create_one_lead_amo.py")
    print("="*50)

def main():
    """Основная функция"""
    try:
        account_info = explore_account()
        print_summary(account_info)
        print("\n✅ Исследование завершено! Теперь настройте create_one_lead_amo.py")

    except Exception as e:
        print(f"\n❌ Ошибка при исследовании: {e}")
        print("Проверьте настройки в .env файле")

if __name__ == "__main__":
    main()
