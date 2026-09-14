# AmoCRM Integration

Комплексная интеграция с AmoCRM: создание лидов, управление сделками, работа с чатами и Telegram ботами.

## 🚀 Быстрый старт

1. **Установка:**
   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   pip install -r requirements.txt
   ```

2. **Настройка `.env`:**
   ```env
   AMO_TOKEN=your_jwt_token_here
   AMO_BASE_DOMAIN=amocrm.ru
   AMO_API_DOMAIN=your_account_name
   ```

3. **Анализ аккаунта:**
   ```bash
   python 1_explore_account.py  # Анализ всех полей и воронок
   ```

4. **Создание лида:**
   ```bash
   python create_one_lead_amo.py
   ```

## 📋 Основные скрипты

### Работа с лидами
- **`create_one_lead_amo.py`** - Создание контакта и сделки
- **`move_lead_status.py`** - Перемещение по воронке
- **`check_lead_status.py`** - Проверка статуса
- **`check_pipelines.py`** - Просмотр воронок

### Коммуникации
- **`client_messages.py`** - Чтение сообщений клиентов нужна регистрация в тех поддержке
- **`bot_bridge_mvp.py`** - Telegram ↔ AmoCRM мост
- **`chat_api.py`** - API чатов AmoCRM

### Исследование
- **`1_explore_account.py`** - Полный анализ аккаунта
- **`1_get_fields.py`** - Получение ID полей/воронок
- **`debug_patch_request.py`** - Отладка запросов

## 🔧 Настройка

### 1. Токен API
AmoCRM → Настройки → Интеграции → Создать приложение → Скопировать JWT

### 2. ID полей
```bash
python 1_explore_account.py  # Рекомендуемый способ
```
Или отдельно:
```bash
python 1_get_fields.py pipelines
python 1_get_fields.py contacts
python 1_get_fields.py users
```

### 3. Обновление констант
В `create_one_lead_amo.py` обновите:
- `PHONE_FIELD_ID`
- `PIPELINE_ID`
- `STATUS_ID`
- `RESPONSIBLE_USER_ID`

## 📱 Telegram интеграция

1. Создайте бота через [@BotFather](https://t.me/botfather)
2. Добавьте в `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token
   MVP_DEFAULT_LEAD_ID=your_lead_id
   ```
3. Запустите: `python bot_bridge_mvp.py`

### Как работает
- **Входящие:** Telegram → заметки в AmoCRM
- **Исходящие:** Заметки с префиксом "tg:" → Telegram

## ⚠️ Важное

### Безопасность
- Токены хранятся в `.env` (не коммитить!)
- Все запросы по HTTPS
- Токены маскируются в логах

### API лимиты
- 5000 запросов/час
- Автоматическая обработка rate limiting
- Рекомендуется кэширование

### Особенности
- ID полей уникальны для каждого аккаунта
- Тестируйте на тестовых данных
- Используйте логи для отладки

## 📚 Ресурсы

- **AmoCRM API:** https://www.amocrm.ru/developers/
- **Telegram Bot API:** https://github.com/python-telegram-bot/python-telegram-bot
- **Регистрация Chat API:** `инструкция_регистрация_chat_Api.md`

## 🆘 Поддержка

При проблемах:
1. Проверьте логи в `logs/`
2. Используйте `debug_patch_request.py`
3. Проверьте корректность токена и ID
4. Проверьте лимиты API в AmoCRM
