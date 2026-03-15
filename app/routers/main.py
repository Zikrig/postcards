"""Compose all routers: auth, admin, user."""
from typing import Any, Optional

from aiogram import Bot, Router

from app.config import Settings
from app.evo_client import EvoClient
from app.repo import Repo

from .admin import register_admin
from .auth import register_auth
from .common import RouterCtx
from .user import register_user


def create_router(
    repo: Repo,
    settings: Settings,
    evo: EvoClient,
    bot: Bot,
    deepseek: Optional[Any] = None,
) -> Router:
    router = Router()
    ctx = RouterCtx(repo=repo, settings=settings, evo=evo, bot=bot, deepseek=deepseek)

    register_auth(router, ctx)
    register_admin(router, ctx)
    register_user(router, ctx)

    return router
