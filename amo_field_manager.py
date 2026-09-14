"""
Модуль для работы с полями AmoCRM через API.
Позволяет получать информацию о полях, их ID и значениях без доступа к интерфейсу.
"""
from amo_api import get

def get_lead_fields():
    """
    Получение списка всех полей для лидов
    
    Returns:
        dict: Словарь с информацией о полях
    """
    return get('leads/custom_fields')

def get_contact_fields():
    """
    Получение списка всех полей для контактов
    
    Returns:
        dict: Словарь с информацией о полях
    """
    return get('contacts/custom_fields')

def get_company_fields():
    """
    Получение списка всех полей для компаний
    
    Returns:
        dict: Словарь с информацией о полях
    """
    return get('companies/custom_fields')

def get_pipelines():
    """
    Получение списка воронок и их статусов
    
    Returns:
        dict: Словарь с информацией о воронках
    """
    return get('leads/pipelines')

def get_users():
    """
    Получение списка всех пользователей
    
    Returns:
        dict: Словарь с информацией о пользователях
    """
    return get('users')
