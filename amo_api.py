"""
БАЗОВЫЙ API МОДУЛЬ ДЛЯ AMOCRM

Что делает этот модуль:
- get_headers() - формирует заголовки с токеном
- get_url(path) - создает URL для запросов
- retry_request - декоратор для повторных попыток
- make_request() - основная функция отправки запросов
- get(), post(), patch(), delete() - обертки для разных типов запросов

Адаптация для другого проекта:
- Измените токен в .env
- Проверьте домены в get_url()
- Настройте retry_request() если нужно

Особенности:
- Автоматическая обработка rate limiting (429)
- Повторные попытки при ошибках
- Подробное логирование всех запросов
- Маскировка токена в логах
"""
import json
import time
import requests
from requests.exceptions import RequestException
from amo_auth import get_amo_token, get_amo_domain, get_amo_api_domain

def get_headers():
    """
    Формирует заголовки для запросов к API
    
    Returns:
        dict: Заголовки с токеном авторизации
    """
    token = get_amo_token()
    print(f"Получен токен для запроса (первые 5 символов): {token[:5] if token else 'нет'}...")
    
    return {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'User-Agent': 'amoCRM-oAuth-client/1.0'
    }

def get_url(path):
    """
    Формирует URL для запроса к API
    
    Args:
        path (str): Путь API
        
    Returns:
        str: Полный URL
    """
    base_domain = get_amo_domain()
    api_domain = get_amo_api_domain()
    return f'https://{api_domain}.{base_domain}/api/v4/{path}'

def retry_request(func, max_retries: int = 5, delay: int = 1):
    """
    Декоратор для повторных попыток выполнения запроса.

    Повторяем вызов функции, если:
    - возникла сетевая ошибка (RequestException, TimeoutError)
    - функция вернула None (ошибка уже обработана внутри неё)

    Args:
        func: Функция для выполнения.
        max_retries: Максимальное количество попыток.
        delay: Базовая задержка между попытками в секундах.

    Returns:
        Результат выполнения функции или None в случае всех неудачных попыток.
    """

    def wrapper(*args, **kwargs):
        attempt = 0

        # Пытаемся понять, относится ли запрос к лидам (для отдельного логирования)
        def is_leads_request() -> bool:
            # Ожидаем сигнатуру make_request(method, url, ...)
            url = None
            if len(args) >= 2 and isinstance(args[1], str):
                url = args[1]
            elif "url" in kwargs and isinstance(kwargs["url"], str):
                url = kwargs["url"]

            if not url:
                return False

            # Простейшая эвристика: в URL есть "/leads"
            return "/leads" in url

        leads_flag = is_leads_request()

        while attempt < max_retries:
            attempt += 1
            try:
                result = func(*args, **kwargs)

                # Если функция вернула что‑то, кроме None — считаем это успешным результатом.
                if result is not None:
                    return result

                # Функция вернула None — пробуем ещё раз (если остались попытки).
                if attempt >= max_retries:
                    print(
                        f"Исчерпаны все попытки ({max_retries}). "
                        "Функция вернула None на последней попытке."
                    )
                    if leads_flag:
                        print("Ретраи для запросов по лидам не помогли (None на последней попытке).")
                    return None

                current_delay = delay * (2 ** (attempt - 1))
                msg = (
                    f"Попытка {attempt}/{max_retries} завершилась без результата (None). "
                    f"Повтор через {current_delay} сек."
                )
                if leads_flag:
                    msg += " [ретрай при заливке лидов]"
                print(msg)
                time.sleep(current_delay)

            except (RequestException, TimeoutError) as e:
                if attempt >= max_retries:
                    print(f"Исчерпаны все попытки ({max_retries}). Последняя ошибка: {e}")
                    if leads_flag:
                        print("Ретраи для запросов по лидам не помогли (исключение на последней попытке).")
                    return None

                current_delay = delay * (2 ** (attempt - 1))
                msg = (
                    f"Попытка {attempt}/{max_retries} не удалась: {e}. "
                    f"Повторная попытка через {current_delay} сек."
                )
                if leads_flag:
                    msg += " [ретрай при заливке лидов]"
                print(msg)
                time.sleep(current_delay)

        return None

    return wrapper

