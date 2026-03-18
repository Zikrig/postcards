import asyncpg
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.admin import (
    build_admin_menu,
    build_promo_item_menu,
    build_promo_list_menu,
    build_promo_menu,
)
from app.states import AdminStates
from app.routers.common import RouterCtx


def register_admin_promo(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query(F.data == "admin:promo_menu")
    async def admin_promo_menu(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        promos = await ctx.repo.list_promo_codes()
        if promos:
            await callback.message.answer("Promo code list:", reply_markup=build_promo_list_menu(promos))
        else:
            await callback.message.answer(
                "Promo code list is empty.",
                reply_markup=build_promo_menu(),
            )
        await callback.answer()

    @router.callback_query(F.data == "admin:promo:back")
    async def admin_promo_back(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        await callback.message.answer("Admin panel:", reply_markup=build_admin_menu())
        await callback.answer()

    @router.callback_query(F.data == "admin:promo:create:single")
    async def admin_promo_create_single(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        await state.update_data(promo_mode="single")
        await state.update_data(promo_action="create", editing_promo_id=None)
        await state.set_state(AdminStates.waiting_promo_code)
        await callback.message.answer("Send promo code text (for start link payload).")
        await callback.answer()

    @router.callback_query(F.data == "admin:promo:create:multi")
    async def admin_promo_create_multi(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        await state.update_data(promo_mode="multi")
        await state.update_data(promo_action="create", editing_promo_id=None)
        await state.set_state(AdminStates.waiting_promo_code)
        await callback.message.answer("Send promo code text (for start link payload).")
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:item:"))
    async def admin_promo_item_actions(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await ctx.repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return
        max_uses = promo["max_uses"]
        max_uses_text = "unlimited" if max_uses is None else str(max_uses)
        await callback.message.answer(
            "Promo code details:\n"
            f"Code: {promo['code']}\n"
            f"Credits: {promo['credits_amount']}\n"
            f"Uses: {promo['uses_count']}/{max_uses_text}\n"
            f"Active: {promo['is_active']}",
            reply_markup=build_promo_item_menu(promo_id),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:edit:"))
    async def admin_promo_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await ctx.repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return

        mode = "single" if promo["max_uses"] == 1 else "multi"
        await state.clear()
        await state.update_data(
            promo_mode=mode,
            promo_action="edit",
            editing_promo_id=promo_id,
        )
        await state.set_state(AdminStates.waiting_promo_code)
        await callback.message.answer(
            f"Editing promo '{promo['code']}'.\n"
            "Send new promo code text:"
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:delete:"))
    async def admin_promo_delete(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await ctx.repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return
        deleted = await ctx.repo.delete_promo_code(promo_id)
        if deleted:
            await callback.message.answer(f"Promo deleted: {promo['code']}")
        else:
            await callback.message.answer("Promo was not deleted.")
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:toggle_active:"))
    async def admin_promo_toggle_active(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await ctx.repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return
        new_active = not promo["is_active"]
        await ctx.repo.set_promo_active(promo_id, new_active)
        status_text = "activated" if new_active else "deactivated"
        await callback.message.answer(f"Promo {promo['code']} {status_text}.")
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:reset_uses:"))
    async def admin_promo_reset_uses(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await ctx.repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return
        await ctx.repo.reset_promo_uses(promo_id)
        await callback.message.answer(f"Promo {promo['code']} uses counter reset to 0.")
        await callback.answer()

    @router.message(AdminStates.waiting_promo_code)
    async def admin_promo_code_value(message: Message, state: FSMContext) -> None:
        code = (message.text or "").strip()
        if not code or len(code) < 3:
            await message.answer("Promo code is too short. Send at least 3 characters.")
            return
        await state.update_data(promo_code=code)
        await state.set_state(AdminStates.waiting_promo_credits)
        await message.answer("How many generation tokens should this promo grant?")

    @router.message(AdminStates.waiting_promo_credits)
    async def admin_promo_credits_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text.isdigit() or int(text) <= 0:
            await message.answer("Send a positive integer.")
            return
        credits = int(text)
        data = await state.get_data()
        mode = data.get("promo_mode")
        await state.update_data(promo_credits=credits)

        if mode == "single":
            await state.update_data(promo_max_uses=1)
            await finalize_promo_creation(message, state)
            return

        await state.set_state(AdminStates.waiting_promo_max_uses)
        await message.answer("How many users can redeem it? Send positive integer, or 0 for unlimited.")

    @router.message(AdminStates.waiting_promo_max_uses)
    async def admin_promo_max_uses_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text.isdigit() or int(text) < 0:
            await message.answer("Send 0 or a positive integer.")
            return
        max_uses = int(text)
        await state.update_data(promo_max_uses=(None if max_uses == 0 else max_uses))
        await finalize_promo_creation(message, state)

    async def finalize_promo_creation(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        user = await ctx.repo.get_user(message.from_user.id)
        try:
            promo_action = data.get("promo_action", "create")
            if promo_action == "edit" and data.get("editing_promo_id") is not None:
                await ctx.repo.update_promo_code(
                    promo_id=int(data["editing_promo_id"]),
                    code=str(data["promo_code"]),
                    credits_amount=int(data["promo_credits"]),
                    max_uses=data.get("promo_max_uses"),
                )
            else:
                await ctx.repo.create_promo_code(
                    code=str(data["promo_code"]),
                    credits_amount=int(data["promo_credits"]),
                    max_uses=data.get("promo_max_uses"),
                    created_by=user["tg_id"] if user else message.from_user.id,
                )
            me = await ctx.bot.get_me()
            header = "Promo code updated." if promo_action == "edit" else "Promo code created."
            if me.username:
                link = f"https://t.me/{me.username}?start={data['promo_code']}"
                await message.answer(
                    f"{header}\n"
                    f"Start link: {link}"
                )
            else:
                await message.answer(
                    f"{header}\n"
                    f"Use payload in /start: {data['promo_code']}"
                )
        except asyncpg.UniqueViolationError:
            await message.answer("Promo code already exists. Choose another code.")
        finally:
            await state.clear()
