# Логика работы (flow) — актуально

## Пользовательский поток: `/start` -> выбор промпта -> генерация
1. `/start`
   - Хендлер: `app/routers/user/auth.py` (`register_user_auth.start_handler`).
   - Бот:
     - гарантирует запись пользователя в БД (`ctx.ensure_user`)
     - если в сообщении есть payload после `/start ...`, пытается погасить промокод (`redeem_promo_code`)
     - при старте делает пользователя “authorized” автоматически (логика пароля в коде фактически выключена)
     - показывает greeting (если хранится в `Repo.settings`), иначе текст приветствия + кнопки промптов
     - показывает меню промптов через `ctx.show_prompt_buttons(...)`

2. Выбор промпта из кнопок
   - Хендлер превью: `app/routers/user/generation.py` (`prompt_preview_callback`).
   - Бот:
     - загружает промпт из БД
     - показывает “карточку” промпта (кнопки `Generate`, `Back`)
     - если есть `example_file_ids` — отправляет пример фото и подпись

3. Нажатие `Generate`
   - Хендлер старта: `prompt_generate_start_callback` (тот же файл).
   - Бот:
     - извлекает переменные из `prompt.template` (`extract_variables`)
     - запускает FSM: `GenerateStates.waiting_variable`
     - кладет в FSM:
       - `template`, `variables`, `current_idx`, `answers`, `image_urls`
       - `variable_descriptions` (для подсказок и кнопок)
       - `reference_photo_file_id` (если он есть у промпта)
     - если есть reference photo — добавляет URL этой фотографии в `image_urls`

4. Сбор значений переменных (FSM)
   - Хендлер: `collect_variable_value` (для текстовых input-ов и image переменных).
   - Для каждой переменной:
     - если `type == "image"`: бот требует `message.photo`, берет `file_id` -> `telegram_file_url`
     - если `type == "text"`:
       - если есть `options` и пользователь не выбрал `My own`, ждет inline-кнопки `gen:opt:{idx}`
       - если разрешено `allow_custom`, то по `gen:myown` принимает произвольный текст
   - После последней переменной вызывается `ctx.run_generation(...)`.

5. Генерация через Evolink
   - Метод: `app/routers/common.py` (`RouterCtx.run_generation`).
   - Биллинг:
     - у админа кредиты не списываются
     - у обычного пользователя списывается `consume_tokens(...)` (по `generation_cost` из FSM)
   - Генерация:
     - `final_prompt = render_prompt(template, answers)`
     - `task_id = evo.create_task(final_prompt, image_urls, quality=generation_quality)`
     - `details = evo.wait_for_completion(task_id, on_progress=...)`
     - если `completed` -> отправка фото из `details["results"]`
     - если `failed` -> выводится человекочитаемая ошибка (в т.ч. `content_policy_violation`)
   - Завершение:
     - показывается новый баланс
     - дергается `maybe_notify_admins_balance_checkpoint` (оповещения админам при падении bucket-уровней)
     - FSM очищается и возвращается меню промптов

## Админ: управление промптами, переменными, промокодами
### Промпт (создание/редактирование)
- Блоки админки расположены в `app/routers/admin/*`.
- Основной флоу создания промпта:
  - `admin:pw:add` -> `AdminStates.waiting_prompt_title` -> `AdminStates.waiting_prompt_template`
  - затем заполнение `variable_descriptions` (описание, варианты, allow_custom)
  - опционально `reference` (фото) и `examples` (1-3 фото)

### DeepSeek “feach” и финальный wizard
- “Драфт” создается через DeepSeek в `admin_gen_start/admin_gen_idea` (см. `app/routers/admin/prompts.py`).
- Конфигурация переменных (features):
  - `app/routers/shared/features.py` обрабатывает `admin:feach:*`, `admin:opt:*`, добавление custom опций, удаление и т.д.
- Финальный wizard перед генерацией `template`:
  - `app/routers/shared/features.py` + `app/final_prompt_wizard.py`
  - логика: из `feach_data.features` строится список wizard шагов (`build_final_setup_steps`)
  - после выбора собирается `variables_spec`, вызывается `DeepSeekClient.generate_final_prompt`
  - получившийся `template` и `variable_descriptions` сохраняются в `Repo.update_prompt(...)` / `update_prompt_description(...)`

## Пользователь: “My prompts” и первичная настройка draft
- `app/routers/user/my_prompts.py` добавляет:
  - список “My prompts” (`menu:my_prompts:*`)
  - просмотр промпта с полным меню (редактирование возможно владельцу или админам)
  - создание чернового промпта через `menu:create_prompt`:
    - платится 2 токена
    - вызывается `DeepSeekClient.refine_idea`
    - сохраняется draft prompt
    - затем пользователь проходит `PrimaryPromptOnboardingStates.reviewing_variables`
      (управление features опциями в порядке, указанном `feach_data.features`)

