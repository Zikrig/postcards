# Структура проекта (актуальная)

## 1) Точка входа
- `bot.py`
  - Загружает настройки из env (`app/config.py`)
  - Создаёт `asyncpg` пул БД
  - Инициализирует `Repo` (создание/ALTER таблиц)
  - Создаёт `EvoClient`
  - Подключает middleware `AlbumMiddleware`
  - Подключает роутер из `app/routers/main.py` и запускает polling (или webhook, если включен в env)

## 2) Настройки
- `app/config.py`
  - `Settings`: BOT_TOKEN, API_KEY, DATABASE_URL, base_url Evolink, параметры генерации (model/size/quality), режим вебхука и таймауты.

## 3) Доступ к данным
- `app/repo.py`
  - Все операции с PostgreSQL спрятаны в `Repo`.
  - Помимо users/prompts/promo/db_state, включает теги и отношения prompt_tags.
  - Типовой вызов хендлера: `ctx.repo.<метод>`.

## 4) Внешние клиенты
- `app/evo_client.py`
  - `create_task(final_prompt, image_urls, quality)`
  - `wait_for_completion(task_id, on_progress)`
  - `get_credits()` для контроля порогов “remaining credits”.
- `app/deepseek_client.py` (опционально)
  - `refine_idea(idea)` -> возвращает структуру `feach.json` (idea + features)
  - `generate_final_prompt(idea, variables_spec)` -> возвращает `template` и `variable_descriptions`.

## 5) Роутеры (aiogram)
- `app/routers/main.py`
  - Экспортирует `create_router(repo, settings, evo, bot, deepseek)`.
  - Внутри создается `Router`, а затем регистрируются подсекции:
    - `register_user_auth`
    - `register_admin_panel`, `register_admin_prompts`, `register_admin_tags`, `register_admin_promo`
    - `register_shared_prompt_card`, `register_shared_editing`
    - `register_shared_variables`, `register_shared_features`, `register_shared_tags`, `register_shared_actions`
    - `register_user_menu`, `register_user_my_prompts`, `register_user_generation`

## 6) Общая логика в `RouterCtx`
- `app/routers/common.py` — ключевая точка общего поведения:
  - `ensure_user(...)` / `ensure_user_from_tg(...)`
  - `show_prompt_buttons(...)`, `present_prompt_card(...)`, `show_prompt_edit_actions(...)`
  - `ask_next_variable(...)` (FSM сбор значений при генерации)
  - `run_generation(...)` (биллинг -> render -> create_task -> polling -> отправка изображений -> очистка FSM)
  - `maybe_notify_admins_balance_checkpoint(...)` (по “bucket” оставшихся кредов в Evolink)
  - `telegram_file_url(file_id)` (для передачи image URL в Evolink)

## 7) Сценарии выбора переменных и wizard DeepSeek
- `app/states.py`
  - FSM группы: `AuthStates`, `AdminStates`, `GenerateStates`,
    `FinalPromptSetupStates` (wizard выборов перед финальным DeepSeek шаблоном),
    `PrimaryPromptOnboardingStates` (шаги конфигурации features при создании draft/черновика).
- `app/final_prompt_wizard.py`
  - Конвертация “features + wizard choices” в `variables_spec`,
    необходимый для `DeepSeekClient.generate_final_prompt`.

## 8) Контракты переменных промпта
- `app/utils.py`
  - `extract_variables(template)` — извлекает `[VAR]` (image) и `<VAR>` (text).
  - `variable_token(...)` и `render_prompt(template, answers)` — подстановка значений.

