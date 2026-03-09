import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp
import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
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
    waiting_prompt_reference = State()


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

    async def wait_for_completion(self, task_id: str) -> dict[str, Any]:
        started = time.time()
        while True:
            details = await self.get_task(task_id)
            status = details.get("status")
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
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS prompts (
                    id SERIAL PRIMARY KEY,
                    title TEXT UNIQUE NOT NULL,
                    template TEXT NOT NULL,
                    reference_photo_file_id TEXT,
                    created_by BIGINT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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

    async def list_prompts(self) -> list[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetch("SELECT * FROM prompts ORDER BY id DESC")

    async def get_prompt_by_title(self, title: str) -> Optional[asyncpg.Record]:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow("SELECT * FROM prompts WHERE title = $1", title)

    async def insert_prompt(
        self,
        title: str,
        template: str,
        reference_photo_file_id: Optional[str],
        created_by: int,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO prompts (title, template, reference_photo_file_id, created_by)
                VALUES ($1, $2, $3, $4)
                """,
                title,
                template,
                reference_photo_file_id,
                created_by,
            )


def build_main_menu(prompt_titles: list[str]) -> ReplyKeyboardMarkup:
    buttons = [[KeyboardButton(text=title)] for title in prompt_titles]
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        input_field_placeholder="Choose a prompt",
    )


def build_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Create Prompt", callback_data="admin:create_prompt")]
        ]
    )


def create_router(repo: Repo, settings: Settings, evo: EvoClient, bot: Bot) -> Router:
    router = Router()

    async def ensure_user(message: Message) -> asyncpg.Record:
        tg_user = message.from_user
        assert tg_user is not None
        full_name = (tg_user.full_name or "").strip()
        return await repo.upsert_user(
            tg_id=tg_user.id,
            username=tg_user.username or "",
            full_name=full_name,
            is_admin=tg_user.id in settings.admin_ids,
        )

    async def show_prompt_buttons(message: Message) -> None:
        prompts = await repo.list_prompts()
        titles = [p["title"] for p in prompts]
        if not titles:
            await message.answer("No prompts yet. Please wait for admin to add them.")
            return
        await message.answer("Select a prompt:", reply_markup=build_main_menu(titles))

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
        if var_type == "image":
            await message.answer(
                f"Please send an image for variable: {var_name}",
                reply_markup=ReplyKeyboardRemove(),
            )
        else:
            await message.answer(
                f"Please enter value for variable: {var_name}",
                reply_markup=ReplyKeyboardRemove(),
            )

    async def run_generation(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        template = data["template"]
        answers: dict[str, str] = data.get("answers", {})
        image_urls: list[str] = data.get("image_urls", [])
        prompt_title = data["prompt_title"]

        final_prompt = render_prompt(template, answers)
        await message.answer(f"Generating image for prompt: {prompt_title}...")

        try:
            task_id = await evo.create_task(final_prompt, image_urls=image_urls)
            details = await evo.wait_for_completion(task_id)

            if details.get("status") != "completed":
                await message.answer(f"Generation failed: {details}")
                return

            results = details.get("results") or []
            if not results:
                await message.answer("Generation completed, but no images were returned.")
                return

            for url in results:
                await message.answer_photo(photo=url)

            credits = await evo.get_credits()
            token = (credits.get("data") or {}).get("token") or {}
            user = (credits.get("data") or {}).get("user") or {}
            await message.answer(
                "Balance:\n"
                f"- Token remaining: {token.get('remaining_credits')}\n"
                f"- Token used: {token.get('used_credits')}\n"
                f"- User remaining: {user.get('remaining_credits')}\n"
                f"- User used: {user.get('used_credits')}"
            )
        except Exception as e:
            await message.answer(f"Error: {e}")
        finally:
            await state.clear()
            await show_prompt_buttons(message)

    async def telegram_file_url(file_id: str) -> str:
        file = await bot.get_file(file_id)
        return f"https://api.telegram.org/file/bot{settings.bot_token}/{file.file_path}"

    @router.message(CommandStart())
    async def start_handler(message: Message, state: FSMContext) -> None:
        user = await ensure_user(message)
        await state.clear()

        if user["is_authorized"]:
            await message.answer("Welcome! You are authorized.")
            await show_prompt_buttons(message)
            return

        await message.answer("Welcome! Please enter password to continue:")
        await state.set_state(AuthStates.waiting_password)

    @router.message(AuthStates.waiting_password)
    async def password_handler(message: Message, state: FSMContext) -> None:
        user = await ensure_user(message)
        text = (message.text or "").strip()
        if text == settings.user_password:
            await repo.set_user_authorized(user["tg_id"], True)
            await message.answer("Access granted.")
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

    @router.callback_query(F.data == "admin:create_prompt")
    async def admin_create_prompt_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await callback.message.answer("Send prompt title:")
        await state.set_state(AdminStates.waiting_prompt_title)
        await callback.answer()

    @router.message(AdminStates.waiting_prompt_title)
    async def admin_prompt_title(message: Message, state: FSMContext) -> None:
        title = (message.text or "").strip()
        if not title:
            await message.answer("Title cannot be empty. Send prompt title:")
            return
        await state.update_data(prompt_title=title)
        await state.set_state(AdminStates.waiting_prompt_template)
        await message.answer(
            "Send prompt template. Use [var] or <var> for variables.\n"
            "Example: Photorealistic astronauts on <planet_name> with [user_photo]."
        )

    @router.message(AdminStates.waiting_prompt_template)
    async def admin_prompt_template(message: Message, state: FSMContext) -> None:
        template = (message.text or "").strip()
        if not template:
            await message.answer("Template cannot be empty. Send prompt template:")
            return
        await state.update_data(prompt_template=template)
        await state.set_state(AdminStates.waiting_prompt_reference)
        await message.answer(
            "Send optional reference image now, or type /skip to continue without it."
        )

    @router.message(AdminStates.waiting_prompt_reference, Command("skip"))
    async def admin_prompt_skip_reference(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        user = await repo.get_user(message.from_user.id)
        try:
            await repo.insert_prompt(
                title=data["prompt_title"],
                template=data["prompt_template"],
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
            await repo.insert_prompt(
                title=data["prompt_title"],
                template=data["prompt_template"],
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

        if not message.text:
            await message.answer("Please choose a prompt button.")
            return

        prompt = await repo.get_prompt_by_title(message.text.strip())
        if not prompt:
            await message.answer("Unknown prompt. Use /start to reload menu.")
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
            reference_photo_file_id=prompt["reference_photo_file_id"],
        )

        if prompt["reference_photo_file_id"]:
            try:
                ref_url = await telegram_file_url(prompt["reference_photo_file_id"])
                data = await state.get_data()
                image_urls = data.get("image_urls", [])
                image_urls.append(ref_url)
                await state.update_data(image_urls=image_urls)
            except Exception as e:
                await message.answer(f"Warning: could not load reference image: {e}")

        if not variables:
            await run_generation(message, state)
            return

        await ask_next_variable(message, state)

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

        if var_type == "image":
            if not message.photo:
                await message.answer(f"Variable '{var_name}' expects an image. Please send a photo.")
                return
            file_id = message.photo[-1].file_id
            file_url = await telegram_file_url(file_id)
            image_urls.append(file_url)
            answers[var_name] = "provided reference image"
        else:
            value = (message.text or "").strip()
            if not value:
                await message.answer(f"Variable '{var_name}' expects text. Please try again.")
                return
            answers[var_name] = value

        await state.update_data(
            answers=answers,
            image_urls=image_urls,
            current_idx=current_idx + 1,
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

