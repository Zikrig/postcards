import logging
from typing import Any, Optional, List

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.admin import build_admin_menu, build_prompt_work_menu
from app.states import AdminStates
from app.routers.common import RouterCtx


def register_admin_panel(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query(F.data == "admin:prompt_work")
    async def admin_prompt_work_menu(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await callback.message.answer(
            "Prompts: generate from idea, list, or add manually.",
            reply_markup=build_prompt_work_menu(),
        )
        await callback.answer()

    @router.callback_query(F.data == "admin:pw:back")
    async def admin_prompt_work_back(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        await callback.message.answer("Admin panel:", reply_markup=build_admin_menu())
        await callback.answer()

    @router.callback_query(F.data == "admin:greeting")
    async def admin_greeting_menu(callback: CallbackQuery, state: FSMContext) -> None:
        """
        Entry point to edit bot greeting message.
        Admin can send a new greeting with text, images and/or voice.
        """
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        stored = await ctx.repo.get_greeting()
        text_preview = (stored or {}).get("text") or "<not set>"
        await callback.message.answer(
            "Greeting editor.\n\n"
            "Send a NEW greeting message.\n"
            "It may contain text, images and/or a voice message.\n\n"
            f"Current text preview:\n{text_preview}"
        )
        await state.set_state(AdminStates.waiting_greeting_message)
        await callback.answer()

    @router.message(AdminStates.waiting_greeting_message)
    async def admin_greeting_message_entered(message: Message, state: FSMContext, album: Optional[List[Message]] = None) -> None:
        """
        Save whatever admin sent (text + media file_ids) as the new greeting payload.
        """
        user = await ctx.repo.get_user(message.from_user.id)
        if not user or not user["is_admin"]:
            return

        text = (message.text or message.caption or "").strip()
        photos = [p.file_id for p in (message.photo or [])]
        voice_id = message.voice.file_id if message.voice else None
        doc_id = message.document.file_id if message.document else None

        # If it's an album, collect all photos
        if album:
            photos = []
            for msg in album:
                if msg.photo:
                    photos.append(msg.photo[-1].file_id)
                # Text should come from the first message in the album (common practice in TG)
                if not text and (msg.text or msg.caption):
                    text = (msg.text or msg.caption or "").strip()
                if not voice_id and msg.voice:
                    voice_id = msg.voice.file_id
                if not doc_id and msg.document:
                    doc_id = msg.document.file_id

        payload: dict[str, Any] = {
            "text": text,
            "photos": photos,
            "voice_id": voice_id,
            "document_id": doc_id,
        }
        await ctx.repo.set_greeting(payload)
        await state.clear()
        await message.answer("Greeting has been updated.")

    @router.callback_query(F.data == "admin:initial_tokens")
    async def admin_initial_tokens_menu(callback: CallbackQuery, state: FSMContext) -> None:
        """Entry point to set initial tokens for new users."""
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        current = await ctx.repo.get_initial_tokens()
        await callback.message.answer(
            f"Current initial tokens: {current}\n\n"
            "Enter NEW amount of tokens for new users:"
        )
        await state.set_state(AdminStates.waiting_initial_tokens)
        await callback.answer()

    @router.message(AdminStates.waiting_initial_tokens)
    async def admin_initial_tokens_entered(message: Message, state: FSMContext) -> None:
        """Save new initial tokens amount."""
        user = await ctx.repo.get_user(message.from_user.id)
        if not user or not user["is_admin"]:
            return
        try:
            amount = int((message.text or "").strip())
            if amount < 0:
                await message.answer("Amount must be non-negative.")
                return
        except ValueError:
            await message.answer("Please enter a valid number.")
            return

        await ctx.repo.set_initial_tokens(amount)
        await state.clear()
        await message.answer(f"Initial tokens set to: {amount}", reply_markup=build_admin_menu())
