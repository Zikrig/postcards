"""User flow: my prompts listing, prompt creation."""
import json
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.states import AdminStates
from app.utils import ensure_dict
from app.routers.common import RouterCtx

logger = logging.getLogger(__name__)


def register_user_my_prompts(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query(F.data.startswith("menu:my_prompts:"))
    async def my_prompts_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        page = int(parts[2]) if len(parts) > 2 else 0
        await callback.answer()
        await ctx.edit_to_user_prompts(callback.message, callback.from_user.id, page=page)

    @router.callback_query(F.data.startswith("menu:my_prompt_item:"))
    async def my_prompt_item_callback(callback: CallbackQuery) -> None:
        """Юзерское меню «My prompts»: открыть свой промпт с полным меню (редактирование и т.д.)."""
        if not callback.message:
            return
        logger.info("my_prompt_item_callback: data=%r, from_user_id=%s", callback.data, callback.from_user.id)
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        owner_tg_id = prompt.get("owner_tg_id")
        is_owner = owner_tg_id == callback.from_user.id
        is_admin = bool(user.get("is_admin"))
        
        logger.info(
            "my_prompt_item_callback: prompt_id=%s, owner_tg_id=%s, user_tg_id=%s, is_admin=%s",
            prompt_id,
            owner_tg_id,
            callback.from_user.id,
            is_admin,
        )
        if not is_owner and not is_admin:
            await callback.answer("Not your prompt", show_alert=True)
            return
        
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        template = str(prompt.get("template") or "")
        desc = await ctx.format_prompt_description(prompt)
        
        raw_examples = prompt.get("example_file_ids") or []
        # Нормализуем: может быть list или JSON-строка
        if isinstance(raw_examples, str):
            try:
                raw_examples = json.loads(raw_examples) if raw_examples else []
            except json.JSONDecodeError:
                raw_examples = []
        if not isinstance(raw_examples, list):
            raw_examples = []
        example_ids = [str(f) for f in raw_examples[:3] if f]
        markup = ctx.build_prompt_card_markup(prompt, callback.from_user.id, back_callback="menu:my_prompts:0")
        if example_ids:
            await callback.message.answer_photo(
                photo=example_ids[0],
                caption=desc,
                reply_markup=markup,
            )
        else:
            await callback.message.edit_text(desc, reply_markup=markup)
        await callback.answer()

    @router.callback_query(F.data == "menu:create_prompt")
    async def create_prompt_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        await callback.answer()
        await callback.message.answer("Enter a title for your new prompt:")
        from app.states import AdminStates
        await state.set_state(AdminStates.waiting_user_prompt_title)

    @router.message(AdminStates.waiting_user_prompt_title)
    async def user_prompt_title_handler(message: Message, state: FSMContext) -> None:
        user = await ctx.ensure_user(message)
        if not user["is_authorized"]:
            return
        title = (message.text or "").strip()
        if not title:
            return
        
        await state.update_data(user_prompt_title=title)
        await state.set_state(AdminStates.waiting_user_prompt_idea)
        await message.answer(f"Title: {title}\nNow enter the main idea for your image (2 🪙 will be charged):")

    @router.message(AdminStates.waiting_user_prompt_idea)
    async def user_prompt_idea_handler(message: Message, state: FSMContext) -> None:
        user = await ctx.ensure_user(message)
        if not user["is_authorized"]:
            return
        idea = (message.text or "").strip()
        if not idea:
            return
        
        data = await state.get_data()
        title = data.get("user_prompt_title")
        if not title:
            await message.answer("Session expired. Please start over.")
            await state.clear()
            return

        # Charge 2 tokens
        new_balance = await ctx.repo.consume_tokens(message.from_user.id, 2)
        if new_balance is None:
            balance = await ctx.repo.get_user_balance(message.from_user.id)
            await message.answer(f"Not enough balance to create a prompt (2 🪙 needed).\nYour balance: {balance}")
            await state.clear()
            return

        if not ctx.deepseek:
            await message.answer("Error: AI client unavailable.")
            await state.clear()
            return

        try:
            msg = await message.answer("Calling AI to refine your idea…")
            from app.utils import normalize_feach_for_storage
            api_feach = await ctx.deepseek.refine_idea(idea)
            normalized = normalize_feach_for_storage(api_feach)
            draft_template = normalized.get("idea") or idea
            
            # Create prompt
            prompt_id = await ctx.repo.insert_prompt(
                title=title,
                template=draft_template,
                variable_descriptions={},
                reference_photo_file_id=None,
                created_by=message.from_user.id,
                owner_tg_id=message.from_user.id,
                is_public=False,
                feach_data=normalized,
                is_active=False,
            )
            
            # Add 'Users' tag
            users_tag = await ctx.repo.get_tag_by_name("Users")
            if users_tag:
                await ctx.repo.set_prompt_tags(prompt_id, [users_tag["id"]])

            await msg.delete()
            # Сразу открываем карточку с фичами (как в My prompts),
            # чтобы юзер увидел параметры, Generate final и т.п.
            prompt = await ctx.repo.get_prompt_by_id(prompt_id)
            if prompt:
                feach_data = ensure_dict(prompt.get("feach_data") or {})
                template = str(prompt.get("template") or "")
                desc = await ctx.format_prompt_description(prompt)
                
                raw_examples = prompt.get("example_file_ids") or []
                if isinstance(raw_examples, str):
                    try:
                        raw_examples = json.loads(raw_examples) if raw_examples else []
                    except json.JSONDecodeError:
                        raw_examples = []
                if not isinstance(raw_examples, list):
                    raw_examples = []
                example_ids = [str(f) for f in raw_examples[:3] if f]
                markup = ctx.build_prompt_card_markup(prompt, message.from_user.id)
                await message.answer(
                    f"Prompt '{title}' created! 2 🪙 deducted (Balance: {new_balance})."
                )
                if example_ids:
                    await message.answer_photo(
                        photo=example_ids[0],
                        caption=desc,
                        reply_markup=markup,
                    )
                else:
                    await message.answer(desc, reply_markup=markup)
        except Exception as e:
            await message.answer(f"Error refining idea: {e}")
        finally:
            await state.clear()
