import logging
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from app.routers.common import RouterCtx


def register_shared_prompt_card(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query(F.data.startswith("admin:pw:item:"))
    async def admin_prompt_item_actions(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        is_admin = bool(user and user.get("is_admin"))
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return

        owner_tg_id = prompt.get("owner_tg_id")
        is_owner = owner_tg_id == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("No permission", show_alert=True)
            return

        await ctx.present_prompt_card(
            callback.message,
            prompt,
            callback.from_user.id,
            back_callback="admin:pw:list",
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:active:"))
    async def admin_toggle_active(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        logging.info(
            "admin_toggle_active: prompt_id=%s, owner_tg_id=%s, is_admin=%s, is_owner=%s",
            prompt_id,
            prompt.get("owner_tg_id"),
            is_admin,
            is_owner,
        )
        if not (is_admin or is_owner):
            logging.warning("admin_toggle_active: no permission, answering Not allowed")
            await callback.answer("Not allowed", show_alert=True)
            return
        new_active = not bool(prompt.get("is_active", True))
        await ctx.repo.set_prompt_active(prompt_id, new_active)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer()
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id

        await ctx.show_prompt_card(callback.message, prompt, callback.from_user.id)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:toggle_public:"))
    async def admin_toggle_public(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        parts = (callback.data or "").split(":")
        prompt_id = int(parts[2])
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return

        is_admin = callback.from_user.id in ctx.settings.admin_ids
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("No permission", show_alert=True)
            return

        new_status = not prompt.get("is_public", False)
        await ctx.repo.update_prompt_public(prompt_id, new_status)
        await callback.answer(f"Status changed to {'Public' if new_status else 'Private'}")

        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if prompt:
            markup = ctx.build_prompt_card_markup(prompt, callback.from_user.id)
            try:
                await callback.message.edit_reply_markup(reply_markup=markup)
            except TelegramBadRequest:
                pass
