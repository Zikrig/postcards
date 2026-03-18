import json
import logging
from typing import Any
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from app.states import AdminStates
from app.utils import ensure_dict, extract_variables, variable_token
from app.routers.common import RouterCtx


def register_shared_variables(router: Router, ctx: RouterCtx) -> None:

    @router.callback_query(F.data.startswith("admin:editpart:variables:"))
    async def admin_edit_prompt_variables_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user:
            await callback.answer("Access denied", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("No permission", show_alert=True)
            return
        template = prompt["template"]
        variables = extract_variables(template)
        descriptions = ctx.normalize_variable_descriptions_for_template(
            prompt.get("variable_descriptions") or {},
            variables,
        )
        await state.clear()
        await state.update_data(
            editing_prompt_id=prompt_id,
            prompt_title=prompt["title"],
            prompt_template=template,
            prompt_variables=variables,
            variable_descriptions=descriptions,
            reference_photo_file_id=prompt["reference_photo_file_id"],
            editing_as_owner=is_owner,
        )
        await state.set_state(None)
        await ctx.show_variable_pick_menu(callback.message, state)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:add:"))
    async def admin_add_variable_start(callback: CallbackQuery, state: FSMContext) -> None:
        """
        Start flow to add a new variable for a prompt.
        Новая переменная:
        - появляется в feach_data["features"] (список параметров в карточке промпта)
        - синхронизируется с шаблоном (добавляется токен в template)
        """
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user:
            await callback.answer("Access denied", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("No permission", show_alert=True)
            return
        await state.update_data(editing_prompt_id=prompt_id)
        await state.set_state(AdminStates.waiting_new_variable_name)
        await callback.message.answer(
            "Enter new variable name (e.g. CHARACTER_POSITION). "
            "Use only letters, numbers and underscores."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:pick:"))
    async def admin_edit_variable_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user:
            await callback.answer("Access denied", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 6:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        try:
            prompt_id = int(parts[4])
            var_idx = int(parts[5])
        except ValueError:
            await callback.answer("Invalid variable action", show_alert=True)
            return

        data = await state.get_data()
        if data.get("editing_prompt_id") != prompt_id:
            prompt = await ctx.repo.get_prompt_by_id(prompt_id)
            if not prompt:
                await callback.answer("Prompt not found", show_alert=True)
                return
            template = prompt["template"]
            variables = extract_variables(template)
            descriptions = ctx.normalize_variable_descriptions_for_template(
                prompt.get("variable_descriptions") or {},
                variables,
            )
            is_owner = prompt.get("owner_tg_id") == callback.from_user.id
            await state.clear()
            await state.update_data(
                editing_prompt_id=prompt_id,
                prompt_title=prompt["title"],
                prompt_template=template,
                prompt_variables=variables,
                variable_descriptions=descriptions,
                reference_photo_file_id=prompt["reference_photo_file_id"],
                editing_as_owner=is_owner,
            )
            await state.set_state(None)

        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("No permission", show_alert=True)
            return
        await ctx.show_variable_actions_menu(callback.message, state, var_idx)
        await callback.answer()

    @router.message(AdminStates.waiting_new_variable_name)
    async def admin_new_variable_name_entered(message: Message, state: FSMContext) -> None:
        user = await ctx.repo.get_user(message.from_user.id)
        if not user:
            return
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        if prompt_id is None:
            await state.clear()
            await message.answer("Prompt edit session expired. Open edit menu again.")
            return
        name = (message.text or "").strip().upper()
        if not name or any(ch for ch in name if not (ch.isalnum() or ch == "_")):
            await message.answer("Invalid name. Use only letters, numbers and underscores. Try again:")
            return
        # Проверяем, что такой фичи/переменной ещё нет
        prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
        if not prompt:
            await state.clear()
            await message.answer("Prompt not found.")
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if name in features:
            await message.answer("Variable with this name already exists. Enter another name:")
            return
        await state.update_data(new_variable_name=name)
        await state.set_state(AdminStates.waiting_new_variable_type)
        # Предлагаем выбрать тип переменной инлайн-кнопками
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Text", callback_data="admin:newvar:type:text"),
                    InlineKeyboardButton(text="Image", callback_data="admin:newvar:type:image"),
                ]
            ]
        )
        await message.answer("Choose variable type:", reply_markup=kb)

    @router.callback_query(AdminStates.waiting_new_variable_type, F.data.startswith("admin:newvar:type:"))
    async def admin_new_variable_type_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user:
            await callback.answer("Access denied", show_alert=True)
            return
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        name = data.get("new_variable_name")
        if prompt_id is None or not name:
            await state.clear()
            await callback.message.answer("Prompt edit session expired. Open edit menu again.")
            await callback.answer()
            return
        vtype_raw = (callback.data or "").split(":")[-1].lower()
        if vtype_raw not in {"text", "image"}:
            await callback.answer("Invalid type", show_alert=True)
            return
        vtype = vtype_raw
        prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
        if not prompt:
            await state.clear()
            await callback.message.answer("Prompt not found.")
            await callback.answer()
            return

        # 1) Обновляем feach_data.features (то, что видно в карточке промпта).
        #    Шаблон (template) НЕ трогаем — токен владелец добавляет вручную
        #    через "Change template", чтобы не появлялись скрытые переменные.
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        features[name] = {
            "varname": name,
            "type": vtype,
            "options": [],
            "custom": [],
            "my_own": True,
        }
        feach_data["features"] = features
        await ctx.repo.update_prompt_feach_data(int(prompt_id), feach_data)

        await state.clear()

        # 3) Перерисовываем карточку промпта в том же стиле, что и при обычном открытии
        updated = await ctx.repo.get_prompt_by_id(int(prompt_id))
        if not updated:
            await callback.message.answer("Variable added, but prompt reload failed.")
            await callback.answer()
            return
        feach_data = ensure_dict(updated.get("feach_data") or {})
        is_active = bool(updated.get("is_active", True))
        desc = (updated.get("description") or updated.get("title") or "").strip() or updated["title"]

        raw_examples = updated.get("example_file_ids") or []
        if isinstance(raw_examples, str):
            try:
                raw_examples = json.loads(raw_examples) if raw_examples else []
            except json.JSONDecodeError:
                raw_examples = []
        if not isinstance(raw_examples, list):
            raw_examples = []
        example_ids = [str(f) for f in raw_examples[:3] if f]

        markup = ctx.build_prompt_card_markup(updated, callback.from_user.id)

        try:
            if example_ids:
                await callback.message.answer_photo(
                    photo=example_ids[0],
                    caption=desc,
                    reply_markup=markup,
                )
            else:
                await callback.message.edit_text(desc, reply_markup=markup)
        except TelegramBadRequest:
            await callback.message.answer(desc, reply_markup=markup)

        await callback.answer("Variable added")

    @router.callback_query(F.data.startswith("admin:editvar:field:name:"))
    async def admin_edit_variable_name_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user:
            await callback.answer("Access denied", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 7:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        try:
            var_idx = int(parts[6])
        except ValueError:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        prompt = await ctx.repo.get_prompt_by_id(prompt_id) if prompt_id is not None else None
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("No permission", show_alert=True)
            return
        await state.update_data(edit_var_idx=var_idx)
        await state.set_state(AdminStates.waiting_prompt_edit_variable_name)
        await callback.message.answer(
            "Send new variable name only (without [] or <>).\n"
            "Example: USER_PHOTO"
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:field:desc:"))
    async def admin_edit_variable_description_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user:
            await callback.answer("Access denied", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 7:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        try:
            var_idx = int(parts[6])
        except ValueError:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        prompt = await ctx.repo.get_prompt_by_id(prompt_id) if prompt_id is not None else None
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("No permission", show_alert=True)
            return
        await state.update_data(edit_var_idx=var_idx)
        await state.set_state(AdminStates.waiting_prompt_edit_variable_description)
        await callback.message.answer(
            "Send new user-facing description.\n"
            "Use /skip to clear description."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:field:opts:"))
    async def admin_edit_variable_options_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user:
            await callback.answer("Access denied", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 7:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        try:
            var_idx = int(parts[6])
        except ValueError:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        prompt = await ctx.repo.get_prompt_by_id(prompt_id) if prompt_id is not None else None
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("No permission", show_alert=True)
            return
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if var_idx < 0 or var_idx >= len(variables):
            await callback.answer("Variable not found", show_alert=True)
            return
        if variables[var_idx]["type"] != "text":
            await callback.answer("Options are available only for text variables.", show_alert=True)
            return
        await state.update_data(edit_var_idx=var_idx)
        await state.set_state(AdminStates.waiting_prompt_edit_variable_options)
        await callback.message.answer(
            "Send options separated by ';'.\n"
            "Use /skip to clear options."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:allow:"))
    async def admin_edit_variable_allow_custom(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user:
            await callback.answer("Access denied", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 7:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        try:
            var_idx = int(parts[5])
        except ValueError:
            await callback.answer("Invalid variable action", show_alert=True)
            return
        allow_custom = parts[6] == "yes"
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        prompt = await ctx.repo.get_prompt_by_id(prompt_id) if prompt_id is not None else None
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("No permission", show_alert=True)
            return
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if var_idx < 0 or var_idx >= len(variables):
            await callback.answer("Variable not found", show_alert=True)
            return
        var = variables[var_idx]
        if var["type"] != "text":
            await callback.answer("My own is available only for text variables.", show_alert=True)
            return
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = ctx.get_variable_config(descriptions, token, "text")
        cfg["allow_custom"] = allow_custom
        descriptions[token] = cfg
        await state.update_data(variable_descriptions=descriptions)
        await ctx.persist_prompt_edit_state(state)
        await ctx.show_variable_actions_menu(callback.message, state, var_idx)
        await callback.answer("Saved")

    @router.message(AdminStates.waiting_prompt_edit_variable_name)
    async def admin_edit_variable_name_value(message: Message, state: FSMContext) -> None:
        new_name = (message.text or "").strip()
        if not new_name:
            await message.answer("Variable name cannot be empty. Send new name:")
            return
        if any(ch in new_name for ch in "[]<>"):
            await message.answer("Send name without brackets [] or <>.")
            return

        data = await state.get_data()
        var_idx = data.get("edit_var_idx")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if not isinstance(var_idx, int) or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable edit session expired. Open variable list again.")
            await state.set_state(None)
            return

        old_var = variables[var_idx]
        if any(
            i != var_idx and v["type"] == old_var["type"] and v["name"] == new_name
            for i, v in enumerate(variables)
        ):
            await message.answer("Variable with this name already exists for this type.")
            return

        old_token = variable_token(old_var)
        new_var = {"name": new_name, "type": old_var["type"]}
        new_token = variable_token(new_var)
        template = str(data.get("prompt_template") or "").replace(old_token, new_token)
        variables_updated = extract_variables(template)

        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        old_cfg = ctx.get_variable_config(descriptions, old_token, old_var["type"])
        descriptions.pop(old_token, None)
        descriptions[new_token] = old_cfg
        descriptions = ctx.normalize_variable_descriptions_for_template(descriptions, variables_updated)

        await state.update_data(
            prompt_template=template,
            prompt_variables=variables_updated,
            variable_descriptions=descriptions,
        )
        await ctx.persist_prompt_edit_state(state)
        await state.set_state(None)
        await message.answer("Variable renamed.")
        new_idx = next(
            (i for i, v in enumerate(variables_updated) if variable_token(v) == new_token),
            0,
        )
        await ctx.show_variable_actions_menu(message, state, new_idx)

    @router.message(AdminStates.waiting_prompt_edit_variable_description, Command("skip"))
    async def admin_edit_variable_description_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        var_idx = data.get("edit_var_idx")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if not isinstance(var_idx, int) or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable edit session expired. Open variable list again.")
            await state.set_state(None)
            return
        var = variables[var_idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = ctx.get_variable_config(descriptions, token, var["type"])
        cfg["description"] = ""
        descriptions[token] = cfg
        await state.update_data(variable_descriptions=descriptions)
        await ctx.persist_prompt_edit_state(state)
        await state.set_state(None)
        await message.answer("Description cleared.")
        await ctx.show_variable_actions_menu(message, state, var_idx)

    @router.message(AdminStates.waiting_prompt_edit_variable_description)
    async def admin_edit_variable_description_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Description cannot be empty. Send text or /skip.")
            return
        data = await state.get_data()
        var_idx = data.get("edit_var_idx")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if not isinstance(var_idx, int) or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable edit session expired. Open variable list again.")
            await state.set_state(None)
            return
        var = variables[var_idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = ctx.get_variable_config(descriptions, token, var["type"])
        cfg["description"] = text
        descriptions[token] = cfg
        await state.update_data(variable_descriptions=descriptions)
        await ctx.persist_prompt_edit_state(state)
        await state.set_state(None)
        await message.answer("Description updated.")
        await ctx.show_variable_actions_menu(message, state, var_idx)

    @router.message(AdminStates.waiting_prompt_edit_variable_options, Command("skip"))
    async def admin_edit_variable_options_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        var_idx = data.get("edit_var_idx")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if not isinstance(var_idx, int) or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable edit session expired. Open variable list again.")
            await state.set_state(None)
            return
        var = variables[var_idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = ctx.get_variable_config(descriptions, token, "text")
        cfg["options"] = []
        cfg["allow_custom"] = True
        descriptions[token] = cfg
        await state.update_data(variable_descriptions=descriptions)
        await ctx.persist_prompt_edit_state(state)
        await state.set_state(None)
        await message.answer("Options cleared. My own enabled.")
        await ctx.show_variable_actions_menu(message, state, var_idx)

    @router.message(AdminStates.waiting_prompt_edit_variable_options)
    async def admin_edit_variable_options_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        options = [part.strip() for part in text.split(";") if part.strip()]
        if not options:
            await message.answer("No valid options found. Send options or /skip to clear.")
            return
        data = await state.get_data()
        var_idx = data.get("edit_var_idx")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if not isinstance(var_idx, int) or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable edit session expired. Open variable list again.")
            await state.set_state(None)
            return
        var = variables[var_idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = ctx.get_variable_config(descriptions, token, "text")
        cfg["options"] = options
        descriptions[token] = cfg
        await state.update_data(variable_descriptions=descriptions)
        await ctx.persist_prompt_edit_state(state)
        await state.set_state(None)
        await message.answer("Options updated.")
        await ctx.show_variable_actions_menu(message, state, var_idx)
