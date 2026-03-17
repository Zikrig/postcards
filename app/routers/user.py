"""User flow: prompt selection and generation (variables, run_generation)."""
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.states import GenerateStates
from app.utils import ensure_dict, extract_variables, variable_token

from .common import RouterCtx


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

    @router.callback_query(F.data.startswith("menu:tags"))
    async def menu_tags_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
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
