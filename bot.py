import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp
import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

try:
    from deepseek_client import DeepSeekClient
except ImportError:
    DeepSeekClient = None  # type: ignore[misc, assignment]


def parse_admin_ids(raw: str) -> set[int]:
    result: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            result.add(int(part))
    return result


@dataclass
class Settings:
    bot_token: str
    user_password: str
    admin_ids: set[int]
    database_url: str
    api_key: str
    api_base_url: str
    image_model: str
    image_size: str
    image_quality: str
    poll_interval_seconds: float
    task_timeout_seconds: int


def load_settings() -> Settings:
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    user_password = os.getenv("USER_PASSWORD", "").strip()
    api_key = os.getenv("API_KEY", "").strip()

    if not bot_token:
        raise RuntimeError("BOT_TOKEN is required in .env")
    if not user_password:
        raise RuntimeError("USER_PASSWORD is required in .env")
    if not api_key:
        raise RuntimeError("API_KEY is required in .env")

    db_host = os.getenv("DB_HOST", "db")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "botdb")
    db_user = os.getenv("DB_USER", "botuser")
    db_password = os.getenv("DB_PASSWORD", "botpassword")
    database_url = os.getenv(
        "DATABASE_URL",
        f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}",
    )

    return Settings(
        bot_token=bot_token,
        user_password=user_password,
        admin_ids=parse_admin_ids(os.getenv("ADMIN_IDS", "")),
        database_url=database_url,
        api_key=api_key,
        api_base_url=os.getenv("API_BASE_URL", "https://api.evolink.ai"),
        image_model=os.getenv("IMAGE_MODEL", "gemini-3.1-flash-image-preview"),
        image_size=os.getenv("IMAGE_SIZE", "9:16"),
        image_quality=os.getenv("IMAGE_QUALITY", "1K"),
        poll_interval_seconds=float(os.getenv("POLL_INTERVAL_SECONDS", "3")),
        task_timeout_seconds=int(os.getenv("TASK_TIMEOUT_SECONDS", "120")),
    )


class AuthStates(StatesGroup):
    waiting_password = State()


class AdminStates(StatesGroup):
    waiting_prompt_title = State()
    waiting_prompt_template = State()
    waiting_prompt_edit_title = State()
    waiting_prompt_edit_template = State()
    waiting_prompt_edit_variable_name = State()
    waiting_prompt_edit_variable_description = State()
    waiting_prompt_edit_variable_options = State()
    waiting_variable_description = State()
    waiting_text_options = State()
    waiting_text_allow_custom = State()
    waiting_prompt_reference = State()
    waiting_gen_title = State()
    waiting_gen_idea = State()
    waiting_feach_add_option = State()
    waiting_import_json = State()
    waiting_prompt_examples = State()
    waiting_promo_code = State()
    waiting_promo_credits = State()
    waiting_promo_max_uses = State()


class GenerateStates(StatesGroup):
    waiting_variable = State()


VARIABLE_RE = re.compile(r"\[([^\[\]<>]+)\]|<([^<>]+)>")


def extract_variables(template: str) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in VARIABLE_RE.finditer(template):
        if m.group(1):
            var_name = m.group(1).strip()
            var_type = "image"
        else:
            var_name = (m.group(2) or "").strip()
            var_type = "text"

        key = (var_name, var_type)
        if var_name and key not in seen:
            values.append({"name": var_name, "type": var_type})
            seen.add(key)
    return values


def variable_token(variable: dict[str, str]) -> str:
    name = variable["name"]
    if variable["type"] == "image":
        return f"[{name}]"
    return f"<{name}>"


def render_prompt(template: str, answers: dict[str, str]) -> str:
    result = template
    for key, value in answers.items():
        result = result.replace(f"[{key}]", value)
        result = result.replace(f"<{key}>", value)
    return result


def ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def normalize_feach_for_storage(api_feach: dict[str, Any]) -> dict[str, Any]:
    """Convert DeepSeek feach response to storage format: options as {text, enabled}, my_own, custom."""
    idea = api_feach.get("idea", "")
    features = api_feach.get("features") or {}
    out_features: dict[str, Any] = {}
    for key, feat in features.items():
        if not isinstance(feat, dict):
            continue
        opts = feat.get("options") or {}
        if not isinstance(opts, dict):
            opts = {}
        normalized_opts: dict[str, Any] = {}
        used_opt_keys: set[str] = set()
        for opt_k, opt_v in opts.items():
            if isinstance(opt_v, dict) and "text" in opt_v:
                text = str(opt_v.get("text", ""))
                enabled = bool(opt_v.get("enabled", True))
            else:
                text = str(opt_v) if opt_v is not None else ""
                enabled = True
            base_key = make_option_key(text, max_length=20)
            norm_key = ensure_unique_option_key(base_key, used_opt_keys, max_length=20)
            used_opt_keys.add(norm_key)
            normalized_opts[norm_key] = {"text": text, "enabled": enabled}
        out_features[key] = {
            "varname": feat.get("varname", key),
            "about": feat.get("about", ""),
            "options": normalized_opts,
            "my_own": feat.get("my_own", True),
            "custom": list(feat.get("custom") or []),
        }
    return {"idea": idea, "features": out_features}


def get_feach_option_text(opt_value: Any) -> str:
    if isinstance(opt_value, dict) and "text" in opt_value:
        return str(opt_value.get("text", ""))
    return str(opt_value) if opt_value is not None else ""


def get_feach_option_enabled(opt_value: Any) -> bool:
    if isinstance(opt_value, dict):
        return bool(opt_value.get("enabled", True))
    return True


def make_option_key(text: str, max_length: int = 20) -> str:
    """Осмысленный ключ опции из текста: только a-z, 0-9, _, не длиннее max_length (для JSON и callback_data)."""
    s = (text or "").strip().lower()
    if not s:
        return "opt"
    # Только латиница, цифры, пробелы; пробелы -> _
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    if not s:
        return "opt"
    return s[:max_length].rstrip("_")


def btn_label(text: str, max_length: int = 20) -> str:
    """Подпись кнопки не длиннее max_length символов."""
    s = (text or "").strip()
    return s[:max_length] if len(s) > max_length else s


def ensure_unique_option_key(base_key: str, existing: set[str], max_length: int = 20) -> str:
    """Уникальный ключ: base_key или base_key_2, base_key_3, ... (укладываемся в max_length)."""
    key = (base_key or "opt")[:max_length]
    if key not in existing:
        return key
    for i in range(2, 100):
        suffix = f"_{i}"
        candidate = (base_key or "opt")[: max_length - len(suffix)] + suffix
        if candidate not in existing:
            return candidate
    return base_key[:max_length] + "_0"


def build_prompt_export_payload(prompt_record: Any) -> dict[str, Any]:
    """
    Build export JSON: title, template, idea, features (feach-like).
    No reference_photo_file_id, feach_data, example_file_ids, is_active.
    """
    title = str(prompt_record.get("title") or "")
    template = str(prompt_record.get("template") or "")
    var_desc = ensure_dict(prompt_record.get("variable_descriptions") or {})
    variables = extract_variables(template)
    features: dict[str, Any] = {}
    for var in variables:
        # Переменные в квадратных скобках [USER_PHOTO] — плейсхолдер для фото, не экспортируем в features
        if var["type"] == "image":
            continue
        token = variable_token(var)
        raw = var_desc.get(token)
        if isinstance(raw, str):
            config = {"description": raw, "options": [], "allow_custom": True}
        elif isinstance(raw, dict):
            config = {
                "description": str(raw.get("description") or ""),
                "options": [str(x) for x in (raw.get("options") or []) if str(x).strip()],
                "allow_custom": bool(raw.get("allow_custom", True)),
            }
        else:
            config = {"description": "", "options": [], "allow_custom": True}
        key = var["name"].lower().replace(" ", "_")
        options_obj: dict[str, str] = {}
        used_keys: set[str] = set()
        for opt in config["options"]:
            base_key = make_option_key(opt, max_length=20)
            opt_key = ensure_unique_option_key(base_key, used_keys, max_length=20)
            used_keys.add(opt_key)
            options_obj[opt_key] = opt
        features[key] = {
            "varname": var["name"],
            "about": config["description"],
            "options": options_obj,
            "my_own": config["allow_custom"],
        }
    return {"title": title, "template": template, "idea": "", "features": features}


def variable_descriptions_from_features(template: str, features: dict[str, Any]) -> dict[str, Any]:
    """
    Build variable_descriptions from template placeholders and feach-like features.
    [USER_PHOTO] и прочие переменные в [] — всегда тип image (фото), не берём для них данные из features.
    features только для текстовых переменных <NAME>.
    """
    variables = extract_variables(template)
    var_desc: dict[str, Any] = {}
    for var in variables:
        token = variable_token(var)
        # Переменные в квадратных скобках — плейсхолдер для приложенного фото, не смешиваем с текстовыми features
        if var["type"] == "image":
            var_desc[token] = {
                "description": "",
                "options": [],
                "allow_custom": True,
                "type": "image",
            }
            continue
        key = var["name"].lower().replace(" ", "_")
        feat = (features or {}).get(key)
        if not feat or not isinstance(feat, dict):
            name_upper = var["name"].upper().replace(" ", "_")
            for f in (features or {}).values():
                if isinstance(f, dict) and (f.get("varname") or "").upper().replace(" ", "_") == name_upper:
                    feat = f
                    break
        if feat and isinstance(feat, dict):
            about = str(feat.get("about") or "")
            opts = feat.get("options")
            if isinstance(opts, dict):
                opts = [str(v) for v in opts.values() if str(v).strip()]
            elif isinstance(opts, list):
                opts = [str(x) for x in opts if str(x).strip()]
            else:
                opts = []
            var_desc[token] = {
                "description": about,
                "options": opts,
                "allow_custom": bool(feat.get("my_own", True)),
                "type": "text",
            }
        else:
            var_desc[token] = {
                "description": "",
                "options": [],
                "allow_custom": True,
                "type": "text",
            }
    return var_desc


class EvoClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

    async def create_task(self, prompt: str, image_urls: list[str]) -> str:
        payload: dict[str, Any] = {
            "model": self.settings.image_model,
            "prompt": prompt,
            "size": self.settings.image_size,
            "quality": self.settings.image_quality,
        }
        if image_urls:
            payload["image_urls"] = image_urls

        url = f"{self.settings.api_base_url}/v1/images/generations"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=payload, timeout=90) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"Create task failed [{resp.status}]: {text}")
                data = await resp.json()

        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"Task id not found in response: {data}")
        return task_id

    async def get_task(self, task_id: str) -> dict[str, Any]:
        url = f"{self.settings.api_base_url}/v1/tasks/{task_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=45,
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"Get task failed [{resp.status}]: {text}")
                return await resp.json()

    async def wait_for_completion(
        self,
        task_id: str,
        on_progress: Optional[Any] = None,
    ) -> dict[str, Any]:
        started = time.time()
        while True:
            details = await self.get_task(task_id)
            status = details.get("status")
            progress = details.get("progress")
            if on_progress is not None:
                await on_progress(status, progress)
            if status in {"completed", "failed"}:
                return details
            if time.time() - started > self.settings.task_timeout_seconds:
                raise TimeoutError("Task polling timeout")
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def get_credits(self) -> dict[str, Any]:
        url = f"{self.settings.api_base_url}/v1/credits"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=30,
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"Get credits failed [{resp.status}]: {text}")
                return await resp.json()


