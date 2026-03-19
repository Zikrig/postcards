"""User flow: prompt preview, generation start, variable collection."""
import json
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.keyboards.user import build_prompt_preview_menu
from app.states import GenerateStates
from app.utils import ensure_dict, extract_variables, variable_token
from app.routers.common import RouterCtx

logger = logging.getLogger(__name__)


def register_user_generation(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query(F.data.startswith("prompt:select:"))
    async def prompt_preview_callback(callback: CallbackQuery) -> None:
        """Показ превью: описание + иллюстрации, кнопки Generate (1 🪙) и Back."""
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
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        show_test = is_admin or is_owner
        desc = (prompt.get("description") or prompt.get("title") or "").strip() or str(prompt.get("title", ""))
        raw_examples = prompt.get("example_file_ids") or []
        if isinstance(raw_examples, str):
            try:
                raw_examples = json.loads(raw_examples) if raw_examples else []
            except json.JSONDecodeError:
                raw_examples = []
        if not isinstance(raw_examples, list):
            raw_examples = []
        example_ids = [str(f) for f in raw_examples[:3] if f]
        markup = build_prompt_preview_menu(prompt_id, back_callback="menu:main", show_test=show_test)
        if example_ids:
            try:
                await callback.message.answer_photo(
                    photo=example_ids[0],
                    caption=desc,
                    reply_markup=markup,
                )
            except TelegramBadRequest:
                # Если file_id битый, показываем только текст
                try:
                    await callback.message.edit_text(desc, reply_markup=markup)
                except Exception:
                    await callback.message.answer(desc, reply_markup=markup)
        else:
            try:
                await callback.message.edit_text(desc, reply_markup=markup)
            except Exception:
                await callback.message.answer(desc, reply_markup=markup)
        await callback.answer()

    @router.callback_query(F.data.startswith("prompt:generate:"))
    async def prompt_generate_start_callback(callback: CallbackQuery, state: FSMContext) -> None:
        """Старт генерации после превью: сбор переменных или сразу генерация."""
        if not callback.message:
            return
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        # prompt:generate:{quality}:{prompt_id}  or legacy prompt:generate:{prompt_id}
        quality_cost_map = {"1k": 1, "2k": 2, "4k": 4}
        if len(parts) >= 4 and parts[2].lower() in quality_cost_map:
            quality = parts[2].upper()
            cost = quality_cost_map[parts[2].lower()]
            raw_id = parts[3]
        else:
            quality = "1K"
            cost = 1
            raw_id = parts[-1]
        try:
            prompt_id = int(raw_id)
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
            generation_quality=quality,
            generation_cost=cost,
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
            await message.answer("Please use /start first.")
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
