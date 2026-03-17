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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    logging.info("Starting bot process...")
    try:
        settings = load_settings()
        logging.info("Settings loaded")

        pool = await asyncpg.create_pool(settings.database_url)
        logging.info("Database pool created")
        repo = Repo(pool)
        await repo.init()
        logging.info("Repo initialized")

        bot = Bot(token=settings.bot_token)
        dp = Dispatcher()

        @dp.update.outer_middleware()
        async def logging_middleware(handler, event, data):
            try:
                logger = logging.getLogger("updates")
                if event.message:
                    user_id = event.message.from_user.id if event.message.from_user else "unknown"
                    logger.info(f"Incoming message from {user_id}: {event.message.text}")
                elif event.callback_query:
                    user_id = event.callback_query.from_user.id if event.callback_query.from_user else "unknown"
                    logger.info(f"Incoming callback from {user_id}: {event.callback_query.data}")
                else:
                    logger.info(f"Incoming update: {event.event_type}")
            except Exception as e:
                logging.error(f"Error in logging middleware: {e}")
            
            return await handler(event, data)

        evo = EvoClient(settings)
        deepseek: Optional[Any] = DeepSeekClient() if DeepSeekClient else None
        dp.include_router(create_router(repo, settings, evo, bot, deepseek))

        logging.info("Bot started and polling...")
        await dp.start_polling(bot)
    except Exception as e:
        logging.critical(f"FATAL ERROR: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
