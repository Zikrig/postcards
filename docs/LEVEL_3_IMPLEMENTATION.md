# Уровень 3: Детали реализации

## Схема БД (PostgreSQL)

### users
| Колонка | Тип | Описание |
|---------|-----|----------|
| id | SERIAL | PK |
| tg_id | BIGINT UNIQUE | Telegram user id |
| username | TEXT | @username |
| full_name | TEXT | Имя в Telegram |
| is_authorized | BOOLEAN | Прошёл проверку пароля |
| is_admin | BOOLEAN | Флаг админа (из ADMIN_IDS) |
| balance_tokens | INTEGER | Баланс токенов для генераций |
| created_at | TIMESTAMPTZ | |

### prompts
| Колонка | Тип | Описание |
|---------|-----|----------|
| id | SERIAL | PK |
| title | TEXT UNIQUE | Название промпта |
| template | TEXT | Шаблон с плейсхолдерами \<NAME\> и [NAME] |
| variable_descriptions | JSONB | Конфиг переменных (см. ниже) |
| reference_photo_file_id | TEXT | file_id референсного фото (Telegram) |
| created_by | BIGINT | tg_id создателя |
| created_at | TIMESTAMPTZ | |
| is_active | BOOLEAN | Показывать в меню пользователю |
| feach_data | JSONB | Кэш ответа DeepSeek feach (идея, фичи) |
| example_file_ids | JSONB | Массив file_id примеров (до 3) |

### bot_state
| Колонка | Тип | Описание |
|---------|-----|----------|
| key | TEXT | PK |
| value | TEXT | Произвольное значение (например, last_user_remaining_bucket_20) |

### promo_codes
| Колонка | Тип | Описание |
|---------|-----|----------|
| id | SERIAL | PK |
| code | TEXT UNIQUE | Код промокода |
| credits_amount | INTEGER | Сколько токенов начислять |
| max_uses | INTEGER | NULL = без лимита |
| uses_count | INTEGER | Сколько раз уже использован |
| is_active | BOOLEAN | |
| created_by | BIGINT | tg_id создателя |
| created_at | TIMESTAMPTZ | |

### promo_redemptions
Связь пользователь–промокод (один пользователь не может погасить один промокод дважды).

---

## Шаблон промпта и переменные

- **Текстовая переменная:** в шаблоне задаётся как `\<VarName\>` (например `\<STYLE\>`). В БД и в коде переменная имеет тип `"text"`.
- **Переменная-изображение:** в шаблоне задаётся как `[VarName]` (например `[USER_PHOTO]`). Тип `"image"`; пользователь присылает фото, бот подставляет URL файла в финальный промпт при вызове Evolink.

Регулярное выражение в `utils.VARIABLE_RE`: `\[([^\[\]<>]+)\]|<([^<>]+)>` — сначала ищутся `[...]`, затем `<...>`.

- **variable_token(var):** для image — `"[{name}]"`, для text — `"<{name}>"`.
- **render_prompt(template, answers):** подставляет в шаблон значения по ключам в `answers` (ключ — имя переменной), заменяя и `[name]`, и `<name>`.

---

## variable_descriptions (JSONB)

Ключ — токен переменной (`\<Name\>` или `[Name]`). Значение — либо строка (старый формат: только описание), либо объект:

```json
{
  "description": "Текст подсказки пользователю",
  "options": ["Option A", "Option B"],
  "allow_custom": true,
  "type": "text"
}
```

- **description** — показывается пользователю перед вводом.
- **options** — для типа text: кнопки выбора; если одна опция и `allow_custom === false`, подставляется автоматически.
- **allow_custom** — для text: показывать ли кнопку «My own» и принимать произвольный текст.
- **type** — `"text"` или `"image"`.

Для типа `image` опции и allow_custom в UI не используются.

---

## FSM (states.py)

- **AuthStates:** `waiting_password` — ожидание пароля после /start.
- **AdminStates:** множество состояний для создания/редактирования промпта (ожидание заголовка, шаблона, описаний переменных, опций, референса, примеров, промокода и т.д.).
- **GenerateStates:** `waiting_variable` — пользователь вводит значения переменных по очереди; после последней вызывается `run_generation`.

Данные в FSM при генерации: `prompt_title`, `template`, `variables`, `current_idx`, `answers`, `image_urls`, `variable_descriptions`, `reference_photo_file_id`, `awaiting_custom_for`, `request_user_id`, `request_username`, `request_full_name`.

---

## Repo: основные методы

