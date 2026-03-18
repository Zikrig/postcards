from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from app.keyboards.admin import build_prompt_edit_tags_menu
from app.states import AdminStates
from app.routers.common import RouterCtx


def register_shared_tags(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query(F.data.startswith("admin:editpart:tags:"))
    async def admin_editpart_tags(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid prompt", show_alert=True)
            return
        try:
            prompt_id = int(parts[3])
            page = int(parts[4]) if len(parts) > 4 else 0
        except (ValueError, IndexError):
            await callback.answer("Invalid prompt", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
            return
        tag_ids = await ctx.repo.get_prompt_tag_ids(prompt_id)
        assigned_ids = set(tag_ids)
        if is_admin:
            tags, total = await ctx.repo.list_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
            back_cb = f"admin:pw:item:{prompt_id}"
        else:
            tags, total = await ctx.repo.list_community_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
            back_cb = f"menu:my_prompt_item:{prompt_id}"
        try:
            await callback.message.edit_text(
                "Tags: 🟢 = assigned, 🔴 = not assigned. Click to toggle.",
                reply_markup=build_prompt_edit_tags_menu(
                    prompt_id,
                    tags,
                    assigned_ids,
                    page=page,
                    total=total,
                    back_callback=back_cb,
                ),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "Tags: 🟢 = assigned, 🔴 = not assigned. Click to toggle.",
                reply_markup=build_prompt_edit_tags_menu(
                    prompt_id,
                    tags,
                    assigned_ids,
                    page=page,
                    total=total,
                    back_callback=back_cb,
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:tag_add:"))
    async def admin_or_user_tag_add(callback: CallbackQuery, state: FSMContext) -> None:
        """
        Добавление нового тега из экрана редактирования тегов промпта.
        Доступно админу (общие теги) и владельцу промпта (community-теги).
        """
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        parts = (callback.data or "").split(":")
        if len(parts) < 5:
            await callback.answer("Invalid prompt", show_alert=True)
            return
        try:
            prompt_id = int(parts[3])
            page = int(parts[4])
        except (ValueError, IndexError):
            await callback.answer("Invalid prompt", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
            return

        if not is_admin:
            tag_ids = await ctx.repo.get_prompt_tag_ids(prompt_id)
            if len(tag_ids) >= 5:
                await callback.answer("Maximum 5 tags allowed for one prompt.", show_alert=True)
                return

        await state.update_data(tag_add_prompt_id=prompt_id, tag_add_page=page, tag_add_is_admin=is_admin)
        await state.set_state(AdminStates.waiting_tag_name)
        await callback.message.answer("Enter new tag name:")
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:tag_toggle:"))
    async def admin_editpart_tag_toggle(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        parts = (callback.data or "").split(":")
        if len(parts) < 6:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[3])
            tag_id = int(parts[4])
            page = int(parts[5])
        except (ValueError, IndexError):
            await callback.answer("Invalid", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
            return
        tag_ids = await ctx.repo.get_prompt_tag_ids(prompt_id)
        if tag_id in tag_ids:
            tag_ids.remove(tag_id)
        else:
            if not is_admin and len(tag_ids) >= 5:
                await callback.answer("Maximum 5 tags allowed for one prompt.", show_alert=True)
                return
            tag_ids.append(tag_id)
        await ctx.repo.set_prompt_tags(prompt_id, tag_ids)
        assigned_ids = set(await ctx.repo.get_prompt_tag_ids(prompt_id))
        if is_admin:
            tags, total = await ctx.repo.list_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
            back_cb = f"admin:pw:item:{prompt_id}"
        else:
            tags, total = await ctx.repo.list_community_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
            back_cb = f"menu:my_prompt_item:{prompt_id}"
        try:
            await callback.message.edit_text(
                "Tags: 🟢 = assigned, 🔴 = not assigned. Click to toggle.",
                reply_markup=build_prompt_edit_tags_menu(
                    prompt_id,
                    tags,
                    assigned_ids,
                    page=page,
                    total=total,
                    back_callback=back_cb,
                ),
            )
        except TelegramBadRequest:
            pass
        await callback.answer()
