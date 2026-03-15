# Уровень 2: Модули и структура

## Структура пакетов

```
test_api_evo_link/
├── bot.py                 # Точка входа: настройки, пул БД, роутер, polling
├── app/
│   ├── __init__.py
│   ├── config.py          # Загрузка Settings из env
│   ├── evo_client.py      # Клиент Evolink API (задачи, опрос, credits)
│   ├── deepseek_client.py # Опционально: feach (идеи/фичи по промпту)
│   ├── repo.py            # Репозиторий БД: users, prompts, promo, state
│   ├── states.py          # FSM: AuthStates, AdminStates, GenerateStates
│   ├── keyboards.py       # Inline-клавиатуры для меню и выбора опций
│   ├── prompt_utils.py    # Экспорт/импорт промптов, variable_descriptions из feach
│   ├── utils.py           # Переменные шаблона, рендер, feach-нормализация
│   └── routers/
│       ├── __init__.py    # re-export create_router
│       ├── main.py        # create_router(): все хендлеры в одном роутере
│       ├── common.py      # RouterCtx (ensure_user, run_generation, ask_next_variable, …)
│       ├── auth.py        # Хендлеры: /start, пароль, /admin, /addme (могут подключаться отдельно)
│       └── user.py        # Выбор промпта, ввод переменных, генерация (альтернативная разбивка)
├── tests/
│   └── test_deepseek_client.py
└── docs/
    ├── LEVEL_1_OVERVIEW.md
    ├── LEVEL_2_MODULES.md
    └── LEVEL_3_IMPLEMENTATION.md
```

## Роли модулей

| Модуль | Назначение |
|--------|------------|
| **config** | `Settings`: BOT_TOKEN, USER_PASSWORD, ADMIN_IDS, DATABASE_URL, API_KEY, API_BASE_URL, IMAGE_*, POLL_*, TASK_TIMEOUT. Обязательные проверки при загрузке. |
| **repo** | Единственная точка работы с БД: таблицы users, prompts, bot_state, promo_codes, promo_redemptions; методы upsert_user, get_user_balance, add_user_balance, consume_generation_token, list_prompts, get_prompt_by_id, CRUD промптов и промокодов, redeem_promo_code. |
| **evo_client** | Evolink: create_task(prompt, image_urls), get_task(task_id), wait_for_completion(..., on_progress), get_credits(). |
| **deepseek_client** | По промпту/идее — запрос к API для получения idea + features (feach); опциональная зависимость. |
| **states** | Группы состояний FSM для авторизации, админ-действий и сценария генерации (ожидание переменной). |
| **keyboards** | Функции вида build_*_menu(...), возвращающие InlineKeyboardMarkup для главного меню, админки, редактирования промптов/переменных, выбора опций генерации, промокодов. |
| **prompt_utils** | build_prompt_export_payload, variable_descriptions_from_features — связь между шаблоном, variable_descriptions и форматом feach. |
| **utils** | extract_variables, variable_token, render_prompt, ensure_dict, нормализация feach для хранения, ключи опций. |

## Роутеры

- **main.py** — экспортирует `create_router(repo, settings, evo, bot, deepseek)`. Внутри создаётся один `Router`, в нём регистрируются все хендлеры (команды, callback'и, FSM). Часть логики вынесена в хелперы и замыкания (ensure_user, show_prompt_buttons, get_variable_config, run_generation и т.д.).
- **common.py** — класс `RouterCtx`: принимает repo, settings, evo, bot, deepseek; реализует ensure_user, show_prompt_buttons, get_variable_config, normalize_variable_descriptions_for_template, show_prompt_edit_actions, persist_prompt_edit_state, run_generation, ask_next_variable, telegram_file_url и др. Используется в auth.py и user.py.
- **auth.py** — `register_auth(router, ctx)`: /start, ожидание пароля, /admin, /addme, текст "admin".
- **user.py** — `register_user(router, ctx)`: callback выбора промпта, ввод переменных (кнопки/текст), запуск генерации.

Текущая сборка в `bot.py` подключает только роутер из `main.py` (create_router). Модули auth и user можно подключить вместо или вместе с main для другой разбивки хендлеров.

## Зависимости между модулями

- `bot.py` → config, evo_client, repo, routers (create_router); опционально deepseek_client.
- `routers/main.py` → config, evo_client, keyboards, prompt_utils, repo, states, utils; создаёт логику, аналогичную auth + user + админка.
- `routers/common.py` → config, evo_client, keyboards, repo, states, utils.
- `routers/auth.py`, `routers/user.py` → states, keyboards (частично), common.RouterCtx.
- `repo` → asyncpg (и только контракт с БД).
- `evo_client` → config, aiohttp.
- `prompt_utils` → utils (extract_variables, variable_token, ensure_dict, make_option_key, ensure_unique_option_key).

Дальше: [Уровень 3 — Детали реализации](LEVEL_3_IMPLEMENTATION.md).
