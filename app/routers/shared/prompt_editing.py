import json
import logging
import asyncpg
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from app.keyboards.admin import build_prompt_edit_images_menu
from app.states import AdminStates, PrimaryPromptOnboardingStates
from app.utils import ensure_dict, extract_variables
from app.routers.common import RouterCtx

logger = logging.getLogger(__name__)


def register_shared_editing(router: Router, ctx: RouterCtx) -> None:

    @router.callback_query(F.data.startswith("admin:edit:"))
    async def admin_edit_prompt_pick(callback: CallbackQuery, state: FSMContext) -> None:
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

        await state.clear()
        # Автовыбор флоу:
        # - владелец промпта (user или admin) → юзерский Back (My prompts)
        # - админ, редактирующий системный или чужой промпт → админский Back (admin list)
        is_admin_view = not is_owner
        await ctx.show_prompt_edit_actions(callback.message, prompt, is_admin_view=is_admin_view)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:genmenu:"))
    async def admin_prompt_generation_menu(callback: CallbackQuery, state: FSMContext) -> None:
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

        if await state.get_state() == PrimaryPromptOnboardingStates.reviewing_variables:
            await state.clear()
            logger.info("admin_prompt_generation_menu: cleared primary onboarding FSM")

        logger.info("admin_prompt_generation_menu: prompt_id=%s viewer=%s", prompt_id, callback.from_user.id)
        await ctx.send_prompt_generation_menu(callback.message, prompt_id, callback.from_user.id)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:images:"))
    async def admin_edit_prompt_images_menu(callback: CallbackQuery) -> None:
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
        ref_id = prompt.get("reference_photo_file_id")
        example_ids = ctx.normalize_example_file_ids(prompt.get("example_file_ids"))
        ref_line = "set" if ref_id else "not set"
        ex_line = f"{len(example_ids)} photo(s)" if example_ids else "none"
        await callback.message.answer(
            "🖼 Images & examples\n"
            f"Reference image: {ref_line}\n"
            f"Example images: {ex_line}\n"
            "Choose an action:",
            reply_markup=build_prompt_edit_images_menu(prompt_id),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:import_json:"))
    async def admin_edit_prompt_import_json_start(callback: CallbackQuery, state: FSMContext) -> None:
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
        await state.set_state(AdminStates.waiting_import_json)
        await state.update_data(import_target_prompt_id=prompt_id)
        await callback.message.answer(
            "Send a .json file (exported prompt). "
            "This prompt will be updated from the file (title, template, features / variable_descriptions). "
            "Reference photo and example images are kept as they are now."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:description:"))
    async def admin_edit_prompt_description_start(callback: CallbackQuery, state: FSMContext) -> None:
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
        await state.clear()
        await state.update_data(editing_prompt_id=prompt_id)
        await state.set_state(AdminStates.waiting_prompt_edit_description)
        current = ctx.editable_prompt_description_preview(prompt)
        preview = current.strip() or "(empty)"
        if len(preview) > 3500:
            preview = preview[:3500] + "…"
        await callback.message.answer(
            f"📝 Current description:\n{preview}\n\nSend new description:"
        )
        await callback.answer()

    @router.message(AdminStates.waiting_prompt_edit_description)
    async def admin_edit_prompt_description_entered(message: Message, state: FSMContext) -> None:
        if not message.from_user:
            return
        user = await ctx.repo.get_user(message.from_user.id)
        if not user:
            await state.clear()
            return
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        if prompt_id is None:
            await message.answer("Session expired.")
            await state.clear()
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await message.answer("Prompt not found.")
            await state.clear()
            return
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == message.from_user.id
        if not (is_admin or is_owner):
            await message.answer("No permission.")
            await state.clear()
            return
        new_desc = (message.text or "").strip()
        await ctx.repo.update_prompt_description(prompt_id, new_desc or prompt.get("title") or "")
        await state.clear()
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if prompt:
            await ctx.show_prompt_edit_actions(message, prompt)
        await message.answer("📝 Description updated.")

    @router.callback_query(F.data.startswith("admin:editpart:title:"))
    async def admin_edit_prompt_title_start(callback: CallbackQuery, state: FSMContext) -> None:
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
        await state.clear()
        await state.update_data(
            editing_prompt_id=prompt_id,
            prompt_title=prompt["title"],
            prompt_template=prompt["template"],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=prompt["reference_photo_file_id"],
        )
        await state.set_state(AdminStates.waiting_prompt_edit_title)
        await callback.message.answer(
            f"Current title: {prompt['title']}\n"
            "Send new title:"
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:template:"))
    async def admin_edit_prompt_template_start(callback: CallbackQuery, state: FSMContext) -> None:
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
        await state.clear()
        await state.update_data(
            editing_prompt_id=prompt_id,
            prompt_title=prompt["title"],
            prompt_template=prompt["template"],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=prompt["reference_photo_file_id"],
        )
        await state.set_state(AdminStates.waiting_prompt_edit_template)
        # Telegram имеет ограничение на длину сообщения, поэтому если шаблон очень большой,
        # покажем только начало.
        current_template = str(prompt.get("template") or "")
        max_chars = 3500
        if len(current_template) > max_chars:
            current_template = current_template[:max_chars] + "\n... (truncated)"
        await callback.message.answer(
            f"Current template:\n{current_template}\n\n"
            "Send new template.\n"
            "- Use [var] for image variables\n"
            "- Use <var> for text variables"
        )
        await callback.answer()

    @router.message(AdminStates.waiting_prompt_edit_title)
    async def admin_prompt_edit_title_value(message: Message, state: FSMContext) -> None:
        title = (message.text or "").strip()
        if not title:
            await message.answer("Title cannot be empty. Send new title:")
            return
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        if prompt_id is None:
            await message.answer("Prompt edit session expired. Open edit menu again.")
            await state.clear()
            return
        try:
            await ctx.repo.update_prompt(
                prompt_id=int(prompt_id),
                title=title,
                template=data["prompt_template"],
                variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                reference_photo_file_id=data.get("reference_photo_file_id"),
            )
            updated_prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
            await state.clear()
            await message.answer("Title updated.")
            if updated_prompt:
                await ctx.show_prompt_edit_actions(message, updated_prompt)
        except asyncpg.UniqueViolationError:
            await message.answer("Prompt with this title already exists. Send another title.")

    @router.message(AdminStates.waiting_prompt_edit_template)
    async def admin_prompt_edit_template_value(message: Message, state: FSMContext) -> None:
        template = (message.text or "").strip()
        if not template:
            await message.answer("Template cannot be empty. Send new template:")
            return
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        if prompt_id is None:
            await message.answer("Prompt edit session expired. Open edit menu again.")
            await state.clear()
            return

        variables = extract_variables(template)
        descriptions = ctx.normalize_variable_descriptions_for_template(
            data.get("variable_descriptions", {}),
            variables,
        )

        await ctx.repo.update_prompt(
            prompt_id=int(prompt_id),
            title=data["prompt_title"],
            template=template,
            variable_descriptions=descriptions,
            reference_photo_file_id=data.get("reference_photo_file_id"),
        )
        updated_prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
        await state.clear()
        await message.answer("Template updated. Variable descriptions were kept for matching variables.")
        if updated_prompt:
            await ctx.show_prompt_edit_actions(message, updated_prompt)

    @router.callback_query(F.data.startswith("admin:editpart:ref:set:"))
    async def admin_edit_prompt_reference_set_start(callback: CallbackQuery, state: FSMContext) -> None:
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
        await state.clear()
        await state.update_data(
            admin_mode="edit_reference",
            editing_prompt_id=prompt_id,
            prompt_title=prompt["title"],
            prompt_template=prompt["template"],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=prompt["reference_photo_file_id"],
        )
        await state.set_state(AdminStates.waiting_prompt_reference)
        await callback.message.answer(
            "Send new reference image now.\n"
            "Use /skip to cancel reference update."
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:ref:clear:"))
    async def admin_edit_prompt_reference_clear(callback: CallbackQuery) -> None:
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
        await ctx.repo.update_prompt(
            prompt_id=prompt_id,
            title=prompt["title"],
            template=prompt["template"],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=None,
        )
        updated_prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        await callback.message.answer("Reference image removed.")
        if updated_prompt:
            await ctx.show_prompt_edit_actions(callback.message, updated_prompt)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:examples:"))
    async def admin_edit_prompt_examples_start(callback: CallbackQuery, state: FSMContext) -> None:
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
        current = prompt.get("example_file_ids")
        if isinstance(current, str):
            try:
                current = json.loads(current) if current else []
            except json.JSONDecodeError:
                current = []
        elif not isinstance(current, list):
            current = []
        await state.clear()
        await state.update_data(editing_prompt_id=prompt_id, example_file_ids=list(current))
        await state.set_state(AdminStates.waiting_prompt_examples)
        await callback.message.answer(
            "Send 1 to 3 photos as examples. Send /done after the last one or /skip to clear examples."
        )
        await callback.answer()