class Repo:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def init(self) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    tg_id BIGINT UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    is_authorized BOOLEAN NOT NULL DEFAULT FALSE,
                    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
                    balance_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS balance_tokens INTEGER NOT NULL DEFAULT 0;
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompts (
                    id SERIAL PRIMARY KEY,
                    title TEXT UNIQUE NOT NULL,
                    template TEXT NOT NULL,
                    variable_descriptions JSONB NOT NULL DEFAULT '{}'::jsonb,
                    reference_photo_file_id TEXT,
                    created_by BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN IF NOT EXISTS variable_descriptions JSONB NOT NULL DEFAULT '{}'::jsonb;
                """
            )
            await conn.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
                """
            )
            await conn.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN IF NOT EXISTS feach_data JSONB;
                """
            )
            await conn.execute(
                """
                ALTER TABLE prompts
                ADD COLUMN IF NOT EXISTS example_file_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_codes (
                    id SERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    credits_amount INTEGER NOT NULL,
                    max_uses INTEGER,
                    uses_count INTEGER NOT NULL DEFAULT 0,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_by BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_redemptions (
                    id SERIAL PRIMARY KEY,
                    promo_id INTEGER NOT NULL REFERENCES promo_codes(id) ON DELETE CASCADE,
                    user_tg_id BIGINT NOT NULL,
                    redeemed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE (promo_id, user_tg_id)
                );
                """
            )

    async def upsert_user(
        self,
        tg_id: int,
        username: str,
        full_name: str,
        is_admin: bool,
    ) -> asyncpg.Record:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (tg_id, username, full_name, is_authorized, is_admin)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (tg_id) DO UPDATE
                SET username = EXCLUDED.username,
                    full_name = EXCLUDED.full_name,
                    is_admin = users.is_admin OR EXCLUDED.is_admin,
                    is_authorized = CASE WHEN EXCLUDED.is_admin THEN TRUE ELSE users.is_authorized END
                RETURNING *;
                """,
                tg_id,
                username,
                full_name,
                is_admin,
                is_admin,
            )
            return row

    async def get_user(self, tg_id: int) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE tg_id = $1", tg_id)

    async def get_user_by_username(self, username: str) -> Optional[asyncpg.Record]:
        name = (username or "").strip().lstrip("@")
        if not name:
            return None
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM users WHERE username = $1", name)

    async def set_user_authorized(self, tg_id: int, value: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET is_authorized = $1 WHERE tg_id = $2",
                value,
                tg_id,
            )

    async def get_user_balance(self, tg_id: int) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT balance_tokens FROM users WHERE tg_id = $1", tg_id)
            if not row:
                return 0
            return int(row["balance_tokens"] or 0)

    async def add_user_balance(self, tg_id: int, amount: int) -> int:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE users
                SET balance_tokens = balance_tokens + $1
                WHERE tg_id = $2
                RETURNING balance_tokens
                """,
                amount,
                tg_id,
            )
            if not row:
                return 0
            return int(row["balance_tokens"] or 0)

    async def consume_generation_token(self, tg_id: int) -> Optional[int]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE users
                SET balance_tokens = balance_tokens - 1
                WHERE tg_id = $1 AND balance_tokens > 0
                RETURNING balance_tokens
                """,
                tg_id,
            )
            if not row:
                return None
            return int(row["balance_tokens"] or 0)

    async def list_prompts(self, active_only: bool = False) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            if active_only:
                return await conn.fetch(
                    "SELECT * FROM prompts WHERE is_active = TRUE ORDER BY id DESC"
                )
            return await conn.fetch("SELECT * FROM prompts ORDER BY id DESC")

    async def get_prompt_by_title(self, title: str) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM prompts WHERE title = $1", title)

    async def get_prompt_by_id(self, prompt_id: int) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM prompts WHERE id = $1", prompt_id)

    async def insert_prompt(
        self,
        title: str,
        template: str,
        variable_descriptions: dict[str, Any],
        reference_photo_file_id: Optional[str],
        created_by: int,
        is_active: bool = True,
        feach_data: Optional[dict[str, Any]] = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO prompts (title, template, variable_descriptions, reference_photo_file_id, created_by, is_active, feach_data)
                VALUES ($1, $2, $3::jsonb, $4, $5, $6, $7::jsonb)
                """,
                title,
                template,
                json.dumps(variable_descriptions),
                reference_photo_file_id,
                created_by,
                is_active,
                json.dumps(feach_data) if feach_data is not None else None,
            )

    async def update_prompt(
        self,
        prompt_id: int,
        title: str,
        template: str,
        variable_descriptions: dict[str, Any],
        reference_photo_file_id: Optional[str],
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE prompts
                SET title = $1,
                    template = $2,
                    variable_descriptions = $3::jsonb,
                    reference_photo_file_id = $4
                WHERE id = $5
                """,
                title,
                template,
                json.dumps(variable_descriptions),
                reference_photo_file_id,
                prompt_id,
            )

    async def set_prompt_active(self, prompt_id: int, is_active: bool) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE prompts SET is_active = $1 WHERE id = $2",
                is_active,
                prompt_id,
            )

    async def update_prompt_feach_data(
        self, prompt_id: int, feach_data: Optional[dict[str, Any]]
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE prompts SET feach_data = $1::jsonb WHERE id = $2",
                json.dumps(feach_data) if feach_data is not None else None,
                prompt_id,
            )

    async def set_prompt_examples(self, prompt_id: int, example_file_ids: list[str]) -> None:
        if len(example_file_ids) > 3:
            example_file_ids = example_file_ids[:3]
        async with self.pool.acquire() as conn:
            await conn.execute(
                "UPDATE prompts SET example_file_ids = $1::jsonb WHERE id = $2",
                json.dumps(example_file_ids),
                prompt_id,
            )

    async def get_state_value(self, key: str) -> Optional[str]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT value FROM bot_state WHERE key = $1", key)
            if not row:
                return None
            return row["value"]

    async def set_state_value(self, key: str, value: str) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bot_state (key, value)
                VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                key,
                value,
            )

    async def create_promo_code(
        self,
        code: str,
        credits_amount: int,
        max_uses: Optional[int],
        created_by: int,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO promo_codes (code, credits_amount, max_uses, created_by)
                VALUES ($1, $2, $3, $4)
                """,
                code,
                credits_amount,
                max_uses,
                created_by,
            )

    async def list_promo_codes(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM promo_codes ORDER BY id DESC")

    async def get_promo_code_by_id(self, promo_id: int) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM promo_codes WHERE id = $1", promo_id)

    async def update_promo_code(
        self,
        promo_id: int,
        code: str,
        credits_amount: int,
        max_uses: Optional[int],
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE promo_codes
                SET code = $1,
                    credits_amount = $2,
                    max_uses = $3
                WHERE id = $4
                """,
                code,
                credits_amount,
                max_uses,
                promo_id,
            )

    async def delete_promo_code(self, promo_id: int) -> bool:
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM promo_codes WHERE id = $1", promo_id)
            return result.endswith("1")

    async def redeem_promo_code(self, code: str, user_tg_id: int) -> tuple[bool, str, int]:
        """
        Returns: (success, message, granted_credits)
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                promo = await conn.fetchrow(
                    """
                    SELECT *
                    FROM promo_codes
                    WHERE code = $1 AND is_active = TRUE
                    FOR UPDATE
                    """,
                    code,
                )
                if not promo:
                    return False, "Promo code is invalid or inactive.", 0

                if promo["max_uses"] is not None and promo["uses_count"] >= promo["max_uses"]:
                    return False, "Promo code usage limit reached.", 0

                already_used = await conn.fetchrow(
                    """
                    SELECT 1
                    FROM promo_redemptions
                    WHERE promo_id = $1 AND user_tg_id = $2
                    """,
                    promo["id"],
                    user_tg_id,
                )
                if already_used:
                    return False, "You have already used this promo code.", 0

                await conn.execute(
                    """
                    INSERT INTO promo_redemptions (promo_id, user_tg_id)
                    VALUES ($1, $2)
                    """,
                    promo["id"],
                    user_tg_id,
                )
                await conn.execute(
                    """
                    UPDATE promo_codes
                    SET uses_count = uses_count + 1
                    WHERE id = $1
                    """,
                    promo["id"],
                )
                row = await conn.fetchrow(
                    """
                    UPDATE users
                    SET balance_tokens = balance_tokens + $1
                    WHERE tg_id = $2
                    RETURNING balance_tokens
                    """,
                    int(promo["credits_amount"]),
                    user_tg_id,
                )
                new_balance = int(row["balance_tokens"] or 0) if row else 0
                return True, "Promo code applied.", int(promo["credits_amount"])


def build_main_menu(prompts: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=btn_label(str(p["title"]), 20), callback_data=f"prompt:select:{p['id']}")]
        for p in prompts
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Prompt work", callback_data="admin:prompt_work")],
            [InlineKeyboardButton(text="Promo codes", callback_data="admin:promo_menu")],
        ]
    )


def build_prompt_work_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Generate new prompt", callback_data="admin:gen:start")],
            [InlineKeyboardButton(text="List of prompts", callback_data="admin:pw:list")],
            [InlineKeyboardButton(text="Add prompt (manual)", callback_data="admin:pw:add")],
            [InlineKeyboardButton(text="Import JSON", callback_data="admin:import")],
            [InlineKeyboardButton(text="Back", callback_data="admin:pw:back")],
        ]
    )


def build_prompt_list_menu(prompts: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows = []
    for p in prompts:
        active = p.get("is_active", True)
        label = btn_label(f"{'🟢' if active else '🔴'} {p['title']}", 20)
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:pw:item:{p['id']}")])
    rows.append([InlineKeyboardButton(text="Back", callback_data="admin:prompt_work")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_item_menu(prompt_id: int, is_active: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Edit", callback_data=f"admin:edit:{prompt_id}")],
        [InlineKeyboardButton(
            text="Deactivate" if is_active else "Activate",
            callback_data=f"admin:active:{prompt_id}",
        )],
        [InlineKeyboardButton(text="Export JSON", callback_data=f"admin:export:{prompt_id}")],
        [InlineKeyboardButton(text="Delete", callback_data=f"admin:delete:{prompt_id}")],
        [InlineKeyboardButton(text="Back to list", callback_data="admin:pw:list")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_feach_menu(prompt_id: int, feach_data: dict[str, Any], is_active: bool) -> InlineKeyboardMarkup:
    """Menu for a draft prompt: idea + feature buttons, Generate final, Activate, Export, Back."""
    features = feach_data.get("features") or {}
    rows = []
    for feat_key, feat in features.items():
        label = btn_label(str((feat.get("varname") or feat_key) if isinstance(feat, dict) else feat_key), 18)
        rows.append([
            InlineKeyboardButton(text=f"🔹 {label}", callback_data=f"admin:feach:{prompt_id}:{feat_key}"),
        ])
    rows.append([
        InlineKeyboardButton(text="Generate final", callback_data=f"admin:final:{prompt_id}"),
    ])
    rows.append([
        InlineKeyboardButton(
            text="Deactivate" if is_active else "Activate",
            callback_data=f"admin:active:{prompt_id}",
        ),
    ])
    rows.append([InlineKeyboardButton(text="Export JSON", callback_data=f"admin:export:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Edit", callback_data=f"admin:edit:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Delete", callback_data=f"admin:delete:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Back to list", callback_data="admin:pw:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_feature_config_menu(
    prompt_id: int,
    feat_key: str,
    feature: dict[str, Any],
) -> InlineKeyboardMarkup:
    """Option rows (text + green/red + Del), My own, Add, Done. Option view = show full text on click."""
    opts = feature.get("options") or {}
    custom = list(feature.get("custom") or [])
    rows = []
    for opt_key, opt_val in opts.items():
        text_short = btn_label(get_feach_option_text(opt_val) or opt_key, 20)
        enabled = get_feach_option_enabled(opt_val)
        rows.append([
            InlineKeyboardButton(text=text_short, callback_data=f"admin:optview:{prompt_id}:{feat_key}:{opt_key}"),
            InlineKeyboardButton(
                text="🟢" if enabled else "🔴",
                callback_data=f"admin:opt:{prompt_id}:{feat_key}:{opt_key}:{'0' if enabled else '1'}",
            ),
            InlineKeyboardButton(text="Del", callback_data=f"admin:optdel:{prompt_id}:{feat_key}:{opt_key}"),
        ])
    for i, c in enumerate(custom):
        opt_key = f"custom_{i}"
        text_short = btn_label(c.get("text", str(c)) if isinstance(c, dict) else str(c), 20)
        enabled = c.get("enabled", True) if isinstance(c, dict) else True
        rows.append([
            InlineKeyboardButton(text=text_short, callback_data=f"admin:optview:{prompt_id}:{feat_key}:{opt_key}"),
            InlineKeyboardButton(
                text="🟢" if enabled else "🔴",
                callback_data=f"admin:opt:{prompt_id}:{feat_key}:{opt_key}:{'0' if enabled else '1'}",
            ),
            InlineKeyboardButton(text="Del", callback_data=f"admin:optdel:{prompt_id}:{feat_key}:{opt_key}"),
        ])
    my_own = feature.get("my_own", True)
    rows.append([
        InlineKeyboardButton(text=btn_label("My own (user types)", 20), callback_data=f"admin:myown:{prompt_id}:{feat_key}"),
        InlineKeyboardButton(text="ON" if my_own else "OFF", callback_data=f"admin:myown:{prompt_id}:{feat_key}"),
    ])
    rows.append([InlineKeyboardButton(text="Add option", callback_data=f"admin:featadd:{prompt_id}:{feat_key}")])
    rows.append([InlineKeyboardButton(text="Done", callback_data=f"admin:featdone:{prompt_id}:{feat_key}")])
    rows.append([InlineKeyboardButton(text="Back", callback_data=f"admin:pw:item:{prompt_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_menu(prompt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Change title", callback_data=f"admin:editpart:title:{prompt_id}")],
            [InlineKeyboardButton(text="Change template", callback_data=f"admin:editpart:template:{prompt_id}")],
            [InlineKeyboardButton(text="Edit variables", callback_data=f"admin:editpart:variables:{prompt_id}")],
            [InlineKeyboardButton(text="Replace ref. image", callback_data=f"admin:editpart:ref:set:{prompt_id}")],
            [InlineKeyboardButton(text="Remove ref. image", callback_data=f"admin:editpart:ref:clear:{prompt_id}")],
            [InlineKeyboardButton(text="Examples (1–3)", callback_data=f"admin:editpart:examples:{prompt_id}")],
            [InlineKeyboardButton(text="Back to list", callback_data="admin:pw:list")],
        ]
    )


def build_prompt_edit_variables_menu(prompt_id: int, variables: list[dict[str, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, var in enumerate(variables):
        token = variable_token(var)
        rows.append(
            [InlineKeyboardButton(text=btn_label(token, 20), callback_data=f"admin:editvar:pick:{prompt_id}:{idx}")]
        )
    rows.append([InlineKeyboardButton(text="Back to prompt edit", callback_data=f"admin:edit:{prompt_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_variable_actions_menu(
    prompt_id: int,
    var_idx: int,
    variable: dict[str, str],
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Rename variable", callback_data=f"admin:editvar:field:name:{prompt_id}:{var_idx}")],
        [InlineKeyboardButton(text="Change description", callback_data=f"admin:editvar:field:desc:{prompt_id}:{var_idx}")],
    ]
    if variable.get("type") == "text":
        rows.append(
            [InlineKeyboardButton(text="Change options", callback_data=f"admin:editvar:field:opts:{prompt_id}:{var_idx}")]
        )
        rows.append(
            [
                InlineKeyboardButton(text="My own: ON", callback_data=f"admin:editvar:allow:{prompt_id}:{var_idx}:yes"),
                InlineKeyboardButton(text="My own: OFF", callback_data=f"admin:editvar:allow:{prompt_id}:{var_idx}:no"),
            ]
        )
    rows.append([InlineKeyboardButton(text="Back to variables", callback_data=f"admin:editpart:variables:{prompt_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_promo_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Single-user promo", callback_data="admin:promo:create:single")],
            [InlineKeyboardButton(text="Multi-user promo", callback_data="admin:promo:create:multi")],
            [InlineKeyboardButton(text="Back", callback_data="admin:promo:back")],
        ]
    )


def build_promo_list_menu(promos: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=btn_label(str(p["code"]), 20), callback_data=f"admin:promo:item:{p['id']}")]
        for p in promos
    ]
    rows.extend(build_promo_menu().inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_promo_item_menu(promo_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Edit", callback_data=f"admin:promo:edit:{promo_id}")],
            [InlineKeyboardButton(text="Delete", callback_data=f"admin:promo:delete:{promo_id}")],
            [InlineKeyboardButton(text="Back to list", callback_data="admin:promo_menu")],
        ]
    )


def create_router(
    repo: Repo,
    settings: Settings,
    evo: EvoClient,
    bot: Bot,
    deepseek: Optional[Any] = None,
) -> Router:
    router = Router()
    BALANCE_BUCKET_KEY = "last_user_remaining_bucket_20"

    async def ensure_user_from_tg(tg_user: Any) -> asyncpg.Record:
        assert tg_user is not None
        full_name = (tg_user.full_name or "").strip()
        return await repo.upsert_user(
            tg_id=tg_user.id,
            username=tg_user.username or "",
            full_name=full_name,
            is_admin=tg_user.id in settings.admin_ids,
        )

    async def ensure_user(message: Message) -> asyncpg.Record:
        return await ensure_user_from_tg(message.from_user)

    def extract_start_payload(message_text: str) -> str:
        parts = (message_text or "").split(maxsplit=1)
        if len(parts) < 2:
            return ""
        return parts[1].strip()

    async def show_prompt_buttons(message: Message) -> None:
        prompts = await repo.list_prompts(active_only=True)
        if not prompts:
            await message.answer("No prompts yet. Please wait for admin to add them.")
            return
        await message.answer("Select a prompt:", reply_markup=build_main_menu(prompts))

    def get_variable_config(
        raw_configs: Any,
        token: str,
        var_type: str,
    ) -> dict[str, Any]:
        configs = ensure_dict(raw_configs)
        raw = configs.get(token)
        if isinstance(raw, str):
            # Backward compatibility with old format: {"<var>": "description"}
            return {
                "description": raw,
                "options": [],
                "allow_custom": True,
                "type": var_type,
            }
        if isinstance(raw, dict):
            return {
                "description": str(raw.get("description") or ""),
                "options": [str(x) for x in (raw.get("options") or []) if str(x).strip()],
                "allow_custom": bool(raw.get("allow_custom", True)),
                "type": str(raw.get("type") or var_type),
            }
        return {
            "description": "",
            "options": [],
            "allow_custom": True,
            "type": var_type,
        }

    def normalize_variable_descriptions_for_template(
        raw_descriptions: Any,
        variables: list[dict[str, str]],
    ) -> dict[str, Any]:
        existing = ensure_dict(raw_descriptions)
        normalized: dict[str, Any] = {}
        for var in variables:
            token = variable_token(var)
            cfg = get_variable_config(existing, token, var["type"])
            cfg["type"] = var["type"]
            if var["type"] != "text":
                cfg["options"] = []
                cfg["allow_custom"] = True
            normalized[token] = cfg
        return normalized

    async def show_prompt_edit_actions(message: Message, prompt: asyncpg.Record) -> None:
        reference_text = "set" if prompt["reference_photo_file_id"] else "not set"
        await message.answer(
            "Prompt edit menu:\n"
            f"Title: {prompt['title']}\n"
            f"Reference image: {reference_text}\n"
            "Choose what to change:",
            reply_markup=build_prompt_edit_menu(int(prompt["id"])),
        )

    async def persist_prompt_edit_state(state: FSMContext) -> Optional[asyncpg.Record]:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        if prompt_id is None:
            return None
        await repo.update_prompt(
            prompt_id=int(prompt_id),
            title=data["prompt_title"],
            template=data["prompt_template"],
            variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
            reference_photo_file_id=data.get("reference_photo_file_id"),
        )
        return await repo.get_prompt_by_id(int(prompt_id))

    async def show_variable_pick_menu(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if prompt_id is None:
            await message.answer("Prompt edit session expired. Open edit menu again.")
            return
        if not variables:
            await message.answer("Template has no variables to edit.")
            prompt = await repo.get_prompt_by_id(int(prompt_id))
            if prompt:
                await show_prompt_edit_actions(message, prompt)
            return
        await message.answer(
            "Choose variable to edit:",
            reply_markup=build_prompt_edit_variables_menu(int(prompt_id), variables),
        )

    async def show_variable_actions_menu(message: Message, state: FSMContext, var_idx: int) -> None:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if prompt_id is None or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable not found. Open variable list again.")
            return
        var = variables[var_idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = get_variable_config(descriptions, token, var["type"])
        options = [str(x) for x in (cfg.get("options") or []) if str(x).strip()]
        allow_custom = bool(cfg.get("allow_custom", True))
        await state.update_data(edit_var_idx=var_idx)
        details = (
            f"Variable: {token}\n"
            f"Type: {var['type']}\n"
            f"Description: {str(cfg.get('description') or '') or '(empty)'}\n"
        )
        if var["type"] == "text":
            details += f"Options: {', '.join(options) if options else '(none)'}\nMy own: {'ON' if allow_custom else 'OFF'}"
        await message.answer(
            details,
            reply_markup=build_prompt_edit_variable_actions_menu(int(prompt_id), var_idx, var),
        )

    def build_text_options_keyboard(options: list[str], allow_custom: bool) -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton(text=(opt[:20] if len(opt) > 20 else opt), callback_data=f"gen:opt:{idx}")]
            for idx, opt in enumerate(options)
        ]
        if allow_custom:
            keyboard.append([InlineKeyboardButton(text="My own", callback_data="gen:myown")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    async def ask_admin_text_options(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            await ask_admin_next_var_description(message, state)
            return

        var = variables[idx]
        if var["type"] != "text":
            await state.update_data(var_desc_idx=idx + 1)
            await ask_admin_next_var_description(message, state)
            return

        token = variable_token(var)
        await state.set_state(AdminStates.waiting_text_options)
        await message.answer(
            f"Set answer options for {token}.\n"
            "Send options separated by ';' (example: Mars; Venus; Jupiter), or /skip for no options."
        )

    async def ask_admin_allow_custom(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        var = variables[idx]
        token = variable_token(var)
        await state.set_state(AdminStates.waiting_text_allow_custom)
        await message.answer(
            f"Allow 'My own' custom text for {token}?",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Yes", callback_data="admin:allow_custom:yes"),
                        InlineKeyboardButton(text="No", callback_data="admin:allow_custom:no"),
                    ]
                ]
            ),
        )

    async def ask_admin_next_var_description(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            admin_mode = data.get("admin_mode", "create")
            if admin_mode == "edit_variables" and data.get("editing_prompt_id") is not None:
                await repo.update_prompt(
                    prompt_id=int(data["editing_prompt_id"]),
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                    reference_photo_file_id=data.get("reference_photo_file_id"),
                )
                prompt = await repo.get_prompt_by_id(int(data["editing_prompt_id"]))
                await state.clear()
                await message.answer("Variable descriptions updated.")
                if prompt:
                    await show_prompt_edit_actions(message, prompt)
            else:
                await state.set_state(AdminStates.waiting_prompt_reference)
                await message.answer(
                    "Send optional reference image now, or type /skip to continue without it."
                )
            return

        var = variables[idx]
        token = variable_token(var)
        await message.answer(
            f"Write a user-facing description for {token}.\n"
            "User will see only this text.\n"
            "Type /skip to leave description empty."
        )

    async def ask_next_variable(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("variables", [])
        current_idx: int = data.get("current_idx", 0)

        if current_idx >= len(variables):
            await run_generation(message, state)
            return

        variable = variables[current_idx]
        var_name = variable["name"]
        var_type = variable["type"]
        token = variable_token(variable)
        variable_descriptions = ensure_dict(data.get("variable_descriptions", {}))
        config = get_variable_config(variable_descriptions, token, var_type)
        description = str(config.get("description") or "").strip()
        options: list[str] = [str(x) for x in (config.get("options") or []) if str(x).strip()]
        allow_custom = bool(config.get("allow_custom", True))

        # Auto-handle trivial/disabled text variables
        if var_type == "text":
            # If exactly one option and custom input is disabled, just use this value and skip asking user
            if len(options) == 1 and not allow_custom:
                answers: dict[str, str] = data.get("answers", {})
                answers[var_name] = options[0]
                await state.update_data(
                    answers=answers,
                    current_idx=current_idx + 1,
                    awaiting_custom_for=None,
                )
                await ask_next_variable(message, state)
                return

            # If no options and custom input is disabled, drop this variable from template and skip it
            if not options and not allow_custom:
                template = str(data.get("template") or "")
                template = template.replace(token, "")
                await state.update_data(
                    template=template,
                    current_idx=current_idx + 1,
                    awaiting_custom_for=None,
                )
                await ask_next_variable(message, state)
                return

        if description:
            await message.answer(description)
        elif var_type == "image":
            await message.answer("Please send a photo.")
        else:
            await message.answer("Please send text.")

        if var_type == "text" and options:
            await message.answer(
                "Choose one option:",
                reply_markup=build_text_options_keyboard(options, allow_custom),
            )

    async def run_generation(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        user_tg_id = int(
            data.get("request_user_id")
            or ((message.from_user.id if message.from_user else 0))
        )
        if not user_tg_id:
            await message.answer("Cannot detect user account for billing.")
            return

        new_balance = await repo.consume_generation_token(user_tg_id)
        if new_balance is None:
            balance = await repo.get_user_balance(user_tg_id)
            await message.answer(
                "Not enough balance for generation.\n"
                f"Your balance: {balance}\n"
                "Apply a promo code via your start link."
            )
            await state.clear()
            await show_prompt_buttons(message)
            return

        template = data["template"]
        answers: dict[str, str] = data.get("answers", {})
        image_urls: list[str] = data.get("image_urls", [])
        prompt_title = data["prompt_title"]

        final_prompt = render_prompt(template, answers)
        progress_message = await message.answer(
            f"Generating image for prompt: {prompt_title}\nStatus: queued"
        )
        last_progress_text = progress_message.text or ""

        try:
            task_id = await evo.create_task(final_prompt, image_urls=image_urls)

            async def update_progress(status: Any, progress: Any) -> None:
                nonlocal last_progress_text
                status_text = str(status or "processing")
                progress_text = "?"
                if progress is not None:
                    progress_text = str(progress)
                text = (
                    f"Generating image for prompt: {prompt_title}\n"
                    f"Status: {status_text}\n"
                    f"Progress: {progress_text}%"
                )
                if text == last_progress_text:
                    return
                try:
                    await progress_message.edit_text(text)
                    last_progress_text = text
                except TelegramBadRequest:
                    # Ignore "message is not modified" and similar harmless edit errors.
                    pass

            details = await evo.wait_for_completion(task_id, on_progress=update_progress)

            status = details.get("status")
            if status != "completed":
                await progress_message.delete()

                error = (details.get("error") or {}) if isinstance(details, dict) else {}
                error_code = error.get("code")
                error_message = (error.get("message") or "").strip()

                user_friendly = "Image generation failed."
                if error_message:
                    user_friendly = f"{user_friendly}\nReason: {error_message}"

                # Special handling for content policy violations to give clearer guidance.
                if error_code == "content_policy_violation":
                    user_friendly += (
                        "\n\nIt looks like the request may include brand logos, "
                        "trademarks, or copyrighted characters. "
                        "Please remove logos, brand names, and protected characters, "
                        "then try again."
                    )

                await message.answer(user_friendly)
                return

            results = details.get("results") or []
            if not results:
                await progress_message.delete()
                await message.answer("Generation completed, but no images were returned.")
                return

            await progress_message.delete()
            for url in results:
                await message.answer_photo(photo=url)

            await message.answer(f"Your balance: {new_balance}")
            await maybe_notify_admins_balance_checkpoint(data)
        except Exception as e:
            try:
                await progress_message.delete()
            except Exception:
                pass
            await message.answer(f"Error: {e}")
        finally:
            await state.clear()
            await show_prompt_buttons(message)

    async def maybe_notify_admins_balance_checkpoint(state_data: dict[str, Any]) -> None:
        """
        Notify admins only when user remaining credits cross the next multiple of 20.
        """
        credits = await evo.get_credits()
        user_data = (credits.get("data") or {}).get("user") or {}
        remaining_raw = user_data.get("remaining_credits")
        if remaining_raw is None:
            return

        try:
            remaining = float(remaining_raw)
        except (TypeError, ValueError):
            return

        current_bucket = int(remaining // 20)
        prev_bucket_raw = await repo.get_state_value(BALANCE_BUCKET_KEY)

        # First observation: remember bucket silently (no notification)
        if prev_bucket_raw is None:
            await repo.set_state_value(BALANCE_BUCKET_KEY, str(current_bucket))
            return

        try:
            prev_bucket = int(prev_bucket_raw)
        except ValueError:
            prev_bucket = current_bucket

        # Notify only when remaining balance crossed to a lower 20-step bucket.
        if current_bucket < prev_bucket:
            crossed_value = (current_bucket + 1) * 20
            user_id = state_data.get("request_user_id")
            username = state_data.get("request_username") or "no_username"
            full_name = state_data.get("request_full_name") or "Unknown user"
            user_label = f"{full_name} (@{username}, id={user_id})"

            admin_text = (
                "Balance checkpoint reached.\n"
                # f"Triggered by: {user_label}\n"
                f"User remaining credits: {remaining}\n"
                # f"Crossed threshold: <= {crossed_value}"
            )
            for admin_id in settings.admin_ids:
                try:
                    await bot.send_message(admin_id, admin_text)
                except Exception:
                    logging.exception("Failed to send admin balance notification to %s", admin_id)

        await repo.set_state_value(BALANCE_BUCKET_KEY, str(current_bucket))

    async def telegram_file_url(file_id: str) -> str:
        file = await bot.get_file(file_id)
        return f"https://api.telegram.org/file/bot{settings.bot_token}/{file.file_path}"

    @router.message(CommandStart())
    async def start_handler(message: Message, state: FSMContext) -> None:
        user = await ensure_user(message)
        await state.clear()
        payload = extract_start_payload(message.text or "")

        promo_block = ""
        if payload:
            success, promo_message, granted = await repo.redeem_promo_code(payload, user["tg_id"])
            if success:
                new_balance = await repo.get_user_balance(user["tg_id"])
                promo_block = (
                    f"{promo_message}\n"
                    f"Granted: {granted}\n"
                    f"Your balance: {new_balance}\n\n"
                )
            else:
                await message.answer(promo_message)

        if user["is_authorized"]:
            balance = await repo.get_user_balance(user["tg_id"])
            await message.answer(
                f"{promo_block}"
                "Welcome!\n"
                "Choose one of the prompt buttons below.\n"
                "For each prompt, I will ask for required values and then generate an image.\n"
                f"Your balance: {balance}"
            )
            await show_prompt_buttons(message)
            return

        await message.answer(
            f"{promo_block}"
            "Please enter password to continue.\n\n"
        )
        await state.set_state(AuthStates.waiting_password)

    @router.message(AuthStates.waiting_password)
    async def password_handler(message: Message, state: FSMContext) -> None:
        user = await ensure_user(message)
        text = (message.text or "").strip()
        if text == settings.user_password:
            await repo.set_user_authorized(user["tg_id"], True)
            balance = await repo.get_user_balance(user["tg_id"])
            await message.answer(f"Access granted.\nYour balance: {balance}")
            await state.clear()
            await show_prompt_buttons(message)
            return
        await message.answer("Wrong password. Try again:")

    @router.message(Command("admin"))
    async def admin_handler(message: Message) -> None:
        user = await ensure_user(message)
        if not user["is_admin"]:
            await message.answer("Admin only.")
            return
        await message.answer("Admin panel:", reply_markup=build_admin_menu())

    @router.message(Command("addme"))
    async def addme_handler(message: Message) -> None:
        user = await ensure_user(message)
        if not user or not user["is_admin"]:
            await message.answer("Admin only.")
            return
        payload = (message.text or "").strip().split(maxsplit=2)
        if len(payload) < 3:
            await message.answer(
                "Usage: /addme <user_id_or_@username> <amount>\n"
                "Example: /addme 184374602 10 or /addme @username -5"
            )
            return
        target_str = payload[1].strip()
        amount_str = payload[2].strip()
        try:
            amount = int(amount_str)
        except ValueError:
            await message.answer("Amount must be an integer (can be negative).")
            return
        if target_str.startswith("@"):
            target_user = await repo.get_user_by_username(target_str)
        else:
            try:
                tg_id = int(target_str)
                target_user = await repo.get_user(tg_id)
            except ValueError:
                target_user = await repo.get_user_by_username(target_str)
        if not target_user:
            await message.answer("User not found.")
            return
        tg_id = int(target_user["tg_id"])
        new_balance = await repo.add_user_balance(tg_id, amount)
        await message.answer(
            f"Balance updated: {target_user.get('username') or tg_id} now has {new_balance} tokens (delta: {amount:+d})."
        )

    @router.message(StateFilter(None), F.text.casefold() == "admin")
    async def admin_text_handler(message: Message) -> None:
        user = await ensure_user(message)
        if not user["is_admin"]:
            await message.answer("Admin only.")
            return
        await message.answer("Admin panel:", reply_markup=build_admin_menu())

    @router.callback_query(F.data == "admin:prompt_work")
    async def admin_prompt_work_menu(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await callback.message.answer(
            "Prompts: generate from idea (DeepSeek), list, or add manually.",
            reply_markup=build_prompt_work_menu(),
        )
        await callback.answer()

    @router.callback_query(F.data == "admin:pw:list")
    async def admin_prompt_list(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        prompts = await repo.list_prompts()
        if not prompts:
            await callback.message.answer(
                "No prompts yet. Use «Generate new prompt» or «Add prompt (manual)».",
                reply_markup=build_prompt_work_menu(),
            )
        else:
            await callback.message.answer("List of prompts:", reply_markup=build_prompt_list_menu(prompts))
        await callback.answer()

    @router.callback_query(F.data == "admin:gen:start")
    async def admin_gen_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        if not deepseek:
            await callback.answer("DeepSeek client not available.", show_alert=True)
            return
        await state.clear()
        await state.set_state(AdminStates.waiting_gen_title)
        await callback.message.answer("Enter prompt title (as shown in the bot):")
        await callback.answer()

    @router.message(AdminStates.waiting_gen_title)
    async def admin_gen_title(message: Message, state: FSMContext) -> None:
        title = (message.text or "").strip()
        if not title:
            await message.answer("Title cannot be empty. Enter title:")
            return
        await state.update_data(gen_title=title)
        await state.set_state(AdminStates.waiting_gen_idea)
        await message.answer("Enter the main idea for the image:")

    @router.message(AdminStates.waiting_gen_idea)
    async def admin_gen_idea(message: Message, state: FSMContext) -> None:
        idea = (message.text or "").strip()
        if not idea:
            await message.answer("Idea cannot be empty. Enter idea:")
            return
        data = await state.get_data()
        title = data.get("gen_title", "")
        if not title:
            await message.answer("Session expired. Start from «Generate new prompt» again.")
            await state.clear()
            return
        user = await repo.get_user(message.from_user.id)
        if not user or not deepseek:
            await message.answer("Error: unavailable.")
            await state.clear()
            return
        try:
            await message.answer("Calling bot…")
            api_feach = await deepseek.refine_idea(idea)
            normalized = normalize_feach_for_storage(api_feach)
        except Exception as e:
            await message.answer(f"DeepSeek error: {e}")
            await state.clear()
            return
        try:
            await repo.insert_prompt(
                title=title,
                template=idea,
                variable_descriptions={},
                reference_photo_file_id=None,
                created_by=user["tg_id"],
                is_active=False,
                feach_data=normalized,
            )
        except asyncpg.UniqueViolationError:
            await message.answer("A prompt with this title already exists. Choose another title.")
            return
        await state.clear()
        await message.answer(
            "Draft prompt created. Open «List of prompts» to configure features and generate the final prompt.",
            reply_markup=build_prompt_work_menu(),
        )

    @router.callback_query(F.data.startswith("admin:feach:"))
    async def admin_feach_feature(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        feat = features[feat_key]
        varname = feat.get("varname", feat_key)
        about = feat.get("about", "")
        try:
            await callback.message.edit_text(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feat),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feat),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:opt:"))
    async def admin_opt_toggle(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 6:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        opt_key = parts[4]
        enabled = parts[5] == "1"
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        feat = features[feat_key]
        opts = feat.get("options") or {}
        if opt_key.startswith("custom_"):
            custom = list(feat.get("custom") or [])
            idx = int(opt_key.replace("custom_", "")) if opt_key.replace("custom_", "").isdigit() else -1
            if 0 <= idx < len(custom):
                if isinstance(custom[idx], dict):
                    custom[idx] = {**custom[idx], "enabled": enabled}
                else:
                    custom[idx] = {"text": str(custom[idx]), "enabled": enabled}
                feat["custom"] = custom
        else:
            if opt_key in opts:
                if isinstance(opts[opt_key], dict):
                    opts[opt_key]["enabled"] = enabled
                else:
                    opts[opt_key] = {"text": get_feach_option_text(opts[opt_key]), "enabled": enabled}
        await repo.update_prompt_feach_data(prompt_id, feach_data)
        prompt = await repo.get_prompt_by_id(prompt_id)
        if prompt:
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        feat_key,
                        feach_data.get("features", {}).get(feat_key, {}),
                    )
                )
            except TelegramBadRequest:
                # Ignore harmless edit errors (e.g. message was changed elsewhere).
                pass
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:optdel:"))
    async def admin_opt_delete(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 5:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        opt_key = parts[4]
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        feat = features[feat_key]
        if opt_key.startswith("custom_"):
            custom = list(feat.get("custom") or [])
            idx = int(opt_key.replace("custom_", "")) if opt_key.replace("custom_", "").isdigit() else -1
            if 0 <= idx < len(custom):
                custom.pop(idx)
                feat["custom"] = custom
            else:
                await callback.answer("Option not found", show_alert=True)
                return
        else:
            opts = feat.get("options") or {}
            if opt_key not in opts:
                await callback.answer("Option not found", show_alert=True)
                return
            del opts[opt_key]
            feat["options"] = opts
        await repo.update_prompt_feach_data(prompt_id, feach_data)
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer()
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        feat = feach_data.get("features", {}).get(feat_key, {})
        varname = feat.get("varname", feat_key)
        about = feat.get("about", "")
        try:
            await callback.message.edit_text(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feat),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feat),
            )
        await callback.answer("Deleted")

    @router.callback_query(F.data.startswith("admin:myown:"))
    async def admin_myown_toggle(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        feat = features[feat_key]
        feat["my_own"] = not feat.get("my_own", True)
        await repo.update_prompt_feach_data(prompt_id, feach_data)
        prompt = await repo.get_prompt_by_id(prompt_id)
        if prompt:
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        feat_key,
                        feach_data.get("features", {}).get(feat_key, {}),
                    )
                )
            except TelegramBadRequest:
                pass
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:featadd:"))
    async def admin_feat_add_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        await state.update_data(feach_add_prompt_id=prompt_id, feach_add_feat_key=feat_key)
        await state.set_state(AdminStates.waiting_feach_add_option)
        await callback.message.answer("Send the new option text:")
        await callback.answer()

    @router.message(AdminStates.waiting_feach_add_option)
    async def admin_feach_add_option_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Send non-empty option text:")
            return
        data = await state.get_data()
        prompt_id = data.get("feach_add_prompt_id")
        feat_key = data.get("feach_add_feat_key")
        if prompt_id is None or not feat_key:
            await message.answer("Session expired.")
            await state.clear()
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await message.answer("Prompt not found.")
            await state.clear()
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await message.answer("Feature not found.")
            await state.clear()
            return
        feat = features[feat_key]
        custom = list(feat.get("custom") or [])
        custom.append({"text": text, "enabled": True})
        feat["custom"] = custom
        await repo.update_prompt_feach_data(prompt_id, feach_data)
        await state.clear()
        prompt = await repo.get_prompt_by_id(prompt_id)
        if prompt:
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            await message.answer(
                "Option added.",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feach_data.get("features", {}).get(feat_key, {})),
            )

    @router.callback_query(F.data.startswith("admin:featdone:"))
    async def admin_feat_done(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        is_active = bool(prompt.get("is_active", True))
        try:
            await callback.message.edit_text(
                "Done. Back to prompt.",
                reply_markup=build_prompt_feach_menu(prompt_id, feach_data, is_active),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "Done. Back to prompt.",
                reply_markup=build_prompt_feach_menu(prompt_id, feach_data, is_active),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:optview:"))
    async def admin_opt_view(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 5:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        opt_key = parts[4]
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        feat = features[feat_key]
        opts = feat.get("options") or {}
        custom = feat.get("custom") or []
        if opt_key.startswith("custom_"):
            idx = int(opt_key.replace("custom_", "")) if opt_key.replace("custom_", "").isdigit() else -1
            text = custom[idx].get("text", str(custom[idx])) if 0 <= idx < len(custom) and isinstance(custom[idx], dict) else (str(custom[idx]) if 0 <= idx < len(custom) else "")
        else:
            opt_val = opts.get(opt_key)
            text = get_feach_option_text(opt_val)
        await callback.answer(text[:200] if text else "—", show_alert=True)

    @router.callback_query(F.data.startswith("admin:final:"))
    async def admin_final_prompt(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        if not deepseek:
            await callback.answer("DeepSeek not available", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        idea = feach_data.get("idea", "")
        # Персона в сцене — всегда приложенное фото [USER_PHOTO], не текстовая переменная
        variables_spec: list[dict[str, Any]] = [
            {
                "name": "USER_PHOTO",
                "type": "image",
                "constant": None,
                "options": None,
                "allow_custom": False,
                "about": "Reference photo of the person to integrate into the scene",
            }
        ]
        for feat_key, feat in features.items():
            varname = (feat.get("varname") or feat_key).upper().replace(" ", "_")
            opts = feat.get("options") or {}
            custom = feat.get("custom") or []
            enabled_opts = []
            for opt_k, opt_v in opts.items():
                if get_feach_option_enabled(opt_v):
                    enabled_opts.append(get_feach_option_text(opt_v))
            for c in custom:
                if isinstance(c, dict) and c.get("enabled", True):
                    enabled_opts.append(c.get("text", ""))
                elif isinstance(c, str):
                    enabled_opts.append(c)
            my_own = feat.get("my_own", True)
            # If no enabled options and no custom values allowed, completely drop this variable
            if not enabled_opts and not my_own and not custom:
                continue
            # If exactly one enabled option and no custom, treat it as constant (no placeholder)
            if len(enabled_opts) == 1 and not my_own and not custom:
                variables_spec.append(
                    {
                        "name": varname,
                        "type": "text",
                        "constant": enabled_opts[0],
                        "options": None,
                        "allow_custom": False,
                        "about": feat.get("about", ""),
                    }
                )
            else:
                variables_spec.append(
                    {
                        "name": varname,
                        "type": "text",
                        "constant": None,
                        "options": enabled_opts,
                        "allow_custom": my_own,
                        "about": feat.get("about", ""),
                    }
                )
        try:
            await callback.message.answer("Generating final prompt…")
            result = await deepseek.generate_final_prompt(idea, variables_spec)
        except Exception as e:
            await callback.message.answer(f"DeepSeek error: {e}")
            await callback.answer()
            return
        template = result.get("template", "")
        var_descriptions = ensure_dict(result.get("variable_descriptions") or {})
        # Всегда помечаем [USER_PHOTO] как image, чтобы не смешивать с текстовыми переменными
        if "[USER_PHOTO]" in template:
            var_descriptions["[USER_PHOTO]"] = {
                "description": "Reference photo of the person",
                "options": [],
                "allow_custom": True,
                "type": "image",
            }
        await repo.update_prompt(prompt_id, prompt["title"], template, var_descriptions, prompt.get("reference_photo_file_id"))
        await callback.message.answer("Final prompt saved. You can activate it or edit further.")
        prompt = await repo.get_prompt_by_id(prompt_id)
        if prompt:
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            await callback.message.answer(
                f"Template: {template[:300]}…" if len(template) > 300 else f"Template: {template}",
                reply_markup=build_prompt_feach_menu(prompt_id, feach_data, bool(prompt.get("is_active", True))),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:active:"))
    async def admin_toggle_active(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        new_active = not bool(prompt.get("is_active", True))
        await repo.set_prompt_active(prompt_id, new_active)
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer()
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {}) if prompt.get("feach_data") else None
        is_active = bool(prompt.get("is_active", True))
        if feach_data and feach_data.get("features"):
            await callback.message.answer(
                f"Prompt: {prompt['title']}\nIdea: {feach_data.get('idea', '')}",
                reply_markup=build_prompt_feach_menu(prompt_id, feach_data, is_active),
            )
        else:
            await callback.message.answer(f"Prompt: {prompt['title']}", reply_markup=build_prompt_item_menu(prompt_id, is_active))
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:export:"))
    async def admin_export_prompt(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        logging.info("admin_export_prompt: callback.data=%r", callback.data)
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            try:
                prompt_id = int((callback.data or "").split(":")[-1])
            except ValueError:
                logging.warning("admin_export_prompt: invalid prompt id in data=%r", callback.data)
                await callback.answer("Invalid", show_alert=True)
                return
            logging.info("admin_export_prompt: prompt_id=%s", prompt_id)
            prompt = await repo.get_prompt_by_id(prompt_id)
            if not prompt:
                logging.warning("admin_export_prompt: prompt %s not found", prompt_id)
                await callback.answer("Prompt not found", show_alert=True)
                return
            payload = build_prompt_export_payload(prompt)
            logging.info("admin_export_prompt: payload built for prompt_id=%s (title=%r)", prompt_id, payload["title"])
            from io import BytesIO
            buf = BytesIO(json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
            buf.seek(0)
            from aiogram.types import BufferedInputFile
            await callback.message.answer_document(
                BufferedInputFile(buf.getvalue(), filename=f"prompt_{prompt_id}.json"),
            )
            logging.info("admin_export_prompt: document sent for prompt_id=%s", prompt_id)
            await callback.answer()
        except Exception as e:
            logging.exception("admin_export_prompt: unexpected error")
            err_msg = str(e).strip() or type(e).__name__
            if len(err_msg) > 80:
                err_msg = err_msg[:77] + "..."
            try:
                await callback.answer(f"Export failed: {err_msg}", show_alert=True)
            except Exception:
                pass

    @router.callback_query(F.data == "admin:import")
    async def admin_import_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.set_state(AdminStates.waiting_import_json)
        await callback.message.answer("Send a JSON file (exported prompt) to import. It will create or update a prompt by title.")
        await callback.answer()

    @router.message(AdminStates.waiting_import_json, F.document)
    async def admin_import_document(message: Message, state: FSMContext) -> None:
        if not message.document:
            return
        doc = message.document
        if not doc.file_name or not doc.file_name.endswith(".json"):
            await message.answer("Send a .json file.")
            return
        try:
            file = await bot.get_file(doc.file_id)
            buf = await bot.download_file(file.file_path)
            raw = buf.read().decode("utf-8") if hasattr(buf, "read") else buf.getvalue().decode("utf-8")
            payload = json.loads(raw)
        except Exception as e:
            await message.answer(f"Failed to read file: {e}")
            return
        title = (payload.get("title") or "").strip()
        if not title:
            await message.answer("JSON must contain 'title'.")
            await state.clear()
            return
        user = await repo.get_user(message.from_user.id)
        if not user:
            await message.answer("User not found.")
            await state.clear()
            return
        template = payload.get("template") or ""
        # New format: features (feach-like) → build variable_descriptions
        features = payload.get("features")
        if isinstance(features, dict):
            var_descriptions = variable_descriptions_from_features(template, features)
            ref_id = None
            feach_data = None
            example_ids = []
        else:
            # Legacy: variable_descriptions in payload (we ignore ref/feach/examples from file)
            var_descriptions = ensure_dict(payload.get("variable_descriptions") or {})
            ref_id = None
            feach_data = None
            example_ids = []
        existing = await repo.get_prompt_by_title(title)
        try:
            if existing:
                # Keep existing reference when updating (import does not touch ref/feach/examples)
                keep_ref = existing.get("reference_photo_file_id") if ref_id is None else ref_id
                await repo.update_prompt(existing["id"], title, template, var_descriptions, keep_ref)
                await message.answer(f"Prompt «{title}» updated.")
            else:
                await repo.insert_prompt(
                    title, template, var_descriptions, ref_id, user["tg_id"],
                    is_active=True, feach_data=feach_data,
                )
                new_prompt = await repo.get_prompt_by_title(title)
                if new_prompt:
                    await repo.set_prompt_examples(new_prompt["id"], example_ids)
                await message.answer(f"Prompt «{title}» created.")
        except Exception as e:
            await message.answer(f"Error: {e}")
        await state.clear()

    @router.callback_query(F.data == "admin:pw:back")
    async def admin_prompt_work_back(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        await callback.message.answer("Admin panel:", reply_markup=build_admin_menu())
        await callback.answer()

    @router.callback_query(F.data == "admin:promo_menu")
    async def admin_promo_menu(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        promos = await repo.list_promo_codes()
        if promos:
            await callback.message.answer("Promo code list:", reply_markup=build_promo_list_menu(promos))
        else:
            await callback.message.answer(
                "Promo code list is empty.",
                reply_markup=build_promo_menu(),
            )
        await callback.answer()

    @router.callback_query(F.data == "admin:promo:back")
    async def admin_promo_back(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        await callback.message.answer("Admin panel:", reply_markup=build_admin_menu())
        await callback.answer()

    @router.callback_query(F.data == "admin:promo:create:single")
    async def admin_promo_create_single(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        await state.update_data(promo_mode="single")
        await state.update_data(promo_action="create", editing_promo_id=None)
        await state.set_state(AdminStates.waiting_promo_code)
        await callback.message.answer("Send promo code text (for start link payload).")
        await callback.answer()

    @router.callback_query(F.data == "admin:promo:create:multi")
    async def admin_promo_create_multi(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        await state.update_data(promo_mode="multi")
        await state.update_data(promo_action="create", editing_promo_id=None)
        await state.set_state(AdminStates.waiting_promo_code)
        await callback.message.answer("Send promo code text (for start link payload).")
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:item:"))
    async def admin_promo_item_actions(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return
        max_uses = promo["max_uses"]
        max_uses_text = "unlimited" if max_uses is None else str(max_uses)
        await callback.message.answer(
            "Promo code details:\n"
            f"Code: {promo['code']}\n"
            f"Credits: {promo['credits_amount']}\n"
            f"Uses: {promo['uses_count']}/{max_uses_text}\n"
            f"Active: {promo['is_active']}",
            reply_markup=build_promo_item_menu(promo_id),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:edit:"))
    async def admin_promo_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return

        mode = "single" if promo["max_uses"] == 1 else "multi"
        await state.clear()
        await state.update_data(
            promo_mode=mode,
            promo_action="edit",
            editing_promo_id=promo_id,
        )
        await state.set_state(AdminStates.waiting_promo_code)
        await callback.message.answer(
            f"Editing promo '{promo['code']}'.\n"
            "Send new promo code text:"
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:delete:"))
    async def admin_promo_delete(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return
        deleted = await repo.delete_promo_code(promo_id)
        if deleted:
            await callback.message.answer(f"Promo deleted: {promo['code']}")
        else:
            await callback.message.answer("Promo was not deleted.")
        await callback.answer()

    @router.message(AdminStates.waiting_promo_code)
    async def admin_promo_code_value(message: Message, state: FSMContext) -> None:
        code = (message.text or "").strip()
        if not code or len(code) < 3:
            await message.answer("Promo code is too short. Send at least 3 characters.")
            return
        await state.update_data(promo_code=code)
        await state.set_state(AdminStates.waiting_promo_credits)
        await message.answer("How many generation tokens should this promo grant?")

    @router.message(AdminStates.waiting_promo_credits)
    async def admin_promo_credits_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text.isdigit() or int(text) <= 0:
            await message.answer("Send a positive integer.")
            return
        credits = int(text)
        data = await state.get_data()
        mode = data.get("promo_mode")
        await state.update_data(promo_credits=credits)

        if mode == "single":
            await state.update_data(promo_max_uses=1)
            await finalize_promo_creation(message, state)
            return

        await state.set_state(AdminStates.waiting_promo_max_uses)
        await message.answer("How many users can redeem it? Send positive integer, or 0 for unlimited.")

    @router.message(AdminStates.waiting_promo_max_uses)
    async def admin_promo_max_uses_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text.isdigit() or int(text) < 0:
            await message.answer("Send 0 or a positive integer.")
            return
        max_uses = int(text)
        await state.update_data(promo_max_uses=(None if max_uses == 0 else max_uses))
        await finalize_promo_creation(message, state)

    async def finalize_promo_creation(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        user = await repo.get_user(message.from_user.id)
        try:
            promo_action = data.get("promo_action", "create")
            if promo_action == "edit" and data.get("editing_promo_id") is not None:
                await repo.update_promo_code(
                    promo_id=int(data["editing_promo_id"]),
                    code=str(data["promo_code"]),
                    credits_amount=int(data["promo_credits"]),
                    max_uses=data.get("promo_max_uses"),
                )
            else:
                await repo.create_promo_code(
                    code=str(data["promo_code"]),
                    credits_amount=int(data["promo_credits"]),
                    max_uses=data.get("promo_max_uses"),
                    created_by=user["tg_id"] if user else message.from_user.id,
                )
            me = await bot.get_me()
            header = "Promo code updated." if promo_action == "edit" else "Promo code created."
            if me.username:
                link = f"https://t.me/{me.username}?start={data['promo_code']}"
                await message.answer(
                    f"{header}\n"
                    f"Start link: {link}"
                )
            else:
                await message.answer(
                    f"{header}\n"
                    f"Use payload in /start: {data['promo_code']}"
                )
        except asyncpg.UniqueViolationError:
            await message.answer("Promo code already exists. Choose another code.")
        finally:
            await state.clear()

    @router.callback_query(F.data == "admin:pw:add")
    async def admin_create_prompt_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        await state.update_data(admin_mode="create", editing_prompt_id=None)
        await callback.message.answer("Send prompt title:")
        await state.set_state(AdminStates.waiting_prompt_title)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:pw:item:"))
    async def admin_prompt_item_actions(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {}) if prompt.get("feach_data") else None
        is_active = bool(prompt.get("is_active", True))
        if feach_data and feach_data.get("features"):
            idea = feach_data.get("idea", "")
            await callback.message.answer(
                f"Prompt: {prompt['title']}\n\nIdea: {idea}",
                reply_markup=build_prompt_feach_menu(prompt_id, feach_data, is_active),
            )
        else:
            await callback.message.answer(
                f"Prompt: {prompt['title']}",
                reply_markup=build_prompt_item_menu(prompt_id, is_active),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("prompt:select:"))
    async def prompt_pick_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start and enter password first.", show_alert=True)
            return

        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt", show_alert=True)
            return

        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return

        template = prompt["template"]
        variables = extract_variables(template)
        await state.clear()
        await state.set_state(GenerateStates.waiting_variable)
        await state.update_data(
            prompt_title=prompt["title"],
            template=template,
            variables=variables,
            current_idx=0,
            answers={},
            image_urls=[],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=prompt["reference_photo_file_id"],
            awaiting_custom_for=None,
            request_user_id=callback.from_user.id,
            request_username=callback.from_user.username or "",
            request_full_name=callback.from_user.full_name or "",
        )

        if prompt["reference_photo_file_id"]:
            try:
                ref_url = await telegram_file_url(prompt["reference_photo_file_id"])
                data = await state.get_data()
                image_urls = data.get("image_urls", [])
                image_urls.append(ref_url)
                await state.update_data(image_urls=image_urls)
            except Exception as e:
                await callback.message.answer(f"Warning: could not load reference image: {e}")

        await callback.answer()
        if not variables:
            await run_generation(callback.message, state)
            return
        await ask_next_variable(callback.message, state)

    @router.callback_query(F.data.startswith("admin:edit:"))
    async def admin_edit_prompt_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return

        await state.clear()
        await show_prompt_edit_actions(callback.message, prompt)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:title:"))
    async def admin_edit_prompt_title_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        await state.clear()
        await state.update_data(
            editing_prompt_id=prompt_id,
            prompt_title=prompt["title"],
            prompt_template=prompt["template"],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=prompt["reference_photo_file_id"],
        )
        await state.set_state(AdminStates.waiting_prompt_edit_title)
        await callback.message.answer(
            f"Current title: {prompt['title']}\n"
            "Send new title:"
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:template:"))
    async def admin_edit_prompt_template_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        await state.clear()
        await state.update_data(
            editing_prompt_id=prompt_id,
            prompt_title=prompt["title"],
            prompt_template=prompt["template"],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=prompt["reference_photo_file_id"],
        )
        await state.set_state(AdminStates.waiting_prompt_edit_template)
        await callback.message.answer(
            "Send new template.\n"
            "- Use [var] for image variables\n"
            "- Use <var> for text variables"
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:variables:"))
    async def admin_edit_prompt_variables_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        template = prompt["template"]
        variables = extract_variables(template)
        descriptions = normalize_variable_descriptions_for_template(
            prompt.get("variable_descriptions") or {},
            variables,
        )
        await state.clear()
        await state.update_data(
            editing_prompt_id=prompt_id,
            prompt_title=prompt["title"],
            prompt_template=template,
            prompt_variables=variables,
            variable_descriptions=descriptions,
            reference_photo_file_id=prompt["reference_photo_file_id"],
        )
        await state.set_state(None)
        await show_variable_pick_menu(callback.message, state)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:pick:"))
    async def admin_edit_variable_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 6:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        try:
            prompt_id = int(parts[4])
            var_idx = int(parts[5])
        except ValueError:
            await callback.answer("Invalid variable action", show_alert=True)
            return

        data = await state.get_data()
        if data.get("editing_prompt_id") != prompt_id:
            prompt = await repo.get_prompt_by_id(prompt_id)
            if not prompt:
                await callback.answer("Prompt not found", show_alert=True)
                return
            template = prompt["template"]
            variables = extract_variables(template)
            descriptions = normalize_variable_descriptions_for_template(
                prompt.get("variable_descriptions") or {},
                variables,
            )
            await state.clear()
            await state.update_data(
                editing_prompt_id=prompt_id,
                prompt_title=prompt["title"],
                prompt_template=template,
                prompt_variables=variables,
                variable_descriptions=descriptions,
                reference_photo_file_id=prompt["reference_photo_file_id"],
            )
            await state.set_state(None)

        await show_variable_actions_menu(callback.message, state, var_idx)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:field:name:"))
    async def admin_edit_variable_name_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 7:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        try:
            var_idx = int(parts[6])
        except ValueError:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        await state.update_data(edit_var_idx=var_idx)
        await state.set_state(AdminStates.waiting_prompt_edit_variable_name)
        await callback.message.answer(
            "Send new variable name only (without [] or <>).\n"
            "Example: USER_PHOTO"
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:field:desc:"))
    async def admin_edit_variable_description_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 7:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        try:
            var_idx = int(parts[6])
        except ValueError:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        await state.update_data(edit_var_idx=var_idx)
        await state.set_state(AdminStates.waiting_prompt_edit_variable_description)
        await callback.message.answer(
            "Send new user-facing description.\n"
            "Use /skip to clear description."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:field:opts:"))
    async def admin_edit_variable_options_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 7:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        try:
            var_idx = int(parts[6])
        except ValueError:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if var_idx < 0 or var_idx >= len(variables):
            await callback.answer("Variable not found", show_alert=True)
            return
        if variables[var_idx]["type"] != "text":
            await callback.answer("Options are available only for text variables.", show_alert=True)
            return
        await state.update_data(edit_var_idx=var_idx)
        await state.set_state(AdminStates.waiting_prompt_edit_variable_options)
        await callback.message.answer(
            "Send options separated by ';'.\n"
            "Use /skip to clear options."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:allow:"))
    async def admin_edit_variable_allow_custom(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 7:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        try:
            var_idx = int(parts[5])
        except ValueError:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        allow_custom = parts[6] == "yes"
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if var_idx < 0 or var_idx >= len(variables):
            await callback.answer("Variable not found", show_alert=True)
            return
        var = variables[var_idx]
        if var["type"] != "text":
            await callback.answer("My own is available only for text variables.", show_alert=True)
            return
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = get_variable_config(descriptions, token, "text")
        cfg["allow_custom"] = allow_custom
        descriptions[token] = cfg
        await state.update_data(variable_descriptions=descriptions)
        await persist_prompt_edit_state(state)
        await show_variable_actions_menu(callback.message, state, var_idx)
        await callback.answer("Saved")

    @router.message(AdminStates.waiting_prompt_edit_variable_name)
    async def admin_edit_variable_name_value(message: Message, state: FSMContext) -> None:
        new_name = (message.text or "").strip()
        if not new_name:
            await message.answer("Variable name cannot be empty. Send new name:")
            return
        if any(ch in new_name for ch in "[]<>"):
            await message.answer("Send name without brackets [] or <>.")
            return

        data = await state.get_data()
        var_idx = data.get("edit_var_idx")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if not isinstance(var_idx, int) or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable edit session expired. Open variable list again.")
            await state.set_state(None)
            return

        old_var = variables[var_idx]
        if any(
            i != var_idx and v["type"] == old_var["type"] and v["name"] == new_name
            for i, v in enumerate(variables)
        ):
            await message.answer("Variable with this name already exists for this type.")
            return

        old_token = variable_token(old_var)
        new_var = {"name": new_name, "type": old_var["type"]}
        new_token = variable_token(new_var)
        template = str(data.get("prompt_template") or "").replace(old_token, new_token)
        variables_updated = extract_variables(template)

        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        old_cfg = get_variable_config(descriptions, old_token, old_var["type"])
        descriptions.pop(old_token, None)
        descriptions[new_token] = old_cfg
        descriptions = normalize_variable_descriptions_for_template(descriptions, variables_updated)

        await state.update_data(
            prompt_template=template,
            prompt_variables=variables_updated,
            variable_descriptions=descriptions,
        )
        await persist_prompt_edit_state(state)
        await state.set_state(None)
        await message.answer("Variable renamed.")
        new_idx = next(
            (i for i, v in enumerate(variables_updated) if variable_token(v) == new_token),
            0,
        )
        await show_variable_actions_menu(message, state, new_idx)

    @router.message(AdminStates.waiting_prompt_edit_variable_description, Command("skip"))
    async def admin_edit_variable_description_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        var_idx = data.get("edit_var_idx")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if not isinstance(var_idx, int) or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable edit session expired. Open variable list again.")
            await state.set_state(None)
            return
        var = variables[var_idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = get_variable_config(descriptions, token, var["type"])
        cfg["description"] = ""
        descriptions[token] = cfg
        await state.update_data(variable_descriptions=descriptions)
        await persist_prompt_edit_state(state)
        await state.set_state(None)
        await message.answer("Description cleared.")
        await show_variable_actions_menu(message, state, var_idx)

    @router.message(AdminStates.waiting_prompt_edit_variable_description)
    async def admin_edit_variable_description_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Description cannot be empty. Send text or /skip.")
            return
        data = await state.get_data()
        var_idx = data.get("edit_var_idx")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if not isinstance(var_idx, int) or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable edit session expired. Open variable list again.")
            await state.set_state(None)
            return
        var = variables[var_idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = get_variable_config(descriptions, token, var["type"])
        cfg["description"] = text
        descriptions[token] = cfg
        await state.update_data(variable_descriptions=descriptions)
        await persist_prompt_edit_state(state)
        await state.set_state(None)
        await message.answer("Description updated.")
        await show_variable_actions_menu(message, state, var_idx)

    @router.message(AdminStates.waiting_prompt_edit_variable_options, Command("skip"))
    async def admin_edit_variable_options_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        var_idx = data.get("edit_var_idx")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if not isinstance(var_idx, int) or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable edit session expired. Open variable list again.")
            await state.set_state(None)
            return
        var = variables[var_idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = get_variable_config(descriptions, token, "text")
        cfg["options"] = []
        cfg["allow_custom"] = True
        descriptions[token] = cfg
        await state.update_data(variable_descriptions=descriptions)
        await persist_prompt_edit_state(state)
        await state.set_state(None)
        await message.answer("Options cleared. My own enabled.")
        await show_variable_actions_menu(message, state, var_idx)

    @router.message(AdminStates.waiting_prompt_edit_variable_options)
    async def admin_edit_variable_options_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        options = [part.strip() for part in text.split(";") if part.strip()]
        if not options:
            await message.answer("No valid options found. Send options or /skip to clear.")
            return
        data = await state.get_data()
        var_idx = data.get("edit_var_idx")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if not isinstance(var_idx, int) or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable edit session expired. Open variable list again.")
            await state.set_state(None)
            return
        var = variables[var_idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = get_variable_config(descriptions, token, "text")
        cfg["options"] = options
        descriptions[token] = cfg
        await state.update_data(variable_descriptions=descriptions)
        await persist_prompt_edit_state(state)
        await state.set_state(None)
        await message.answer("Options updated.")
        await show_variable_actions_menu(message, state, var_idx)

    @router.callback_query(F.data.startswith("admin:editpart:ref:set:"))
    async def admin_edit_prompt_reference_set_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        await state.clear()
        await state.update_data(
            admin_mode="edit_reference",
            editing_prompt_id=prompt_id,
            prompt_title=prompt["title"],
            prompt_template=prompt["template"],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=prompt["reference_photo_file_id"],
        )
        await state.set_state(AdminStates.waiting_prompt_reference)
        await callback.message.answer(
            "Send new reference image now.\n"
            "Use /skip to cancel reference update."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:ref:clear:"))
    async def admin_edit_prompt_reference_clear(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        await repo.update_prompt(
            prompt_id=prompt_id,
            title=prompt["title"],
            template=prompt["template"],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=None,
        )
        updated_prompt = await repo.get_prompt_by_id(prompt_id)
        await callback.message.answer("Reference image removed.")
        if updated_prompt:
            await show_prompt_edit_actions(callback.message, updated_prompt)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:examples:"))
    async def admin_edit_prompt_examples_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        current = prompt.get("example_file_ids")
        if isinstance(current, str):
            try:
                current = json.loads(current) if current else []
            except json.JSONDecodeError:
                current = []
        elif not isinstance(current, list):
            current = []
        await state.clear()
        await state.update_data(editing_prompt_id=prompt_id, example_file_ids=list(current))
        await state.set_state(AdminStates.waiting_prompt_examples)
        await callback.message.answer(
            "Send 1 to 3 photos as examples. Send /done after the last one or /skip to clear examples."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:delete:"))
    async def admin_delete_prompt_ask_confirm(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except (TypeError, ValueError):
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        title = prompt.get("title") or "Untitled"
        await callback.message.answer(
            f"Delete prompt «{title}»? This cannot be undone.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Yes, delete", callback_data=f"admin:delete_confirm:{prompt_id}"),
                    InlineKeyboardButton(text="Cancel", callback_data=f"admin:pw:item:{prompt_id}"),
                ],
            ]),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:delete_confirm:"))
    async def admin_delete_prompt_confirm(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except (TypeError, ValueError):
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        async with repo.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT title FROM prompts WHERE id = $1", prompt_id)
            if not row:
                await callback.answer("Prompt not found", show_alert=True)
                return
            await conn.execute("DELETE FROM prompts WHERE id = $1", prompt_id)
        await callback.message.answer(f"Prompt deleted: {row['title']}")
        await callback.answer("Deleted")

    @router.message(AdminStates.waiting_prompt_title)
    async def admin_prompt_title(message: Message, state: FSMContext) -> None:
        title = (message.text or "").strip()
        if not title:
            await message.answer("Title cannot be empty. Send prompt title:")
            return
        await state.update_data(prompt_title=title)
        await state.set_state(AdminStates.waiting_prompt_template)
        await message.answer(
            "Send prompt template.\n"
            "- Use [var] for image variables\n"
            "- Use <var> for text variables\n"
            "Example: Photorealistic astronauts on <planet_name> with [user_photo]."
        )

    @router.message(AdminStates.waiting_prompt_template)
    async def admin_prompt_template(message: Message, state: FSMContext) -> None:
        template = (message.text or "").strip()
        if not template:
            await message.answer("Template cannot be empty. Send prompt template:")
            return
        variables = extract_variables(template)
        await state.update_data(prompt_template=template)
        await state.update_data(
            prompt_variables=variables,
            var_desc_idx=0,
            variable_descriptions={},
        )
        if not variables:
            await state.set_state(AdminStates.waiting_prompt_reference)
            await message.answer(
                "No variables detected.\n"
                "Send optional reference image now, or type /skip to continue without it."
            )
            return
        await state.set_state(AdminStates.waiting_variable_description)
        await ask_admin_next_var_description(message, state)

    @router.message(AdminStates.waiting_prompt_edit_title)
    async def admin_prompt_edit_title_value(message: Message, state: FSMContext) -> None:
        title = (message.text or "").strip()
        if not title:
            await message.answer("Title cannot be empty. Send new title:")
            return
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        if prompt_id is None:
            await message.answer("Prompt edit session expired. Open edit menu again.")
            await state.clear()
            return
        try:
            await repo.update_prompt(
                prompt_id=int(prompt_id),
                title=title,
                template=data["prompt_template"],
                variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                reference_photo_file_id=data.get("reference_photo_file_id"),
            )
            updated_prompt = await repo.get_prompt_by_id(int(prompt_id))
            await state.clear()
            await message.answer("Title updated.")
            if updated_prompt:
                await show_prompt_edit_actions(message, updated_prompt)
        except asyncpg.UniqueViolationError:
            await message.answer("Prompt with this title already exists. Send another title.")

    @router.message(AdminStates.waiting_prompt_edit_template)
    async def admin_prompt_edit_template_value(message: Message, state: FSMContext) -> None:
        template = (message.text or "").strip()
        if not template:
            await message.answer("Template cannot be empty. Send new template:")
            return
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        if prompt_id is None:
            await message.answer("Prompt edit session expired. Open edit menu again.")
            await state.clear()
            return

        variables = extract_variables(template)
        descriptions = normalize_variable_descriptions_for_template(
            data.get("variable_descriptions", {}),
            variables,
        )

        await repo.update_prompt(
            prompt_id=int(prompt_id),
            title=data["prompt_title"],
            template=template,
            variable_descriptions=descriptions,
            reference_photo_file_id=data.get("reference_photo_file_id"),
        )
        updated_prompt = await repo.get_prompt_by_id(int(prompt_id))
        await state.clear()
        await message.answer("Template updated. Variable descriptions were kept for matching variables.")
        if updated_prompt:
            await show_prompt_edit_actions(message, updated_prompt)

    @router.message(AdminStates.waiting_variable_description, Command("skip"))
    async def admin_var_desc_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            await ask_admin_next_var_description(message, state)
            return

        var = variables[idx]
        token = variable_token(var)
        variable_descriptions = ensure_dict(data.get("variable_descriptions", {}))
        variable_descriptions[token] = {
            "description": "",
            "options": [],
            "allow_custom": True,
            "type": var["type"],
        }
        await state.update_data(variable_descriptions=variable_descriptions)
        if var["type"] == "text":
            await ask_admin_text_options(message, state)
            return

        await state.update_data(var_desc_idx=idx + 1)
        await ask_admin_next_var_description(message, state)

    @router.message(AdminStates.waiting_variable_description)
    async def admin_var_desc_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Description cannot be empty. Send description or /skip.")
            return
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            await ask_admin_next_var_description(message, state)
            return
        var = variables[idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        existing = get_variable_config(descriptions, token, var["type"])
        existing["description"] = text
        existing["type"] = var["type"]
        descriptions[token] = existing
        await state.update_data(variable_descriptions=descriptions)
        if var["type"] == "text":
            await ask_admin_text_options(message, state)
            return
        await state.update_data(var_desc_idx=idx + 1)
        await ask_admin_next_var_description(message, state)

    @router.message(AdminStates.waiting_text_options, Command("skip"))
    async def admin_text_options_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            await ask_admin_next_var_description(message, state)
            return
        var = variables[idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        existing = get_variable_config(descriptions, token, "text")
        existing["options"] = []
        existing["allow_custom"] = True
        descriptions[token] = existing
        await state.update_data(variable_descriptions=descriptions, var_desc_idx=idx + 1)
        await state.set_state(AdminStates.waiting_variable_description)
        await ask_admin_next_var_description(message, state)

    @router.message(AdminStates.waiting_text_options)
    async def admin_text_options_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Send options separated by ';' or /skip.")
            return
        options = [part.strip() for part in text.split(";") if part.strip()]
        if not options:
            await message.answer("No valid options found. Try again or /skip.")
            return

        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        var = variables[idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        existing = get_variable_config(descriptions, token, "text")
        existing["options"] = options
        descriptions[token] = existing
        await state.update_data(variable_descriptions=descriptions)
        await ask_admin_allow_custom(message, state)

    @router.callback_query(AdminStates.waiting_text_allow_custom, F.data.startswith("admin:allow_custom:"))
    async def admin_text_allow_custom_value(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        action = (callback.data or "").split(":")[-1]
        if action not in {"yes", "no"}:
            await callback.answer("Invalid choice", show_alert=True)
            return
        allow_custom = action == "yes"

        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        var = variables[idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        existing = get_variable_config(descriptions, token, "text")
        options = [str(x) for x in (existing.get("options") or []) if str(x).strip()]
        if not options:
            allow_custom = True
        existing["allow_custom"] = allow_custom
        descriptions[token] = existing
        await state.update_data(
            variable_descriptions=descriptions,
            var_desc_idx=idx + 1,
        )
        await callback.answer("Saved")
        await state.set_state(AdminStates.waiting_variable_description)
        await ask_admin_next_var_description(callback.message, state)

    @router.message(AdminStates.waiting_prompt_reference, Command("skip"))
    async def admin_prompt_skip_reference(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        user = await repo.get_user(message.from_user.id)
        prompt_id = data.get("editing_prompt_id")
        try:
            admin_mode = data.get("admin_mode", "create")
            if admin_mode == "edit_reference" and prompt_id is not None:
                await message.answer("Reference update cancelled.")
                prompt = await repo.get_prompt_by_id(int(prompt_id))
                if prompt:
                    await show_prompt_edit_actions(message, prompt)
            elif admin_mode == "edit" and prompt_id is not None:
                await repo.update_prompt(
                    prompt_id=int(prompt_id),
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                    reference_photo_file_id=None,
                )
                await message.answer("Prompt updated.")
            else:
                await repo.insert_prompt(
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                    reference_photo_file_id=None,
                    created_by=user["tg_id"] if user else message.from_user.id,
                )
                await message.answer("Prompt created.")
        except asyncpg.UniqueViolationError:
            await message.answer("Prompt with this title already exists.")
        finally:
            await state.clear()

    @router.message(AdminStates.waiting_prompt_reference, F.photo)
    async def admin_prompt_with_reference(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        user = await repo.get_user(message.from_user.id)
        prompt_id = data.get("editing_prompt_id")
        file_id = message.photo[-1].file_id
        try:
            admin_mode = data.get("admin_mode", "create")
            if admin_mode in {"edit", "edit_reference"} and prompt_id is not None:
                await repo.update_prompt(
                    prompt_id=int(prompt_id),
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                    reference_photo_file_id=file_id,
                )
                await message.answer("Prompt updated with reference image.")
                prompt = await repo.get_prompt_by_id(int(prompt_id))
                if prompt:
                    await show_prompt_edit_actions(message, prompt)
            else:
                await repo.insert_prompt(
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                    reference_photo_file_id=file_id,
                    created_by=user["tg_id"] if user else message.from_user.id,
                )
                await message.answer("Prompt created with reference image.")
        except asyncpg.UniqueViolationError:
            await message.answer("Prompt with this title already exists.")
        finally:
            await state.clear()

    @router.message(AdminStates.waiting_prompt_reference)
    async def admin_prompt_reference_invalid(message: Message) -> None:
        await message.answer("Send an image or /skip.")

    @router.message(AdminStates.waiting_prompt_examples, Command("done"))
    async def admin_prompt_examples_done(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        file_ids = data.get("example_file_ids") or []
        if not isinstance(file_ids, list):
            file_ids = []
        await state.clear()
        if prompt_id is None:
            await message.answer("Session expired. Open prompt edit again.")
            return
        await repo.set_prompt_examples(int(prompt_id), file_ids)
        prompt = await repo.get_prompt_by_id(int(prompt_id))
        await message.answer(f"Examples saved ({len(file_ids)}).")
        if prompt:
            await show_prompt_edit_actions(message, prompt)

    @router.message(AdminStates.waiting_prompt_examples, Command("skip"))
    async def admin_prompt_examples_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        await state.clear()
        if prompt_id is not None:
            await repo.set_prompt_examples(int(prompt_id), [])
        await message.answer("Examples cleared.")
        if prompt_id is not None:
            prompt = await repo.get_prompt_by_id(int(prompt_id))
            if prompt:
                await show_prompt_edit_actions(message, prompt)

    @router.message(AdminStates.waiting_prompt_examples, F.photo)
    async def admin_prompt_examples_photo(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        if prompt_id is None:
            await state.clear()
            await message.answer("Session expired. Open prompt edit again.")
            return
        file_ids = list(data.get("example_file_ids") or [])
        if not isinstance(file_ids, list):
            file_ids = []
        if len(file_ids) >= 3:
            await message.answer("Already 3 examples. Send /done to save or /skip to clear.")
            return
        file_id = message.photo[-1].file_id
        file_ids.append(file_id)
        await state.update_data(example_file_ids=file_ids)
        if len(file_ids) >= 3:
            await repo.set_prompt_examples(int(prompt_id), file_ids)
            await state.clear()
            prompt = await repo.get_prompt_by_id(int(prompt_id))
            await message.answer("Saved 3 examples.")
            if prompt:
                await show_prompt_edit_actions(message, prompt)
        else:
            await message.answer(f"Added ({len(file_ids)}/3). Send another photo or /done.")

    @router.message(AdminStates.waiting_prompt_examples)
    async def admin_prompt_examples_invalid(message: Message) -> None:
        await message.answer("Send 1–3 photos, then /done, or /skip to clear examples.")

    @router.message(StateFilter(None))
    async def prompt_pick_handler(message: Message, state: FSMContext) -> None:
        user = await ensure_user(message)
        if not user["is_authorized"]:
            await message.answer("Please use /start and enter password first.")
            return
        await message.answer("Please choose a prompt using inline buttons from /start.")

    @router.callback_query(GenerateStates.waiting_variable, F.data.startswith("gen:opt:"))
    async def generate_option_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("variables", [])
        current_idx: int = data.get("current_idx", 0)
        if current_idx >= len(variables):
            await callback.answer()
            await run_generation(callback.message, state)
            return

        variable = variables[current_idx]
        if variable["type"] != "text":
            await callback.answer("This variable expects image.", show_alert=True)
            return

        token = variable_token(variable)
        variable_descriptions = ensure_dict(data.get("variable_descriptions", {}))
        config = get_variable_config(variable_descriptions, token, "text")
        options: list[str] = [str(x) for x in (config.get("options") or []) if str(x).strip()]

        try:
            option_idx = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid option", show_alert=True)
            return
        if option_idx < 0 or option_idx >= len(options):
            await callback.answer("Invalid option", show_alert=True)
            return

        answers: dict[str, str] = data.get("answers", {})
        answers[variable["name"]] = options[option_idx]
        await state.update_data(
            answers=answers,
            current_idx=current_idx + 1,
            awaiting_custom_for=None,
        )
        await callback.answer("Selected")
        await ask_next_variable(callback.message, state)

    @router.callback_query(GenerateStates.waiting_variable, F.data == "gen:myown")
    async def generate_myown_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("variables", [])
        current_idx: int = data.get("current_idx", 0)
        if current_idx >= len(variables):
            await callback.answer()
            return

        variable = variables[current_idx]
        if variable["type"] != "text":
            await callback.answer("Invalid action", show_alert=True)
            return

        token = variable_token(variable)
        variable_descriptions = ensure_dict(data.get("variable_descriptions", {}))
        config = get_variable_config(variable_descriptions, token, "text")
        allow_custom = bool(config.get("allow_custom", True))
        if not allow_custom:
            await callback.answer("Custom input is disabled.", show_alert=True)
            return

        await state.update_data(awaiting_custom_for=token)
        await callback.answer()
        await callback.message.answer("Please type your own value.")

    @router.message(GenerateStates.waiting_variable)
    async def collect_variable_value(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("variables", [])
        current_idx: int = data.get("current_idx", 0)

        if current_idx >= len(variables):
            await run_generation(message, state)
            return

        variable = variables[current_idx]
        var_name = variable["name"]
        var_type = variable["type"]
        answers: dict[str, str] = data.get("answers", {})
        image_urls: list[str] = data.get("image_urls", [])
        token = variable_token(variable)
        variable_descriptions = ensure_dict(data.get("variable_descriptions", {}))
        config = get_variable_config(variable_descriptions, token, var_type)
        options: list[str] = [str(x) for x in (config.get("options") or []) if str(x).strip()]
        allow_custom = bool(config.get("allow_custom", True))
        awaiting_custom_for = data.get("awaiting_custom_for")

        if var_type == "image":
            if not message.photo:
                await message.answer("Please send a photo.")
                return
            file_id = message.photo[-1].file_id
            file_url = await telegram_file_url(file_id)
            image_urls.append(file_url)
            answers[var_name] = "provided reference image"
        else:
            if options:
                if awaiting_custom_for == token:
                    value = (message.text or "").strip()
                    if not value:
                        await message.answer("Please send text.")
                        return
                    answers[var_name] = value
                    await state.update_data(awaiting_custom_for=None)
                else:
                    await message.answer(
                        "Please choose one of the options using inline buttons.",
                        reply_markup=build_text_options_keyboard(options, allow_custom),
                    )
                    return
            else:
                value = (message.text or "").strip()
                if not value:
                    await message.answer("Please send text.")
                    return
                answers[var_name] = value

        await state.update_data(
            answers=answers,
            image_urls=image_urls,
            current_idx=current_idx + 1,
            awaiting_custom_for=None,
        )
        await ask_next_variable(message, state)

    return router


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()

    pool = await asyncpg.create_pool(settings.database_url)
    repo = Repo(pool)
    await repo.init()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    evo = EvoClient(settings)
    deepseek = DeepSeekClient() if DeepSeekClient else None
    dp.include_router(create_router(repo, settings, evo, bot, deepseek))

    logging.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

