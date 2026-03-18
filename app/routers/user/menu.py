"""User menu handlers: main menu, tags, community browsing."""
import json
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.utils import ensure_dict
from app.routers.common import RouterCtx

logger = logging.getLogger(__name__)


def register_user_menu(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query(F.data == "menu:main")
    async def menu_main_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        await callback.answer()
        await state.clear()
        await ctx.edit_to_main_menu(callback.message)

    @router.callback_query(F.data.startswith("menu:community_tags"))
    async def community_tags_callback(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        await callback.answer()
        logger.info(f"Community tags callback hit: {callback.data}")
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.message.answer("Please use /start first.", show_alert=True)
            return
        data = (callback.data or "").strip()
        page = 0
        if data.startswith("menu:community_tags:"):
            try:
                page = int(data.split(":")[-1])
            except ValueError:
                pass
        await ctx.edit_to_community_tags(callback.message, page=page)

    @router.callback_query(F.data.startswith("menu:community_tag:"))
    async def community_tag_callback(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        await callback.answer()
        logger.info(f"Community tag callback hit: {callback.data}")
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        data = (callback.data or "").strip()
        parts = data.split(":")
        if len(parts) < 3:
            return
        try:
            tag_id = int(parts[2])
            page = int(parts[3]) if len(parts) > 3 else 0
        except ValueError:
            return
        await ctx.edit_to_community_prompts(callback.message, tag_id, page=page)

    @router.callback_query(F.data.startswith("menu:community_prompt:"))
    async def community_prompt_callback(callback: CallbackQuery) -> None:
        """Юзерское меню «Community»: карточка промпта без редактирования; Clone только для админа."""
        if not callback.message:
            return
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt", show_alert=True)
            return
        logger.info("community_prompt_callback: prompt_id=%s", prompt_id)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        if not prompt.get("is_public") or prompt.get("owner_tg_id") is None:
            await callback.answer("Not available", show_alert=True)
            return
        logger.info(
            "community_prompt_callback: loaded prompt id=%s title=%r ref_id=%r examples_raw=%r is_public=%r owner_tg_id=%r",
            prompt.get("id"),
            prompt.get("title"),
            prompt.get("reference_photo_file_id"),
            prompt.get("example_file_ids"),
            prompt.get("is_public"),
            prompt.get("owner_tg_id"),
        )
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        template = str(prompt.get("template") or "")
        desc = await ctx.format_prompt_description(prompt)

        is_admin = bool(user.get("is_admin"))
        owner_tg_id = prompt.get("owner_tg_id")
        is_owner = owner_tg_id == callback.from_user.id

        raw_examples = prompt.get("example_file_ids") or []
        if isinstance(raw_examples, str):
            try:
                raw_examples = json.loads(raw_examples) if raw_examples else []
            except json.JSONDecodeError:
                raw_examples = []
        if not isinstance(raw_examples, list):
            raw_examples = []
        example_ids = [str(f) for f in raw_examples[:3] if f]
        markup = ctx.build_prompt_card_markup(prompt, callback.from_user.id, back_callback="menu:community_tags:0")
        if example_ids:
            await callback.message.answer_photo(
                photo=example_ids[0],
                caption=desc,
                reply_markup=markup,
            )
        else:
            await callback.message.edit_text(desc, reply_markup=markup)
        await callback.answer()

    @router.callback_query(F.data.startswith("menu:tags"))
    async def menu_tags_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        logger.info(f"Menu tags callback hit: {callback.data}")
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        data = (callback.data or "").strip()
        page = 0
        if data.startswith("menu:tags:"):
            try:
                page = int(data.split(":")[-1])
            except ValueError:
                pass
        await callback.answer()
        # Кнопка All postcards должна показывать все категории (включая Community),
        # поэтому здесь открываем общее меню тегов.
        await ctx.edit_to_tags_menu(callback.message, page=page)

    @router.callback_query(F.data.startswith("menu:tag:"))
    async def menu_tag_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 3:
            await callback.answer("Invalid tag", show_alert=True)
            return
        try:
            tag_id = int(parts[2])
            page = int(parts[3]) if len(parts) > 3 else 0
        except (ValueError, IndexError):
            await callback.answer("Invalid tag", show_alert=True)
            return
        await callback.answer()
        await ctx.edit_to_prompts_for_tag(callback.message, tag_id, page=page)
