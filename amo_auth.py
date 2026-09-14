"""
Модуль авторизации в AmoCRM.
Используется для получения токенов и доменов.
"""
import os
from dotenv import load_dotenv


# Загружаем переменные окружения
load_dotenv()

def get_amo_token():
    """Получение токена из .env"""
    token = os.getenv('AMO_TOKEN')
    if not token:
        print("AMO_TOKEN не найден в .env файле")
    return token

def get_amo_domain():
    """Получение домена из .env"""
    domain = os.getenv('AMO_BASE_DOMAIN')
    if not domain:
        print("AMO_BASE_DOMAIN не найден в .env файле")
    return domain

def get_amo_api_domain():
    """Получение API домена из .env"""
    api_domain = os.getenv('AMO_API_DOMAIN')
    if not api_domain:
        print("AMO_API_DOMAIN не найден в .env файле")
    return api_domain

def verify_env_variables():
    """Проверка наличия всех необходимых переменных окружения"""
    required_vars = {
        'AMO_TOKEN': get_amo_token(),
        'AMO_BASE_DOMAIN': get_amo_domain(),
        'AMO_API_DOMAIN': get_amo_api_domain()
    }
    
    missing_vars = [var for var, value in required_vars.items() if not value]
    
    if missing_vars:
        print(f"Отсутствуют обязательные переменные окружения: {', '.join(missing_vars)}")
        return False
    
    print("Все переменные окружения загружены успешно")
    return True

# Проверяем переменные при импорте модуля
if not verify_env_variables():
    print("Проверьте файл .env и убедитесь, что все переменные заданы корректно")