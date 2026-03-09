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
    waiting_variable_description = State()
    waiting_text_options = State()
    waiting_text_allow_custom = State()
    waiting_prompt_reference = State()
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

    async def list_prompts(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
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
        variable_descriptions: dict[str, str],
        reference_photo_file_id: Optional[str],
        created_by: int,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO prompts (title, template, variable_descriptions, reference_photo_file_id, created_by)
                VALUES ($1, $2, $3::jsonb, $4, $5)
                """,
                title,
                template,
                json.dumps(variable_descriptions),
                reference_photo_file_id,
                created_by,
            )

    async def update_prompt(
        self,
        prompt_id: int,
        title: str,
        template: str,
        variable_descriptions: dict[str, str],
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
        [InlineKeyboardButton(text=p["title"], callback_data=f"prompt:select:{p['id']}")]
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
            [InlineKeyboardButton(text="Add prompt", callback_data="admin:pw:add")],
            [InlineKeyboardButton(text="Edit prompt", callback_data="admin:pw:edit")],
            [InlineKeyboardButton(text="Delete prompt", callback_data="admin:pw:delete")],
            [InlineKeyboardButton(text="Back", callback_data="admin:pw:back")],
        ]
    )


def build_promo_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Create single-user promo", callback_data="admin:promo:create:single")],
            [InlineKeyboardButton(text="Create multi-user promo", callback_data="admin:promo:create:multi")],
            [InlineKeyboardButton(text="Back", callback_data="admin:promo:back")],
        ]
    )


def create_router(repo: Repo, settings: Settings, evo: EvoClient, bot: Bot) -> Router:
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
        prompts = await repo.list_prompts()
        if not prompts:
            await message.answer("No prompts yet. Please wait for admin to add them.")
            return
        await message.answer("Select a prompt:", reply_markup=build_main_menu(prompts))

    def get_variable_config(
        raw_configs: dict[str, Any],
        token: str,
        var_type: str,
    ) -> dict[str, Any]:
        raw = raw_configs.get(token)
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

    def build_text_options_keyboard(options: list[str], allow_custom: bool) -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton(text=opt, callback_data=f"gen:opt:{idx}")]
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
        variable_descriptions: dict[str, Any] = data.get("variable_descriptions", {})
        config = get_variable_config(variable_descriptions, token, var_type)
        description = str(config.get("description") or "").strip()
        options: list[str] = [str(x) for x in (config.get("options") or []) if str(x).strip()]
        allow_custom = bool(config.get("allow_custom", True))

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

            if details.get("status") != "completed":
                await progress_message.delete()
                await message.answer(f"Generation failed: {details}")
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
            crossed_value = current_bucket * 20
            user_id = state_data.get("request_user_id")
            username = state_data.get("request_username") or "no_username"
            full_name = state_data.get("request_full_name") or "Unknown user"
            user_label = f"{full_name} (@{username}, id={user_id})"

            admin_text = (
                "Balance checkpoint reached.\n"
                f"Triggered by: {user_label}\n"
                f"User remaining credits: {remaining}\n"
                f"Crossed threshold: <= {crossed_value}"
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

        if payload:
            success, promo_message, granted = await repo.redeem_promo_code(payload, user["tg_id"])
            if success:
                new_balance = await repo.get_user_balance(user["tg_id"])
                await message.answer(
                    f"{promo_message}\n"
                    f"Granted: {granted}\n"
                    f"Your balance: {new_balance}"
                )
            else:
                await message.answer(promo_message)

        if user["is_authorized"]:
            balance = await repo.get_user_balance(user["tg_id"])
            await message.answer(
                "Welcome!\n"
                "Choose one of the prompt buttons below.\n"
                "For each prompt, I will ask for required values and then generate an image.\n"
                f"Your balance: {balance}"
            )
            await show_prompt_buttons(message)
            return

        await message.answer(
            "Welcome to the Image Generation Bot.\n"
            "How it works:\n"
            "- You choose a prompt from buttons\n"
            "- Variables in [ ] mean image input\n"
            "- Variables in < > mean text input\n"
            "- I generate an image\n\n"
            "Please enter password to continue:"
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
        await callback.message.answer("Prompt work:", reply_markup=build_prompt_work_menu())
        await callback.answer()

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
        await callback.message.answer("Promo codes:", reply_markup=build_promo_menu())
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
        await state.set_state(AdminStates.waiting_promo_code)
        await callback.message.answer("Send promo code text (for start link payload).")
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
            await repo.create_promo_code(
                code=str(data["promo_code"]),
                credits_amount=int(data["promo_credits"]),
                max_uses=data.get("promo_max_uses"),
                created_by=user["tg_id"] if user else message.from_user.id,
            )
            me = await bot.get_me()
            if me.username:
                link = f"https://t.me/{me.username}?start={data['promo_code']}"
                await message.answer(
                    "Promo code created.\n"
                    f"Start link: {link}"
                )
            else:
                await message.answer(
                    "Promo code created.\n"
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
            variable_descriptions=prompt.get("variable_descriptions") or {},
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

    @router.callback_query(F.data == "admin:pw:delete")
    async def admin_delete_prompt_start(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return

        prompts = await repo.list_prompts()
        if not prompts:
            await callback.message.answer("No prompts to delete.")
            await callback.answer()
            return

        buttons = [
            [InlineKeyboardButton(text=f"Delete: {p['title']}", callback_data=f"admin:delete:{p['id']}")]
            for p in prompts
        ]
        await callback.message.answer(
            "Select prompt to delete:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()

    @router.callback_query(F.data == "admin:pw:edit")
    async def admin_edit_prompt_start(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        prompts = await repo.list_prompts()
        if not prompts:
            await callback.message.answer("No prompts to edit.")
            await callback.answer()
            return
        buttons = [
            [InlineKeyboardButton(text=f"Edit: {p['title']}", callback_data=f"admin:edit:{p['id']}")]
            for p in prompts
        ]
        await callback.message.answer(
            "Select prompt to edit:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
        await callback.answer()

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
        await state.update_data(admin_mode="edit", editing_prompt_id=prompt_id)
        await callback.message.answer(
            f"Editing prompt: {prompt['title']}\n"
            "Send new prompt title:"
        )
        await state.set_state(AdminStates.waiting_prompt_title)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:delete:"))
    async def admin_delete_prompt_confirm(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return

        try:
            prompt_id = int(callback.data.split(":")[-1])  # type: ignore[union-attr]
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

    @router.message(AdminStates.waiting_variable_description, Command("skip"))
    async def admin_var_desc_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            await state.set_state(AdminStates.waiting_prompt_reference)
            await message.answer(
                "Send optional reference image now, or type /skip to continue without it."
            )
            return

        var = variables[idx]
        token = variable_token(var)
        variable_descriptions: dict[str, Any] = data.get("variable_descriptions", {})
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
            await state.set_state(AdminStates.waiting_prompt_reference)
            await message.answer(
                "Send optional reference image now, or type /skip to continue without it."
            )
            return
        var = variables[idx]
        token = variable_token(var)
        descriptions: dict[str, Any] = data.get("variable_descriptions", {})
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
            await state.set_state(AdminStates.waiting_prompt_reference)
            await message.answer(
                "Send optional reference image now, or type /skip to continue without it."
            )
            return
        var = variables[idx]
        token = variable_token(var)
        descriptions: dict[str, Any] = data.get("variable_descriptions", {})
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
        descriptions: dict[str, Any] = data.get("variable_descriptions", {})
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
        descriptions: dict[str, Any] = data.get("variable_descriptions", {})
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
        try:
            admin_mode = data.get("admin_mode", "create")
            if admin_mode == "edit" and data.get("editing_prompt_id") is not None:
                await repo.update_prompt(
                    prompt_id=int(data["editing_prompt_id"]),
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=data.get("variable_descriptions", {}),
                    reference_photo_file_id=None,
                )
                await message.answer("Prompt updated.")
            else:
                await repo.insert_prompt(
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=data.get("variable_descriptions", {}),
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
        file_id = message.photo[-1].file_id
        try:
            admin_mode = data.get("admin_mode", "create")
            if admin_mode == "edit" and data.get("editing_prompt_id") is not None:
                await repo.update_prompt(
                    prompt_id=int(data["editing_prompt_id"]),
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=data.get("variable_descriptions", {}),
                    reference_photo_file_id=file_id,
                )
                await message.answer("Prompt updated with reference image.")
            else:
                await repo.insert_prompt(
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=data.get("variable_descriptions", {}),
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
        variable_descriptions: dict[str, Any] = data.get("variable_descriptions", {})
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
        variable_descriptions: dict[str, Any] = data.get("variable_descriptions", {})
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
        variable_descriptions: dict[str, Any] = data.get("variable_descriptions", {})
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
    dp.include_router(create_router(repo, settings, evo, bot))

    logging.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

