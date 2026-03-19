# Agent guide for `test_api_evo_link`

Это проект Telegram-бота для генерации изображений по шаблонным промптам (Evolink API).

## Как использовать эти документы
- Если нужно понять “что где находится” или “как устроена логика”, начинай с:
  - `docs/BOT_STRUCTURE_RU.md` (структура и зависимости)
  - `docs/BOT_FLOW_RU.md` (основные сценарии пользователя/админа)
  - `docs/BOT_DATA_CONTRACTS_RU.md` (контракты переменных, JSON форматы, placeholders)
- Если нужно изменить поведение, которое связано с генерацией изображений, ищи код в:
  - `app/routers/common.py` (`RouterCtx.run_generation`)
  - `app/evo_client.py` (`EvoClient`)
  - `app/routers/user/generation.py` (FSM сбор значений переменных)

## Точка входа исполнения
- Запуск кода в основном пайплайне: `bot.py` (создает пул БД, `Repo`, `EvoClient`, и подключает роутер из `app/routers/main.py`).

## Основные сущности (для ориентира)
- `Repo` (`app/repo.py`) — единственная точка работы с PostgreSQL.
- `RouterCtx` (`app/routers/common.py`) — контекст/утилиты для хендлеров: ensure user, отображение карточек, биллинг, генерация.
- Шаблон промпта и переменные:
  - `[VAR]` — image переменная (пользователь/бот подставляет URL с Telegram файла)
  - `<VAR>` — text переменная