@retry_request
def make_request(method, url, params=None, data=None, timeout=30):
    """
    Выполняет запрос к API AmoCRM
    
    Args:
        method (str): HTTP метод (GET, POST, PATCH, DELETE)
        url (str): URL запроса
        params (dict, optional): Параметры запроса
        data (dict, optional): Данные для отправки в теле запроса
        timeout (int, optional): Таймаут запроса в секундах
    
    Returns:
        dict: Ответ от API в формате JSON или None в случае ошибки
    """
    headers = get_headers()
    
    # Логируем детали запроса
    print(f"API запрос: {method} {url}")
    print(f"Заголовки: {mask_token(headers)}")
    print(f"Параметры: {params}")
    if data:
        # Ограничиваем вывод данных, чтобы не перегружать логи
        if isinstance(data, dict):
            log_data = {k: (v[:100] + '...' if isinstance(v, str) and len(v) > 100 else v) 
                        for k, v in data.items()}
        elif isinstance(data, list):
            log_data = f"Список из {len(data)} элементов"
        else:
            log_data = str(data)[:200] + '...' if len(str(data)) > 200 else data
        print(f"Данные: {log_data}")
    
    try:
        if method == 'GET':
            response = requests.get(url, headers=headers, params=params, timeout=timeout, verify=True)
        elif method == 'POST':
            response = requests.post(url, headers=headers, params=params, json=data, timeout=timeout, verify=True)
        elif method == 'PATCH':
            response = requests.patch(url, headers=headers, params=params, json=data, timeout=timeout, verify=True)
        elif method == 'DELETE':
            response = requests.delete(url, headers=headers, params=params, timeout=timeout, verify=True)
        else:
            print(f"Неизвестный метод запроса: {method}")
            return None
        
        # Логируем ответ
        print(f"Статус ответа: {response.status_code}")
        print(f"Заголовки ответа: {filter_important_headers(dict(response.headers))}")
        
        # Обработка rate limiting
        if response.status_code == 429:
            retry_after = int(response.headers.get('Retry-After', 60))
            print(f"Достигнут лимит запросов. Ожидание {retry_after} секунд")
            time.sleep(retry_after)
            # Рекурсивно повторяем запрос
            return make_request(method, url, params, data, timeout)
            
        # Проверяем статус ответа
        if response.status_code in (200, 201, 204):
            try:
                # Для ответов без контента
                if response.status_code == 204 or not response.text:
                    print(f"Успешный запрос без данных: {method} {url}")
                    return {}
                
                # Парсим JSON
                result = response.json()
                print(f"Тело ответа: {truncate_response_body(result)}")
                
                # Проверяем, содержит ли ответ данные
                if not result:
                    print(f"Пустой ответ от API: {method} {url}")
                    return {}
                
                # Логируем краткую информацию о результате
                if isinstance(result, dict):
                    if '_embedded' in result and isinstance(result['_embedded'], dict):
                        for key, value in result['_embedded'].items():
                            if isinstance(value, list):
                                print(f"Получено {len(value)} элементов в {key}")
                            else:
                                print(f"Получены данные в {key}")
                    else:
                        print(f"Получен ответ с ключами: {list(result.keys())}")
                elif isinstance(result, list):
                    print(f"Получен список из {len(result)} элементов")
                
                return result
            except json.JSONDecodeError as e:
                print(f"Ошибка декодирования JSON: {e}")
                print(f"Текст ответа: {response.text[:200]}...")
                return None
        else:
            # Логируем ошибки
            print(f"Ошибка {response.status_code}: {method} {url}")
            try:
                error_data = response.json()
                print(f"Детали ошибки: {error_data}")
            except Exception:
                print(f"Текст ошибки: {response.text[:200]}...")
            
            # Проверяем, истек ли токен
            if response.status_code == 401:
                print("Ошибка авторизации (401). Проверьте правильность токена и домена.")
            
            return None
    except requests.exceptions.Timeout as e:
        print(f"Таймаут запроса: {e}")
        return None
    except RequestException as e:
        print(f"Ошибка сетевого запроса: {e}")
        return None
    except Exception as e:
        print(f"Неожиданная ошибка при выполнении запроса: {e}")
        return None

# Обертки для разных типов запросов
def get(path, params=None):
    """GET запрос к API"""
    url = get_url(path)
    return make_request('GET', url, params)

def post(path, data, params=None):
    """POST запрос к API"""
    url = get_url(path)
    return make_request('POST', url, params, data)

def patch(path, data, params=None):
    """PATCH запрос к API"""
    url = get_url(path)
    return make_request('PATCH', url, params, data)

def delete(path, params=None):
    """DELETE запрос к API"""
    url = get_url(path)
    return make_request('DELETE', url, params)

def mask_token(headers):
    """
    Маскирует токен в заголовках для безопасного логирования
    """
    if not headers:
        return headers
        
    masked_headers = headers.copy()
    if 'Authorization' in masked_headers:
        auth = masked_headers['Authorization']
        if auth.startswith('Bearer '):
            token = auth[7:]  # убираем 'Bearer '
            masked_headers['Authorization'] = f'Bearer {token[:8]}...'
    return masked_headers

def filter_important_headers(headers):
    """
    Фильтрует только важные заголовки для логирования
    """
    important_headers = [
        'Content-Type',
        'X-Request-Id',
        'X-Runtime-Generated'
    ]
    return {k: v for k, v in headers.items() if k in important_headers}

def truncate_response_body(body):
    """
    Сокращает тело ответа для логирования
    """
    if not isinstance(body, dict):
        return body
        
    result = {}
    if '_total_items' in body:
        result['_total_items'] = body['_total_items']
    if '_page' in body:
        result['_page'] = body['_page']
    if '_embedded' in body:
        # Показываем только количество элементов
        for key, value in body['_embedded'].items():
            if isinstance(value, list):
                result[f'{key}_count'] = len(value)
            else:
                result[key] = '...'
    return result
