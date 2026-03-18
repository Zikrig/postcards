"""Auth and entry handlers: start, password, admin, addme."""
from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.keyboards import build_admin_menu

from .common import RouterCtx


def register_auth(router: Router, ctx: RouterCtx) -> None:
    @router.message(CommandStart())
    async def start_handler(message: Message, state: FSMContext) -> None:
        user = await ctx.ensure_user(message)
        await state.clear()
        payload = ctx.extract_start_payload(message.text or "")

        promo_block = ""
        if payload:
            success, promo_message, granted = await ctx.repo.redeem_promo_code(payload, user["tg_id"])
            if success:
                new_balance = await ctx.repo.get_user_balance(user["tg_id"])
                promo_block = (
                    f"{promo_message}\n"
                    f"Granted: {granted}\n"
                    f"Your balance: {new_balance}\n\n"
                )
            else:
                await message.answer(promo_message)

        # Автоматически авторизуем пользователя при старте (пароль больше не требуется)
        if not user["is_authorized"]:
            await ctx.repo.set_user_authorized(user["tg_id"], True)

        balance = await ctx.repo.get_user_balance(user["tg_id"])
        greeting = await ctx.repo.get_greeting()

        if greeting:
            text = (greeting.get("text") or "").strip()
            if promo_block:
                text = f"{promo_block}\n{text}"
            
            # Append balance if not present in the custom greeting
            if "balance" not in text.lower():
                text += f"\n\nYour balance: {balance}"
            
            photos = greeting.get("photos")
            voice_id = greeting.get("voice_id")
            document_id = greeting.get("document_id")

            if photos:
                if len(photos) > 1:
                    from aiogram.types import InputMediaPhoto
                    media = [InputMediaPhoto(media=p) for p in photos]
                    # The caption should go to the first photo in the media group
                    media[0].caption = text
                    await message.answer_media_group(media=media)
                else:
                    await message.answer_photo(photo=photos[0], caption=text)
            elif voice_id:
                await message.answer_voice(voice=voice_id, caption=text)
            elif document_id:
                await message.answer_document(document=document_id, caption=text)
            else:
                await message.answer(text)
        else:
            await message.answer(
                f"{promo_block}"
                "Welcome!\n"
                "Choose one of the prompt buttons below.\n"
                "For each prompt, I will ask for required values and then generate an image.\n"
                f"Your balance: {balance}"
            )
        await ctx.show_prompt_buttons(message)
        return

    @router.message(Command("admin"))
    async def admin_handler(message: Message) -> None:
        user = await ctx.ensure_user(message)
        if not user["is_admin"]:
            await message.answer("Admin only.")
            return
        await message.answer("Admin panel:", reply_markup=build_admin_menu())

    @router.message(Command("addme"))
    async def addme_handler(message: Message) -> None:
        user = await ctx.ensure_user(message)
        if not user or not user["is_admin"]:
            await message.answer("Admin only.")
            return
        payload = (message.text or "").strip().split(maxsplit=2)
        if len(payload) < 3:
            await message.answer(
                "Usage: /addme <user_id_or_@username> <amount>\n"
                "Example: /addme 184374602 10 or /addme @username -5"
            )
            return
        target_str = payload[1].strip()
        amount_str = payload[2].strip()
        try:
            amount = int(amount_str)
        except ValueError:
            await message.answer("Amount must be an integer (can be negative).")
            return
        if target_str.startswith("@"):
            target_user = await ctx.repo.get_user_by_username(target_str)
        else:
            try:
                tg_id = int(target_str)
                target_user = await ctx.repo.get_user(tg_id)
            except ValueError:
                target_user = await ctx.repo.get_user_by_username(target_str)
        if not target_user:
            await message.answer("User not found.")
            return
        tg_id = int(target_user["tg_id"])
        new_balance = await ctx.repo.add_user_balance(tg_id, amount)
        await message.answer(
            f"Balance updated: {target_user.get('username') or tg_id} now has {new_balance} tokens (delta: {amount:+d})."
        )

    @router.message(Command("del"))
    async def del_user_handler(message: Message) -> None:
        user = await ctx.ensure_user(message)
        if not user or not user["is_admin"]:
            await message.answer("Admin only.")
            return
        payload = (message.text or "").strip().split(maxsplit=1)
        if len(payload) < 2:
            await message.answer(
                "Usage: /del <user_id_or_@username>\n"
                "Example: /del 184374602 or /del @username"
            )
            return
        target_str = payload[1].strip()
        if target_str.startswith("@"):
            target_user = await ctx.repo.get_user_by_username(target_str)
        else:
            try:
                tg_id = int(target_str)
                target_user = await ctx.repo.get_user(tg_id)
            except ValueError:
                target_user = await ctx.repo.get_user_by_username(target_str)
        
        if not target_user:
            await message.answer("User not found.")
            return
        
        tg_id = int(target_user["tg_id"])
        if tg_id == message.from_user.id:
            await message.answer("You cannot delete yourself.")
            return
            
        success = await ctx.repo.delete_user(tg_id)
        if success:
            username = target_user.get('username') or tg_id
            await message.answer(f"User {username} has been deleted from the database. Their prompts remain intact.")
        else:
            await message.answer("Failed to delete user.")

    @router.message(StateFilter(None), F.text.casefold() == "admin")
    async def admin_text_handler(message: Message) -> None:
        user = await ctx.ensure_user(message)
        if not user["is_admin"]:
            await message.answer("Admin only.")
            return
        await message.answer("Admin panel:", reply_markup=build_admin_menu())
