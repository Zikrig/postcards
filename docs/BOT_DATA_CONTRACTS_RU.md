# Контракты данных (шаблоны, переменные, JSON)

## 1) Template placeholders
Парсинг переменных делается в `app/utils.py`:
- `[VAR]` — переменная типа `"image"`
- `<VAR>` — переменная типа `"text"`

Отсюда:
- `extract_variables(template)` возвращает список `{name, type}` в порядке появления (без дублей)
- `variable_token({"name","type"})` возвращает строку `[NAME]` или `<NAME>`
- `render_prompt(template, answers)` подставляет значения:
  - заменяет и `[key]`, и `<key>` (по ключам из `answers`)

## 2) variable_descriptions в БД (`prompts.variable_descriptions`)
Это JSON-словарь, где ключ — “токен переменной”:
- для image: ключ `[VAR]`
- для text: ключ `<VAR>`

Типовая структура (для text):
```
{
  "<STYLE>": {
    "description": "подсказка пользователю",
    "options": ["Option A", "Option B"],
    "allow_custom": true,
    "type": "text"
  }
}
```

Правила:
- Для `type == "image"` `options` игнорируются, UI принимает фото.
- Для `type == "text"`:
  - если есть `options`, то бот показывает inline-кнопки
  - если `allow_custom == true`, доступна кнопка `My own` и ввод произвольного текста.

Нормализация делается в `RouterCtx.normalize_variable_descriptions_for_template(...)`.

## 3) feach.json (DeepSeek)
`app/deepseek_client.py` использует строгий формат ответа DeepSeek:
- JSON с ключами: `"idea"` и `"features"`
- В `features`:
  - один ключ MUST быть `"style"`
  - остальное: feature1..featureN
  - у каждой feature:
    - `varname` (латинское имя)
    - `about` (описание для UI)
    - `options` — объект опций (ключ->текст)

Далее `app/utils.normalize_feach_for_storage(...)` приводит response к формату хранения:
- `options` превращаются в `{opt_key: {text, enabled}}`
- добавляется `my_own` и `custom`
- гарантируется наличие `"character_position"` (как опционально включаемая feature).

## 4) Итоговая генерация
Собранные пользователем `answers` + `template` -> `render_prompt`.
Дальше `EvoClient.create_task(prompt, image_urls, quality)` отправляет:
- `model`, `prompt`, `size`, `quality`
- при наличии `image_urls` добавляет `image_urls`.

Poll до `completed/failed` через `EvoClient.wait_for_completion(...)`.

