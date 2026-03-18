from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.admin import (
    build_admin_menu,
    build_admin_tags_menu,
    build_admin_tag_item_menu,
    build_prompt_edit_tags_menu,
)
from app.states import AdminStates
from app.routers.common import RouterCtx


def register_admin_tags(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query(
        (F.data == "admin:tags") | (F.data.startswith("admin:tags:") & (F.data != "admin:tags:back"))
    )
    async def admin_tags_menu(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        data = (callback.data or "").strip()
        page = 0
        if data.startswith("admin:tags:") and data != "admin:tags:back":
            try:
                page = int(data.split(":")[-1])
            except ValueError:
                page = 0
        tags, total = await ctx.repo.list_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
        try:
            await callback.message.edit_text(
                "Tags:",
                reply_markup=build_admin_tags_menu(tags, page=page, total=total),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "Tags:",
                reply_markup=build_admin_tags_menu(tags, page=page, total=total),
            )
        await callback.answer()

    @router.callback_query(F.data == "admin:tags:back")
    async def admin_tags_back(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            await callback.message.edit_text("Admin panel:", reply_markup=build_admin_menu())
        except TelegramBadRequest:
            await callback.message.answer("Admin panel:", reply_markup=build_admin_menu())
        await callback.answer()

    @router.callback_query(F.data == "admin:tag:add")
    async def admin_tag_add(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        await state.set_state(AdminStates.waiting_tag_name)
        await callback.message.answer("Enter tag name:")
        await callback.answer()

    @router.message(AdminStates.waiting_tag_name)
    async def admin_tag_name_entered(message: Message, state: FSMContext) -> None:
        """
        Ввод имени тега:
        - если есть контекст tag_add_* → добавление тега из экрана тегов промпта (admin/owner),
          затем возврат на этот же экран;
        - иначе классический админский сценарий добавления глобального тега.
        """
        user = await ctx.repo.get_user(message.from_user.id)
        data = await state.get_data()
        name = (message.text or "").strip()
        if not name:
            await message.answer("Tag name cannot be empty. Enter tag name:")
            return

        prompt_id = data.get("tag_add_prompt_id")
        if prompt_id:
            # Добавление тега из экрана конкретного промпта
            page = int(data.get("tag_add_page", 0))
            is_admin_ctx = bool(data.get("tag_add_is_admin"))

            prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
            if not prompt:
                await state.clear()
                await message.answer("Prompt not found.")
                return

            is_admin = bool(user and user.get("is_admin"))
            is_owner = prompt.get("owner_tg_id") == message.from_user.id
            if not (is_admin or is_owner):
                await state.clear()
                await message.answer("Not allowed.")
                return

            tag_ids = await ctx.repo.get_prompt_tag_ids(int(prompt_id))
            if not is_admin and len(tag_ids) >= 5:
                await message.answer("Maximum 5 tags allowed for one prompt.")
                await state.clear()
                # Optionally return the user to the tags menu here, but state is cleared now.
                return

            tag = await ctx.repo.create_tag(name)
            tag_id = int(tag["id"])
            # Привязываем новый тег к промпту
            if tag_id not in tag_ids:
                tag_ids.append(tag_id)
                await ctx.repo.set_prompt_tags(int(prompt_id), tag_ids)

            # Обновляем экран тегов
            assigned_ids = set(await ctx.repo.get_prompt_tag_ids(int(prompt_id)))
            if is_admin_ctx:
                tags, total = await ctx.repo.list_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
                back_cb = f"admin:pw:item:{prompt_id}"
            else:
                tags, total = await ctx.repo.list_community_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
                back_cb = f"menu:my_prompt_item:{prompt_id}"

            await state.clear()
            await message.answer(
                "Tag added.",
                reply_markup=build_prompt_edit_tags_menu(
                    int(prompt_id),
                    tags,
                    assigned_ids,
                    page=page,
                    total=total,
                    back_callback=back_cb,
                ),
            )
        else:
            # Старый админский сценарий: добавление глобального тега из админ-меню
            if not user or not user["is_admin"]:
                return
            await ctx.repo.create_tag(name)
            await state.clear()
            tags, total = await ctx.repo.list_tags_paginated(page=0, per_page=ctx.repo.PAGE_SIZE)
            await message.answer("Tag added. Tags:", reply_markup=build_admin_tags_menu(tags, page=0, total=total))

    @router.callback_query(F.data.startswith("admin:tag:item:"))
    async def admin_tag_item(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            tag_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid tag", show_alert=True)
            return
        tag = await ctx.repo.get_tag_by_id(tag_id)
        if not tag:
            await callback.answer("Tag not found", show_alert=True)
            return
        await callback.message.answer(f"Tag: {tag['name']}", reply_markup=build_admin_tag_item_menu(tag_id))
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:tag:edit:"))
    async def admin_tag_edit(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            tag_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid tag", show_alert=True)
            return
        await state.update_data(editing_tag_id=tag_id)
        await state.set_state(AdminStates.waiting_tag_edit_name)
        await callback.message.answer("Enter new tag name:")
        await callback.answer()

    @router.message(AdminStates.waiting_tag_edit_name)
    async def admin_tag_edit_name_entered(message: Message, state: FSMContext) -> None:
        user = await ctx.repo.get_user(message.from_user.id)
        if not user or not user["is_admin"]:
            return
        data = await state.get_data()
        tag_id = data.get("editing_tag_id")
        if tag_id is None:
            await state.clear()
            return
        name = (message.text or "").strip()
        if not name:
            await message.answer("Tag name cannot be empty. Enter new tag name:")
            return
        await ctx.repo.update_tag(int(tag_id), name)
        await state.clear()
        tags, total = await ctx.repo.list_tags_paginated(page=0, per_page=ctx.repo.PAGE_SIZE)
        await message.answer("Tag renamed. Tags:", reply_markup=build_admin_tags_menu(tags, page=0, total=total))

    @router.callback_query(F.data.startswith("admin:tag:delete:"))
    async def admin_tag_delete(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            tag_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid tag", show_alert=True)
            return
        await ctx.repo.delete_tag(tag_id)
        tags, total = await ctx.repo.list_tags_paginated(page=0, per_page=ctx.repo.PAGE_SIZE)
        try:
            await callback.message.edit_text("Tags:", reply_markup=build_admin_tags_menu(tags, page=0, total=total))
        except TelegramBadRequest:
            await callback.message.answer("Tags:", reply_markup=build_admin_tags_menu(tags, page=0, total=total))
        await callback.answer()
