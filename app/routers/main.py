"""Compose all routers: user (auth, menu, generation, my_prompts), admin, shared."""
from typing import Any, Optional

from aiogram import Bot, Router

from app.config import Settings
from app.evo_client import EvoClient
from app.repo import Repo

from .common import RouterCtx
from .user.auth import register_user_auth
from .user.menu import register_user_menu
from .user.generation import register_user_generation
from .user.my_prompts import register_user_my_prompts
from .admin.panel import register_admin_panel
from .admin.prompts import register_admin_prompts
from .admin.tags import register_admin_tags
from .admin.promo import register_admin_promo
from .shared.prompt_card import register_shared_prompt_card
from .shared.prompt_editing import register_shared_editing
from .shared.variables import register_shared_variables
from .shared.features import register_shared_features
from .shared.tags import register_shared_tags
from .shared.actions import register_shared_actions


def create_router(
    repo: Repo,
    settings: Settings,
    evo: EvoClient,
    bot: Bot,
    deepseek: Optional[Any] = None,
) -> Router:
    router = Router()
    ctx = RouterCtx(repo=repo, settings=settings, evo=evo, bot=bot, deepseek=deepseek)

    register_user_auth(router, ctx)
    register_admin_panel(router, ctx)
    register_admin_prompts(router, ctx)
    register_admin_tags(router, ctx)
    register_admin_promo(router, ctx)
    register_shared_prompt_card(router, ctx)
    register_shared_editing(router, ctx)
    register_shared_variables(router, ctx)
    register_shared_features(router, ctx)
    register_shared_tags(router, ctx)
    register_shared_actions(router, ctx)
    register_user_menu(router, ctx)
    register_user_my_prompts(router, ctx)
    register_user_generation(router, ctx)

    return router