| Метод | Назначение |
|-------|------------|
| init() | CREATE TABLE IF NOT EXISTS для всех таблиц, ALTER для недостающих колонок |
| upsert_user(tg_id, username, full_name, is_admin) | INSERT/UPDATE users, RETURNING * |
| get_user(tg_id), get_user_by_username(username) | Поиск пользователя |
| set_user_authorized(tg_id, value) | Обновить is_authorized |
| get_user_balance(tg_id), add_user_balance(tg_id, amount) | Баланс |
| consume_generation_token(tg_id) | balance_tokens - 1 при balance_tokens > 0; возвращает новый баланс или None |
| list_prompts(active_only) | Список промптов |
| get_prompt_by_id(id), get_prompt_by_title(title) | Один промпт |
| insert_prompt(...), update_prompt(...) | Создание/обновление промпта |
| set_prompt_active(prompt_id, is_active) | Вкл/выкл в меню |
| update_prompt_feach_data(prompt_id, feach_data), set_prompt_examples(prompt_id, example_file_ids) | Доп. данные промпта |
| get_state_value(key), set_state_value(key, value) | bot_state key-value |
| create_promo_code(...), list_promo_codes(), get_promo_code_by_id(...), update_promo_code(...), delete_promo_code(...) | Промокоды |
| redeem_promo_code(code, user_tg_id) | Погашение: (success, message, granted) |

---

## EvoClient (Evolink API)

- **create_task(prompt: str, image_urls: list[str])** — POST `/v1/images/generations`; в теле: model, prompt, size, quality, при необходимости image_urls. Возвращает `task_id` (id из ответа).
- **get_task(task_id)** — GET `/v1/tasks/{task_id}`; возвращает объект со статусом и результатами.
- **wait_for_completion(task_id, on_progress=None)** — цикл опроса get_task до status in (`completed`, `failed`) или до истечения `task_timeout_seconds`; при каждом шаге вызывается `on_progress(status, progress)`.
- **get_credits()** — GET `/v1/credits` (для уведомлений админам о порогах баланса API).

Результат успешной задачи: `details["results"]` — список URL изображений.

---

## Префиксы callback_data (inline-кнопки)

- **prompt:select:{id}** — выбор промпта пользователем.
- **gen:opt:{idx}** — выбор опции для текстовой переменной; **gen:myown** — «своё» значение.
- **admin:*** — все действия админки: меню (admin:prompts, admin:promo и т.д.), редактирование промпта (admin:edit, admin:editpart:title, admin:editpart:template, admin:editpart:variables, admin:editvar:...), референс (admin:editpart:ref:set/clear), примеры (admin:editpart:examples), удаление (admin:delete, admin:delete_confirm), feach (admin:feach, admin:opt, admin:featdel, admin:myown, admin:featadd, admin:featdone, admin:optview, admin:final), тест (admin:test), экспорт (admin:export), активность (admin:active), промокоды (admin:promo:item, admin:promo:edit, admin:promo:delete), список промптов админки (admin:pw:item), разрешение «своё» (admin:allow_custom:yes/no).

Формат данных в callback обычно: префикс и id в конце после последнего `:` (например `admin:edit:123` → prompt_id 123).

---

## run_generation (логика генерации)

1. Взять из state: `request_user_id`, `template`, `answers`, `image_urls`, `prompt_title`.
2. **consume_generation_token(user_tg_id)** — при неудаче (нет баланса) — сообщение и возврат к кнопкам промптов.
3. **render_prompt(template, answers)** → итоговый текст промпта.
4. Отправить сообщение «Generating… Status: queued».
5. **evo.create_task(final_prompt, image_urls)** → task_id.
6. **evo.wait_for_completion(task_id, on_progress=...)** — редактировать сообщение статусом/прогрессом.
7. При status != "completed" — сообщение об ошибке (в т.ч. content_policy_violation).
8. Из details["results"] отправить каждое фото через message.answer_photo(photo=url).
9. Сообщение с новым балансом; при необходимости — **maybe_notify_admins_balance_checkpoint** (пороги по 20 кредитов Evolink).
10. state.clear(), показать кнопки промптов.

---

## Экспорт/импорт промптов (prompt_utils)

- **build_prompt_export_payload(prompt_record)** — JSON для экспорта: title, template, idea (пусто), features (по переменным из шаблона: varname, about, options как объект ключ→текст, my_own). Без reference_photo_file_id, feach_data, example_file_ids, is_active.
- **variable_descriptions_from_features(template, features)** — по шаблону и объекту features (feach-формат) собирает variable_descriptions для сохранения в БД.

Формат feach (DeepSeek): idea + features[key] с полями varname, about, options (ключ→текст или {text, enabled}), my_own. **normalize_feach_for_storage** в utils приводит options к виду {key: {text, enabled}} для хранения.

---

## Переменные окружения (.env)

Обязательные: **BOT_TOKEN**, **USER_PASSWORD**, **API_KEY**.

Остальное: **ADMIN_IDS** (через запятую), **DATABASE_URL** или **DB_HOST**, **DB_PORT**, **DB_NAME**, **DB_USER**, **DB_PASSWORD**, **API_BASE_URL**, **IMAGE_MODEL**, **IMAGE_SIZE**, **IMAGE_QUALITY**, **POLL_INTERVAL_SECONDS**, **TASK_TIMEOUT_SECONDS**.

См. также: [Уровень 1 — Обзор](LEVEL_1_OVERVIEW.md), [Уровень 2 — Модули](LEVEL_2_MODULES.md).
