"""Telegram bot entry point. All application code lives in the app package."""
import asyncio
import logging
from typing import Any, Optional

import asyncpg
from aiogram import Bot, Dispatcher

from app.config import load_settings
from app.evo_client import EvoClient
from app.repo import Repo
from app.routers import create_router

try:
    from app.deepseek_client import DeepSeekClient
except ImportError:
    DeepSeekClient = None  # type: ignore[misc, assignment]


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()

    pool = await asyncpg.create_pool(settings.database_url)
    repo = Repo(pool)
    await repo.init()

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    evo = EvoClient(settings)
    deepseek: Optional[Any] = DeepSeekClient() if DeepSeekClient else None
    dp.include_router(create_router(repo, settings, evo, bot, deepseek))

    logging.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
