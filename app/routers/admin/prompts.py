import json
import logging
from typing import Any

import asyncpg
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.keyboards.admin import (
    build_admin_prompt_tags_menu,
    build_prompt_list_menu,
    build_prompt_work_menu,
)
from app.prompt_utils import build_prompt_export_payload, variable_descriptions_from_features
from app.states import AdminStates
from app.utils import ensure_dict, extract_variables, normalize_feach_for_storage, variable_token
from app.routers.common import RouterCtx


def register_admin_prompts(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query((F.data == "admin:pw:list") | F.data.startswith("admin:pw:list:"))
    async def admin_prompt_list(callback: CallbackQuery) -> None:
        """
        List of prompts entry point: shows tag filter (All, Main Menu, other tags with pagination).
        """
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        data = (callback.data or "").strip()
        page = 0
        if data.startswith("admin:pw:list:"):
            try:
                page = int(data.split(":")[-1])
            except ValueError:
                page = 0
        tags, total = await ctx.repo.list_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
        try:
            await callback.message.edit_text(
                "Choose tag for prompt list:",
                reply_markup=build_admin_prompt_tags_menu(tags, page=page, total=total),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "Choose tag for prompt list:",
                reply_markup=build_admin_prompt_tags_menu(tags, page=page, total=total),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:pw:list_tag:"))
    async def admin_prompt_list_by_tag(callback: CallbackQuery) -> None:
        """
        List prompts filtered by tag from the admin "List of prompts" menu.
        Patterns:
        - admin:pw:list_tag:all:<page>  -> all prompts
        - admin:pw:list_tag:main:<page> -> prompts with Main Menu tag
        - admin:pw:list_tag:<tag_id>:<page> -> prompts with given tag
        """
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid", show_alert=True)
            return
        tag_key = parts[3]
        try:
            page = int(parts[4]) if len(parts) > 4 else 0
        except ValueError:
            page = 0

        if tag_key == "all":
            prompts, total = await ctx.repo.list_prompts_paginated(
                active_only=False, page=page, per_page=ctx.repo.PAGE_SIZE
            )
        elif tag_key == "main":
            # Use real Main Menu tag behind the scenes
            main_prompts = await ctx.repo.list_prompts_main_menu(active_only=False)
            # Simple pagination in Python, as this is rare and small
            total = len(main_prompts)
            start = max(0, page) * ctx.repo.PAGE_SIZE
            end = start + ctx.repo.PAGE_SIZE
            prompts = main_prompts[start:end]
        else:
            try:
                tag_id = int(tag_key)
            except ValueError:
                await callback.answer("Invalid tag", show_alert=True)
                return
            prompts, total = await ctx.repo.list_prompts_with_tag_paginated(
                tag_id, active_only=False, page=page, per_page=ctx.repo.PAGE_SIZE
            )

        if not total:
            await callback.answer("No prompts for this tag.", show_alert=True)
            return

        try:
            await callback.message.edit_text(
                "List of prompts:",
                reply_markup=build_prompt_list_menu(prompts, page=page, total=total),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "List of prompts:",
                reply_markup=build_prompt_list_menu(prompts, page=page, total=total),
            )
        await callback.answer()

    @router.callback_query(F.data == "admin:pw:add")
    async def admin_create_prompt_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        await state.update_data(admin_mode="create", editing_prompt_id=None)
        await callback.message.answer("Send prompt title:")
        await state.set_state(AdminStates.waiting_prompt_title)
        await callback.answer()

    @router.callback_query(F.data == "admin:gen:start")
    async def admin_gen_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        if not ctx.deepseek:
            await callback.answer("DeepSeek client not available.", show_alert=True)
            return
        await state.clear()
        await state.set_state(AdminStates.waiting_gen_title)
        await callback.message.answer("Enter prompt title (as shown in the bot):")
        await callback.answer()

    @router.message(AdminStates.waiting_gen_title)
    async def admin_gen_title(message: Message, state: FSMContext) -> None:
        title = (message.text or "").strip()
        if not title:
            await message.answer("Title cannot be empty. Enter title:")
            return
        await state.update_data(gen_title=title)
        await state.set_state(AdminStates.waiting_gen_idea)
        await message.answer("Enter the main idea for the image:")

    @router.message(AdminStates.waiting_gen_idea)
    async def admin_gen_idea(message: Message, state: FSMContext) -> None:
        idea = (message.text or "").strip()
        if not idea:
            await message.answer("Idea cannot be empty. Enter idea:")
            return
        data = await state.get_data()
        title = data.get("gen_title", "")
        if not title:
            await message.answer("Session expired. Start from «Generate new prompt» again.")
            await state.clear()
            return
        user = await ctx.repo.get_user(message.from_user.id)
        if not user or not ctx.deepseek:
            await message.answer("Error: unavailable.")
            await state.clear()
            return
        try:
            await message.answer("Calling bot…")
            api_feach = await ctx.deepseek.refine_idea(idea)
            normalized = normalize_feach_for_storage(api_feach)
            draft_template = normalized.get("idea") or idea
        except Exception as e:
            await message.answer(f"DeepSeek error: {e}")
            await state.clear()
            return
        try:
            await ctx.repo.insert_prompt(
                title=title,
                template=draft_template,
                variable_descriptions={},
                reference_photo_file_id=None,
                created_by=user["tg_id"],
                is_active=False,
                feach_data=normalized,
            )
        except asyncpg.UniqueViolationError:
            await message.answer("A prompt with this title already exists. Choose another title.")
            return
        await state.clear()
        await message.answer(
            "Draft prompt created. Open «List of prompts» to configure features and generate the final prompt.",
            reply_markup=build_prompt_work_menu(),
        )

    @router.callback_query(F.data == "admin:import")
    async def admin_import_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.update_data(import_target_prompt_id=None)
        await state.set_state(AdminStates.waiting_import_json)
        await callback.message.answer("Send a JSON file (exported prompt) to import. It will create or update a prompt by title.")
        await callback.answer()

    @router.message(AdminStates.waiting_import_json, F.document)
    async def admin_import_document(message: Message, state: FSMContext) -> None:
        if not message.document:
            return
        doc = message.document
        if not doc.file_name or not doc.file_name.endswith(".json"):
            await message.answer("Send a .json file.")
            return
        try:
            file = await ctx.bot.get_file(doc.file_id)
            buf = await ctx.bot.download_file(file.file_path)
            raw = buf.read().decode("utf-8") if hasattr(buf, "read") else buf.getvalue().decode("utf-8")
            payload = json.loads(raw)
        except Exception as e:
            await message.answer(f"Failed to read file: {e}")
            return
        title = (payload.get("title") or "").strip()
        if not title:
            await message.answer("JSON must contain 'title'.")
            await state.clear()
            return
        user = await ctx.repo.get_user(message.from_user.id)
        if not user:
            await message.answer("User not found.")
            await state.clear()
            return
        template = payload.get("template") or ""
        # New format: features (feach-like) → build variable_descriptions
        features = payload.get("features")
        if isinstance(features, dict):
            var_descriptions = variable_descriptions_from_features(template, features)
            ref_id = None
            feach_data = None
            example_ids = []
        else:
            # Legacy: variable_descriptions in payload (we ignore ref/feach/examples from file)
            var_descriptions = ensure_dict(payload.get("variable_descriptions") or {})
            ref_id = None
            feach_data = None
            example_ids = []

        data = await state.get_data()
        import_target_id = data.get("import_target_prompt_id")
        if import_target_id is not None:
            try:
                import_target_id = int(import_target_id)
            except (TypeError, ValueError):
                import_target_id = None

        if import_target_id is not None:
            target = await ctx.repo.get_prompt_by_id(import_target_id)
            if not target:
                await message.answer("Target prompt not found.")
                await state.clear()
                return
            is_admin = bool(user.get("is_admin"))
            is_owner = target.get("owner_tg_id") == message.from_user.id
            if not (is_admin or is_owner):
                await message.answer("No permission to update this prompt.")
                await state.clear()
                return
            keep_ref = target.get("reference_photo_file_id")
            try:
                await ctx.repo.update_prompt(import_target_id, title, template, var_descriptions, keep_ref)
                desc_from_file = (payload.get("description") or "").strip()
                idea_from_file = (payload.get("idea") or "").strip()
                if desc_from_file:
                    await ctx.repo.update_prompt_description(import_target_id, desc_from_file)
                elif idea_from_file:
                    await ctx.repo.update_prompt_description(import_target_id, idea_from_file)
                feach_existing = ensure_dict(target.get("feach_data") or {})
                if idea_from_file:
                    feach_existing["idea"] = idea_from_file
                    await ctx.repo.update_prompt_feach_data(import_target_id, feach_existing)
                await message.answer(f"Prompt «{title}» updated from JSON (id={import_target_id}).")
                refreshed = await ctx.repo.get_prompt_by_id(import_target_id)
                if refreshed:
                    await ctx.show_prompt_edit_actions(message, refreshed, is_admin_view=not is_owner)
            except Exception as e:
                await message.answer(f"Error: {e}")
            await state.clear()
            return

        if not user.get("is_admin"):
            await message.answer("Only admins can import without choosing a prompt in Edit → Import JSON.")
            await state.clear()
            return

        existing = await ctx.repo.get_prompt_by_title(title)
        try:
            if existing:
                # Keep existing reference when updating (import does not touch ref/feach/examples)
                keep_ref = existing.get("reference_photo_file_id") if ref_id is None else ref_id
                await ctx.repo.update_prompt(existing["id"], title, template, var_descriptions, keep_ref)
                desc_from_file = (payload.get("description") or "").strip()
                idea_from_file = (payload.get("idea") or "").strip()
                if desc_from_file:
                    await ctx.repo.update_prompt_description(existing["id"], desc_from_file)
                elif idea_from_file:
                    await ctx.repo.update_prompt_description(existing["id"], idea_from_file)
                feach_existing = ensure_dict(existing.get("feach_data") or {})
                if idea_from_file:
                    feach_existing["idea"] = idea_from_file
                    await ctx.repo.update_prompt_feach_data(existing["id"], feach_existing)
                await message.answer(f"Prompt «{title}» updated.")
            else:
                await ctx.repo.insert_prompt(
                    title, template, var_descriptions, ref_id, user["tg_id"],
                    is_active=True, feach_data=feach_data,
                )
                new_prompt = await ctx.repo.get_prompt_by_title(title)
                if new_prompt:
                    await ctx.repo.set_prompt_examples(new_prompt["id"], example_ids)
                    desc_from_file = (payload.get("description") or "").strip()
                    idea_from_file = (payload.get("idea") or "").strip()
                    if desc_from_file:
                        await ctx.repo.update_prompt_description(new_prompt["id"], desc_from_file)
                    elif idea_from_file:
                        await ctx.repo.update_prompt_description(new_prompt["id"], idea_from_file)
                await message.answer(f"Prompt «{title}» created.")
        except Exception as e:
            await message.answer(f"Error: {e}")
        await state.clear()

    @router.callback_query(F.data.startswith("admin:pw:users:"))
    async def admin_pw_users_list(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        data = (callback.data or "").strip()
        parts = data.split(":")
        page = int(parts[3]) if len(parts) > 3 else 0
        await callback.answer()
        await ctx.edit_to_admin_users_list(callback.message, page=page)

    @router.callback_query(F.data.startswith("admin:pw:user_prompts:"))
    async def admin_pw_user_prompts(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            user_id = int(parts[3])
            page = int(parts[4]) if len(parts) > 4 else 0
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        await callback.answer()
        await ctx.edit_to_user_prompts(callback.message, user_id, page=page, is_admin_view=True)

    @router.callback_query(F.data.startswith("admin:clone:"))
    async def admin_clone_prompt(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        prompt_id = int(parts[2])
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        
        new_title = f"{prompt['title']} copy"
        try:
            new_id = await ctx.repo.clone_prompt(prompt_id, new_title)
            await callback.answer(f"Cloned as '{new_title}'")
            # Open the new prompt
            new_prompt = await ctx.repo.get_prompt_by_id(new_id)
            if new_prompt:
                await ctx.show_prompt_edit_actions(callback.message, new_prompt)
        except Exception as e:
            await callback.answer(f"Clone failed: {e}", show_alert=True)

    @router.message(AdminStates.waiting_prompt_title)
    async def admin_prompt_title(message: Message, state: FSMContext) -> None:
        title = (message.text or "").strip()
        if not title:
            await message.answer("Title cannot be empty. Send prompt title:")
            return
        await state.update_data(prompt_title=title)
        await state.set_state(AdminStates.waiting_prompt_template)
        await message.answer(
            "Send prompt template.\n"
            "- Use [var] for image variables\n"
            "- Use <var> for text variables\n"
            "Example: Photorealistic astronauts on <planet_name> with [user_photo]."
        )

    @router.message(AdminStates.waiting_prompt_template)
    async def admin_prompt_template(message: Message, state: FSMContext) -> None:
        template = (message.text or "").strip()
        if not template:
            await message.answer("Template cannot be empty. Send prompt template:")
            return
        variables = extract_variables(template)
        await state.update_data(prompt_template=template)
        await state.update_data(
            prompt_variables=variables,
            var_desc_idx=0,
            variable_descriptions={},
        )
        if not variables:
            await state.set_state(AdminStates.waiting_prompt_reference)
            await message.answer(
                "No variables detected.\n"
                "Send optional reference image now, or type /skip to continue without it."
            )
            return
        await state.set_state(AdminStates.waiting_variable_description)
        await ctx.ask_admin_next_var_description(message, state)

    @router.message(AdminStates.waiting_variable_description, Command("skip"))
    async def admin_var_desc_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            await ctx.ask_admin_next_var_description(message, state)
            return

        var = variables[idx]
        token = variable_token(var)
        variable_descriptions = ensure_dict(data.get("variable_descriptions", {}))
        variable_descriptions[token] = {
            "description": "",
            "options": [],
            "allow_custom": True,
            "type": var["type"],
        }
        await state.update_data(variable_descriptions=variable_descriptions)
        if var["type"] == "text":
            await ctx.ask_admin_text_options(message, state)
            return

        await state.update_data(var_desc_idx=idx + 1)
        await ctx.ask_admin_next_var_description(message, state)

    @router.message(AdminStates.waiting_variable_description)
    async def admin_var_desc_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Description cannot be empty. Send description or /skip.")
            return
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            await ctx.ask_admin_next_var_description(message, state)
            return
        var = variables[idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        existing = ctx.get_variable_config(descriptions, token, var["type"])
        existing["description"] = text
        existing["type"] = var["type"]
        descriptions[token] = existing
        await state.update_data(variable_descriptions=descriptions)
        if var["type"] == "text":
            await ctx.ask_admin_text_options(message, state)
            return
        await state.update_data(var_desc_idx=idx + 1)
        await ctx.ask_admin_next_var_description(message, state)

    @router.message(AdminStates.waiting_text_options, Command("skip"))
    async def admin_text_options_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            await ctx.ask_admin_next_var_description(message, state)
            return
        var = variables[idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        existing = ctx.get_variable_config(descriptions, token, "text")
        existing["options"] = []
        existing["allow_custom"] = True
        descriptions[token] = existing
        await state.update_data(variable_descriptions=descriptions, var_desc_idx=idx + 1)
        await state.set_state(AdminStates.waiting_variable_description)
        await ctx.ask_admin_next_var_description(message, state)

    @router.message(AdminStates.waiting_text_options)
    async def admin_text_options_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Send options separated by ';' or /skip.")
            return
        options = [part.strip() for part in text.split(";") if part.strip()]
        if not options:
            await message.answer("No valid options found. Try again or /skip.")
            return

        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        var = variables[idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        existing = ctx.get_variable_config(descriptions, token, "text")
        existing["options"] = options
        descriptions[token] = existing
        await state.update_data(variable_descriptions=descriptions)
        await ctx.ask_admin_allow_custom(message, state)

    @router.callback_query(AdminStates.waiting_text_allow_custom, F.data.startswith("admin:allow_custom:"))
    async def admin_text_allow_custom_value(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        action = (callback.data or "").split(":")[-1]
        if action not in {"yes", "no"}:
            await callback.answer("Invalid choice", show_alert=True)
            return
        allow_custom = action == "yes"

        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        var = variables[idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        existing = ctx.get_variable_config(descriptions, token, "text")
        options = [str(x) for x in (existing.get("options") or []) if str(x).strip()]
        if not options:
            allow_custom = True
        existing["allow_custom"] = allow_custom
        descriptions[token] = existing
        await state.update_data(
            variable_descriptions=descriptions,
            var_desc_idx=idx + 1,
        )
        await callback.answer("Saved")
        await state.set_state(AdminStates.waiting_variable_description)
        await ctx.ask_admin_next_var_description(callback.message, state)

    @router.message(AdminStates.waiting_prompt_reference, Command("skip"))
    async def admin_prompt_skip_reference(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        user = await ctx.repo.get_user(message.from_user.id)
        prompt_id = data.get("editing_prompt_id")
        try:
            admin_mode = data.get("admin_mode", "create")
            if admin_mode == "edit_reference" and prompt_id is not None:
                await message.answer("Reference update cancelled.")
                prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
                if prompt:
                    await ctx.show_prompt_edit_actions(message, prompt)
            elif admin_mode == "edit" and prompt_id is not None:
                await ctx.repo.update_prompt(
                    prompt_id=int(prompt_id),
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                    reference_photo_file_id=None,
                )
                await message.answer("Prompt updated.")
            else:
                await ctx.repo.insert_prompt(
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                    reference_photo_file_id=None,
                    created_by=user["tg_id"] if user else message.from_user.id,
                )
                await message.answer("Prompt created.")
        except asyncpg.UniqueViolationError:
            await message.answer("Prompt with this title already exists.")
        finally:
            await state.clear()

    @router.message(AdminStates.waiting_prompt_reference, F.photo)
    async def admin_prompt_with_reference(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        user = await ctx.repo.get_user(message.from_user.id)
        prompt_id = data.get("editing_prompt_id")
        file_id = message.photo[-1].file_id
        try:
            admin_mode = data.get("admin_mode", "create")
            if admin_mode in {"edit", "edit_reference"} and prompt_id is not None:
                await ctx.repo.update_prompt(
                    prompt_id=int(prompt_id),
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                    reference_photo_file_id=file_id,
                )
                await message.answer("Prompt updated with reference image.")
                prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
                if prompt:
                    await ctx.show_prompt_edit_actions(message, prompt)
            else:
                await ctx.repo.insert_prompt(
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                    reference_photo_file_id=file_id,
                    created_by=user["tg_id"] if user else message.from_user.id,
                )
                await message.answer("Prompt created with reference image.")
        except asyncpg.UniqueViolationError:
            await message.answer("Prompt with this title already exists.")
        finally:
            await state.clear()

    @router.message(AdminStates.waiting_prompt_reference)
    async def admin_prompt_reference_invalid(message: Message) -> None:
        await message.answer("Send an image or /skip.")

    @router.message(AdminStates.waiting_prompt_examples, Command("done"))
    async def admin_prompt_examples_done(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        file_ids = data.get("example_file_ids") or []
        if not isinstance(file_ids, list):
            file_ids = []
        await state.clear()
        if prompt_id is None:
            await message.answer("Session expired. Open prompt edit again.")
            return
        await ctx.repo.set_prompt_examples(int(prompt_id), file_ids)
        prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
        await message.answer(f"Examples saved ({len(file_ids)}).")
        if prompt:
            await ctx.show_prompt_edit_actions(message, prompt)

    @router.message(AdminStates.waiting_prompt_examples, Command("skip"))
    async def admin_prompt_examples_skip(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        await state.clear()
        if prompt_id is not None:
            await ctx.repo.set_prompt_examples(int(prompt_id), [])
        await message.answer("Examples cleared.")
        if prompt_id is not None:
            prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
            if prompt:
                await ctx.show_prompt_edit_actions(message, prompt)

    @router.message(AdminStates.waiting_prompt_examples, F.photo)
    async def admin_prompt_examples_photo(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        if prompt_id is None:
            await state.clear()
            await message.answer("Session expired. Open prompt edit again.")
            return
        file_ids = list(data.get("example_file_ids") or [])
        if not isinstance(file_ids, list):
            file_ids = []
        if len(file_ids) >= 3:
            await message.answer("Already 3 examples. Send /done to save or /skip to clear.")
            return
        file_id = message.photo[-1].file_id
        file_ids.append(file_id)
        await state.update_data(example_file_ids=file_ids)
        if len(file_ids) >= 3:
            await ctx.repo.set_prompt_examples(int(prompt_id), file_ids)
            await state.clear()
            prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
            await message.answer("Saved 3 examples.")
            if prompt:
                await ctx.show_prompt_edit_actions(message, prompt)
        else:
            await message.answer(f"Added ({len(file_ids)}/3). Send another photo or /done.")

    @router.message(AdminStates.waiting_prompt_examples)
    async def admin_prompt_examples_invalid(message: Message) -> None:
        await message.answer("Send 1–3 photos, then /done, or /skip to clear examples.")
