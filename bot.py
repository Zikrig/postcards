"""Telegram bot entry point. All application code lives in the app package."""
import asyncio
import logging
from typing import Any, Optional

import asyncpg
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from app.config import load_settings, Settings
from app.evo_client import EvoClient
from app.repo import Repo
from app.routers import create_router

try:
    from app.deepseek_client import DeepSeekClient
except ImportError:
    DeepSeekClient = None  # type: ignore[misc, assignment]


def _register_logging_middleware(dp: Dispatcher) -> None:
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


async def _init_services(settings: Settings):
    pool = await asyncpg.create_pool(settings.database_url)
    logging.info("Database pool created")
    repo = Repo(pool)
    await repo.init()
    logging.info("Repo initialized")
    evo = EvoClient(settings)
    deepseek: Optional[Any] = DeepSeekClient() if DeepSeekClient else None
    return repo, evo, deepseek


async def _run_polling(settings: Settings) -> None:
    repo, evo, deepseek = await _init_services(settings)
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    from app.routers.common import AlbumMiddleware
    dp.message.middleware(AlbumMiddleware())
    _register_logging_middleware(dp)

    dp.include_router(create_router(repo, settings, evo, bot, deepseek))

    logging.info("Bot started (polling mode)")
    await dp.start_polling(bot)


def _run_webhook(settings: Settings) -> None:
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    from app.routers.common import AlbumMiddleware
    dp.message.middleware(AlbumMiddleware())
    _register_logging_middleware(dp)

    webhook_path = f"/webhook/{settings.bot_token}"
    domain = settings.webhook_domain.rstrip("/")
    webhook_url = f"{domain}{webhook_path}"

    async def on_startup(app: web.Application) -> None:
        repo, evo, deepseek = await _init_services(settings)
        dp.include_router(create_router(repo, settings, evo, bot, deepseek))
        await bot.set_webhook(webhook_url)
        logging.info(f"Webhook set: {webhook_url}")

    async def on_shutdown(app: web.Application) -> None:
        await bot.delete_webhook()
        logging.info("Webhook removed")

    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    handler.register(app, path=webhook_path)
    setup_application(app, dp)

    logging.info(f"Bot starting (webhook mode) on port {settings.webhook_port}")
    web.run_app(app, host="0.0.0.0", port=settings.webhook_port)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.info("Starting bot process...")

    try:
        settings = load_settings()
        logging.info("Settings loaded")

        if settings.webhook_on:
            _run_webhook(settings)
        else:
            asyncio.run(_run_polling(settings))
    except Exception as e:
        logging.critical(f"FATAL ERROR: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
