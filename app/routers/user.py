"""User flow: prompt selection and generation (variables, run_generation)."""
import logging

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards import build_prompt_feach_menu
from app.states import AdminStates, GenerateStates
from app.utils import ensure_dict, extract_variables, variable_token

from .common import RouterCtx

logger = logging.getLogger(__name__)


def register_user(router: Router, ctx: RouterCtx) -> None:
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
        if prompt.get("owner_tg_id") != callback.from_user.id:
            await callback.answer("Not your prompt", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        template = str(prompt.get("template") or "")
        idea = feach_data.get("idea", "") if feach_data else ""
        text = f"Prompt: {prompt['title']}"
        if idea:
            text = f"{text}\n\nIdea: {idea}"
        await callback.message.edit_text(
            text,
            reply_markup=build_prompt_feach_menu(
                prompt_id,
                feach_data or {},
                bool(prompt.get("is_active", True)),
                owner_tg_id=prompt.get("owner_tg_id"),
                is_public=prompt.get("is_public", False),
                is_admin_view=False,
                template=template,
            ),
        )
        await callback.answer()

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
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        if not prompt.get("is_public") or prompt.get("owner_tg_id") is None:
            await callback.answer("Not available", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        template = str(prompt.get("template") or "")
        idea = feach_data.get("idea", "") if feach_data else ""
        text = f"Prompt: {prompt['title']}"
        if idea:
            text = f"{text}\n\nIdea: {idea}"
        is_admin = bool(user.get("is_admin"))
        await callback.message.edit_text(
            text,
            reply_markup=build_prompt_feach_menu(
                prompt_id,
                feach_data or {},
                bool(prompt.get("is_active", True)),
                owner_tg_id=prompt.get("owner_tg_id"),
                is_public=prompt.get("is_public", False),
                is_admin_view=True,
                template=template,
                back_callback="menu:community_tags:0",
                show_clone=is_admin,
            ),
        )
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
                template=draft_template, # Using refined idea as draft template
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
            await message.answer(
                f"Prompt '{title}' created! 2 🪙 deducted (Balance: {new_balance}).\n"
                "Now you need to generate the final template in 'My prompts'."
            )
        except Exception as e:
            await message.answer(f"Error refining idea: {e}")
        finally:
            await state.clear()
            await ctx.show_user_prompts(message, message.from_user.id)

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
        # Tags menu only shows ADMIN categories and Community button
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

    @router.callback_query(F.data.startswith("prompt:select:"))
    async def prompt_pick_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start and enter password first.", show_alert=True)
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

        template = prompt["template"]
        variables = extract_variables(template)
        await state.clear()
        await state.set_state(GenerateStates.waiting_variable)
        await state.update_data(
            prompt_title=prompt["title"],
            template=template,
            variables=variables,
            current_idx=0,
            answers={},
            image_urls=[],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=prompt["reference_photo_file_id"],
            awaiting_custom_for=None,
            request_user_id=callback.from_user.id,
            request_username=callback.from_user.username or "",
            request_full_name=callback.from_user.full_name or "",
        )

        if prompt["reference_photo_file_id"]:
            try:
                ref_url = await ctx.telegram_file_url(prompt["reference_photo_file_id"])
                data = await state.get_data()
                image_urls = data.get("image_urls", [])
                image_urls.append(ref_url)
                await state.update_data(image_urls=image_urls)
            except Exception as e:
                await callback.message.answer(f"Warning: could not load reference image: {e}")

        await callback.answer()
        if not variables:
            await ctx.run_generation(callback.message, state)
            return
        await ctx.ask_next_variable(callback.message, state)

    @router.message(StateFilter(None))
    async def prompt_pick_handler(message: Message, state: FSMContext) -> None:
        user = await ctx.ensure_user(message)
        if not user["is_authorized"]:
            await message.answer("Please use /start and enter password first.")
            return
        await ctx.show_prompt_buttons(message)

    @router.callback_query(GenerateStates.waiting_variable, F.data.startswith("gen:opt:"))
    async def generate_option_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("variables", [])
        current_idx: int = data.get("current_idx", 0)
        if current_idx >= len(variables):
            await callback.answer()
            await ctx.run_generation(callback.message, state)
            return

        variable = variables[current_idx]
        if variable["type"] != "text":
            await callback.answer("This variable expects image.", show_alert=True)
            return

        token = variable_token(variable)
        variable_descriptions = ensure_dict(data.get("variable_descriptions", {}))
        config = ctx.get_variable_config(variable_descriptions, token, "text")
        options: list[str] = [str(x) for x in (config.get("options") or []) if str(x).strip()]

        try:
            option_idx = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid option", show_alert=True)
            return
        if option_idx < 0 or option_idx >= len(options):
            await callback.answer("Invalid option", show_alert=True)
            return

        answers: dict[str, str] = data.get("answers", {})
        answers[variable["name"]] = options[option_idx]
        await state.update_data(
            answers=answers,
            current_idx=current_idx + 1,
            awaiting_custom_for=None,
        )
        await callback.answer("Selected")
        await ctx.ask_next_variable(callback.message, state)

    @router.callback_query(GenerateStates.waiting_variable, F.data == "gen:myown")
    async def generate_myown_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("variables", [])
        current_idx: int = data.get("current_idx", 0)
        if current_idx >= len(variables):
            await callback.answer()
            return

        variable = variables[current_idx]
        if variable["type"] != "text":
            await callback.answer("Invalid action", show_alert=True)
            return

        token = variable_token(variable)
        variable_descriptions = ensure_dict(data.get("variable_descriptions", {}))
        config = ctx.get_variable_config(variable_descriptions, token, "text")
        allow_custom = bool(config.get("allow_custom", True))
        if not allow_custom:
            await callback.answer("Custom input is disabled.", show_alert=True)
            return

        await state.update_data(awaiting_custom_for=token)
        await callback.answer()
        await callback.message.answer("Please type your own value.")

    @router.message(GenerateStates.waiting_variable)
    async def collect_variable_value(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("variables", [])
        current_idx: int = data.get("current_idx", 0)

        if current_idx >= len(variables):
            await ctx.run_generation(message, state)
            return

        variable = variables[current_idx]
        var_name = variable["name"]
        var_type = variable["type"]
        answers: dict[str, str] = data.get("answers", {})
        image_urls: list[str] = data.get("image_urls", [])
        token = variable_token(variable)
        variable_descriptions = ensure_dict(data.get("variable_descriptions", {}))
        config = ctx.get_variable_config(variable_descriptions, token, var_type)
        options: list[str] = [str(x) for x in (config.get("options") or []) if str(x).strip()]
        allow_custom = bool(config.get("allow_custom", True))
        awaiting_custom_for = data.get("awaiting_custom_for")

        if var_type == "image":
            if not message.photo:
                await message.answer("Please send a photo.")
                return
            file_id = message.photo[-1].file_id
            file_url = await ctx.telegram_file_url(file_id)
            image_urls.append(file_url)
            answers[var_name] = "provided reference image"
        else:
            if options:
                if awaiting_custom_for == token:
                    value = (message.text or "").strip()
                    if not value:
                        await message.answer("Please send text.")
                        return
                    answers[var_name] = value
                    await state.update_data(awaiting_custom_for=None)
                else:
                    await message.answer(
                        "Please choose one of the options using inline buttons.",
                        reply_markup=ctx.build_text_options_keyboard(options, allow_custom),
                    )
                    return
            else:
                value = (message.text or "").strip()
                if not value:
                    await message.answer("Please send text.")
                    return
                answers[var_name] = value

        await state.update_data(
            answers=answers,
            image_urls=image_urls,
            current_idx=current_idx + 1,
            awaiting_custom_for=None,
        )
        await ctx.ask_next_variable(message, state)
