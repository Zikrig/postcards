"""Shared context and helpers for routers (ensure_user, run_generation, etc.)."""
import logging
import asyncio
from typing import Any, Optional, Dict, List, Union

import asyncpg
from aiogram import Bot, BaseMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject


class AlbumMiddleware(BaseMiddleware):
    """
    Middleware to group messages with the same media_group_id.
    It waits for `latency` seconds for messages of the same group to arrive.
    The collected list of messages is placed in `data['album']`.
    """
    def __init__(self, latency: float = 0.5):
        self.latency = latency
        self.albums: Dict[str, List[Message]] = {}
        super().__init__()

    async def __call__(
        self,
        handler: Any,
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message) or not event.media_group_id:
            return await handler(event, data)

        album_id = event.media_group_id
        if album_id not in self.albums:
            # First message of a media group
            self.albums[album_id] = [event]
            await asyncio.sleep(self.latency)
            # After waiting, we pass the album to the handler
            data["album"] = self.albums.pop(album_id, [])
            return await handler(event, data)

        # Subsequent messages just append to the list
        self.albums[album_id].append(event)
        return None

from app.config import Settings
from app.evo_client import EvoClient
from app.keyboards import (
    build_main_menu,
    build_prompt_edit_menu,
    build_prompt_edit_variable_actions_menu,
    build_prompt_edit_variables_menu,
    build_prompts_by_tag_menu,
    build_tags_menu,
    build_user_prompts_menu,
    build_admin_users_with_prompts_menu,
    build_community_tags_menu,
)
from app.repo import Repo
from app.states import AdminStates
from app.utils import ensure_dict, render_prompt, variable_token

logger = logging.getLogger(__name__)


BALANCE_BUCKET_KEY = "last_user_remaining_bucket_20"


class RouterCtx:
    def __init__(
        self,
        repo: Repo,
        settings: Settings,
        evo: EvoClient,
        bot: Bot,
        deepseek: Optional[Any] = None,
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.evo = evo
        self.bot = bot
        self.deepseek = deepseek

    async def ensure_user_from_tg(self, tg_user: Any) -> asyncpg.Record:
        assert tg_user is not None
        full_name = (tg_user.full_name or "").strip()
        logger.debug(f"Ensuring user from TG: {tg_user.id} ({tg_user.username})")
        return await self.repo.upsert_user(
            tg_id=tg_user.id,
            username=tg_user.username or "",
            full_name=full_name,
            is_admin=tg_user.id in self.settings.admin_ids,
        )

    async def ensure_user(self, message: Message) -> asyncpg.Record:
        return await self.ensure_user_from_tg(message.from_user)

    async def format_prompt_description(self, prompt: asyncpg.Record) -> str:
        """Returns a formatted description string for a prompt, including the author if available."""
        title = prompt.get("title") or "Untitled"
        description = (prompt.get("description") or title).strip()
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        idea = feach_data.get("idea", "")
        
        text = f"Prompt: {title}"
        if idea:
            text += f"\n\nIdea: {idea}"
        
        # Add description if different from title and idea
        if description and description != title and description != idea:
            text += f"\n\nDescription: {description}"
            
        owner_tg_id = prompt.get("owner_tg_id")
        if owner_tg_id:
            author = await self.repo.get_user(owner_tg_id)
            if author:
                author_name = author.get("username")
                if author_name:
                    author_name = f"@{author_name}"
                else:
                    author_name = author.get("full_name") or f"ID: {owner_tg_id}"
                text += f"\n\nAuthor: {author_name}"
        
        return text

    def extract_start_payload(self, message_text: str) -> str:
        parts = (message_text or "").split(maxsplit=1)
        if len(parts) < 2:
            return ""
        return parts[1].strip()

    async def show_prompt_buttons(self, message: Message) -> None:
        main_prompts = await self.repo.list_prompts_main_menu(active_only=True)
        all_active = await self.repo.list_prompts(active_only=True)
        if not all_active:
            await message.answer("No prompts yet. Please wait for admin to add them.")
            return
        await message.answer("Select a prompt or Generate:", reply_markup=build_main_menu(main_prompts))

    async def show_tags_menu(self, message: Message, page: int = 0) -> None:
        tags, total = await self.repo.list_tags_paginated(page=page, per_page=self.repo.PAGE_SIZE)
        await message.answer(
            "Choose a category:",
            reply_markup=build_tags_menu(tags, page=page, total=total),
        )

    async def show_prompts_for_tag(self, message: Message, tag_id: int, page: int = 0) -> None:
        if tag_id == 0:
            prompts, total = await self.repo.list_prompts_paginated(
                active_only=True, page=page, per_page=self.repo.PAGE_SIZE
            )
            name = "All"
        else:
            prompts, total = await self.repo.list_prompts_with_tag_paginated(
                tag_id, active_only=True, page=page, per_page=self.repo.PAGE_SIZE
            )
            tag = await self.repo.get_tag_by_id(tag_id)
            name = tag["name"] if tag else str(tag_id)
        text = f"Prompts in «{name}»:" if prompts else f"No prompts in «{name}»."
        await message.answer(text, reply_markup=build_prompts_by_tag_menu(prompts, tag_id, page=page, total=total))

    async def edit_to_main_menu(self, message: Message) -> None:
        main_prompts = await self.repo.list_prompts_main_menu(active_only=True)
        try:
            await message.edit_text("Select a prompt or Generate:", reply_markup=build_main_menu(main_prompts))
        except TelegramBadRequest:
            await self.show_prompt_buttons(message)

    async def edit_to_tags_menu(self, message: Message, page: int = 0) -> None:
        tags, total = await self.repo.list_tags_paginated(page=page, per_page=self.repo.PAGE_SIZE)
        try:
            await message.edit_text(
                "Choose a category:",
                reply_markup=build_tags_menu(tags, page=page, total=total),
            )
        except TelegramBadRequest:
            await self.show_tags_menu(message, page)

    async def edit_to_prompts_for_tag(self, message: Message, tag_id: int, page: int = 0) -> None:
        if tag_id == 0:
            prompts, total = await self.repo.list_prompts_paginated(
                active_only=True, page=page, per_page=self.repo.PAGE_SIZE
            )
            name = "All"
        else:
            prompts, total = await self.repo.list_prompts_with_tag_paginated(
                tag_id, active_only=True, page=page, per_page=self.repo.PAGE_SIZE
            )
            tag = await self.repo.get_tag_by_id(tag_id)
            name = tag["name"] if tag else str(tag_id)
        text = f"Prompts in «{name}»:" if prompts else f"No prompts in «{name}»."
        try:
            await message.edit_text(
                text,
                reply_markup=build_prompts_by_tag_menu(prompts, tag_id, page=page, total=total),
            )
        except TelegramBadRequest:
            await self.show_prompts_for_tag(message, tag_id, page)

    async def show_user_prompts(self, message: Message, owner_tg_id: int, page: int = 0, is_admin_view: bool = False) -> None:
        prompts, total = await self.repo.list_user_prompts_paginated(owner_tg_id, page=page, per_page=self.repo.PAGE_SIZE)
        text = "Your prompts:" if not is_admin_view else f"Prompts by user {owner_tg_id}:"
        if not prompts:
            text = "You have no prompts yet." if not is_admin_view else f"User {owner_tg_id} has no prompts."
        await message.answer(
            text,
            reply_markup=build_user_prompts_menu(prompts, page=page, total=total, is_admin_view=is_admin_view, owner_tg_id=owner_tg_id)
        )

    async def edit_to_user_prompts(self, message: Message, owner_tg_id: int, page: int = 0, is_admin_view: bool = False) -> None:
        prompts, total = await self.repo.list_user_prompts_paginated(owner_tg_id, page=page, per_page=self.repo.PAGE_SIZE)
        text = "Your prompts:" if not is_admin_view else f"Prompts by user {owner_tg_id}:"
        if not prompts:
            text = "You have no prompts yet." if not is_admin_view else f"User {owner_tg_id} has no prompts."
        try:
            await message.edit_text(
                text,
                reply_markup=build_user_prompts_menu(prompts, page=page, total=total, is_admin_view=is_admin_view, owner_tg_id=owner_tg_id)
            )
        except TelegramBadRequest:
            await self.show_user_prompts(message, owner_tg_id, page, is_admin_view)

    async def show_admin_users_list(self, message: Message, page: int = 0) -> None:
        users, total = await self.repo.list_users_with_prompts_paginated(page=page, per_page=self.repo.PAGE_SIZE)
        await message.answer(
            "Users with prompts:",
            reply_markup=build_admin_users_with_prompts_menu(users, page=page, total=total)
        )

    async def edit_to_admin_users_list(self, message: Message, page: int = 0) -> None:
        users, total = await self.repo.list_users_with_prompts_paginated(page=page, per_page=self.repo.PAGE_SIZE)
        try:
            await message.edit_text(
                "Users with prompts:",
                reply_markup=build_admin_users_with_prompts_menu(users, page=page, total=total)
            )
        except TelegramBadRequest:
            await self.show_admin_users_list(message, page)

    async def show_community_tags(self, message: Message, page: int = 0) -> None:
        tags, total = await self.repo.list_community_tags_paginated(page=page, per_page=self.repo.PAGE_SIZE)
        await message.answer(
            "Choose a community category:",
            reply_markup=build_community_tags_menu(tags, page=page, total=total),
        )

    async def edit_to_community_tags(self, message: Message, page: int = 0) -> None:
        logger.info(f"edit_to_community_tags: page={page}")
        tags, total = await self.repo.list_community_tags_paginated(page=page, per_page=self.repo.PAGE_SIZE)
        logger.info(f"Found {len(tags)} tags, total={total}")
        try:
            await message.edit_text(
                "Choose a community category:",
                reply_markup=build_community_tags_menu(tags, page=page, total=total),
            )
        except TelegramBadRequest as e:
            logger.warning(f"edit_to_community_tags edit failed: {e}")
            await self.show_community_tags(message, page)

    async def show_community_prompts(self, message: Message, tag_id: int, page: int = 0) -> None:
        logger.info(f"show_community_prompts: tag={tag_id}, page={page}")
        prompts, total = await self.repo.list_public_user_prompts_paginated(tag_id=tag_id, page=page, per_page=self.repo.PAGE_SIZE)
        logger.info(f"Found {len(prompts)} community prompts, total={total}")
        if tag_id == 0:
            name = "All Community"
        else:
            tag = await self.repo.get_tag_by_id(tag_id)
            name = tag["name"] if tag else str(tag_id)
        
        text = f"Community prompts in «{name}»:" if prompts else f"No community prompts in «{name}»."
        from app.keyboards import build_prompts_by_tag_menu
        markup = build_prompts_by_tag_menu(prompts, tag_id, page=page, total=total)
        
        # Create a new markup with replaced callback data because buttons are immutable
        new_rows = []
        for row in markup.inline_keyboard:
            new_row = []
            for btn in row:
                cb = btn.callback_data
                if cb:
                    if cb.startswith("menu:tag:"):
                        cb = cb.replace("menu:tag:", "menu:community_tag:")
                    elif cb == "menu:tags":
                        cb = "menu:community_tags:0"
                    elif cb.startswith("prompt:select:"):
                        cb = "menu:community_prompt:" + cb.split(":")[-1]
                new_row.append(InlineKeyboardButton(text=btn.text, callback_data=cb))
            new_rows.append(new_row)
        
        await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=new_rows))

    async def edit_to_community_prompts(self, message: Message, tag_id: int, page: int = 0) -> None:
        logger.info(f"edit_to_community_prompts: tag={tag_id}, page={page}")
        prompts, total = await self.repo.list_public_user_prompts_paginated(tag_id=tag_id, page=page, per_page=self.repo.PAGE_SIZE)
        logger.info(f"Found {len(prompts)} community prompts, total={total}")
        if tag_id == 0:
            name = "All Community"
        else:
            tag = await self.repo.get_tag_by_id(tag_id)
            name = tag["name"] if tag else str(tag_id)
        
        text = f"Community prompts in «{name}»:" if prompts else f"No community prompts in «{name}»."
        from app.keyboards import build_prompts_by_tag_menu
        markup = build_prompts_by_tag_menu(prompts, tag_id, page=page, total=total)
        
        new_rows = []
        for row in markup.inline_keyboard:
            new_row = []
            for btn in row:
                cb = btn.callback_data
                if cb:
                    if cb.startswith("menu:tag:"):
                        cb = cb.replace("menu:tag:", "menu:community_tag:")
                    elif cb == "menu:tags":
                        cb = "menu:community_tags:0"
                    elif cb.startswith("prompt:select:"):
                        cb = "menu:community_prompt:" + cb.split(":")[-1]
                new_row.append(InlineKeyboardButton(text=btn.text, callback_data=cb))
            new_rows.append(new_row)
        
        try:
            await message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=new_rows))
        except TelegramBadRequest as e:
            logger.warning(f"edit_to_community_prompts edit failed: {e}")
            await self.show_community_prompts(message, tag_id, page)

    def get_variable_config(
        self,
        raw_configs: Any,
        token: str,
        var_type: str,
    ) -> dict[str, Any]:
        configs = ensure_dict(raw_configs)
        raw = configs.get(token)
        if isinstance(raw, str):
            return {
                "description": raw,
                "options": [],
                "allow_custom": True,
                "type": var_type,
            }
        if isinstance(raw, dict):
            return {
                "description": str(raw.get("description") or ""),
                "options": [str(x) for x in (raw.get("options") or []) if str(x).strip()],
                "allow_custom": bool(raw.get("allow_custom", True)),
                "type": str(raw.get("type") or var_type),
            }
        return {
            "description": "",
            "options": [],
            "allow_custom": True,
            "type": var_type,
        }

    def normalize_variable_descriptions_for_template(
        self,
        raw_descriptions: Any,
        variables: list[dict[str, str]],
    ) -> dict[str, Any]:
        existing = ensure_dict(raw_descriptions)
        normalized: dict[str, Any] = {}
        for var in variables:
            token = variable_token(var)
            cfg = self.get_variable_config(existing, token, var["type"])
            cfg["type"] = var["type"]
            if var["type"] != "text":
                cfg["options"] = []
                cfg["allow_custom"] = True
            normalized[token] = cfg
        return normalized

    async def show_prompt_edit_actions(self, message: Message, prompt: asyncpg.Record, is_admin_view: bool | None = None) -> None:
        """
        Показывает меню редактирования промпта.
        is_admin_view:
          - True  → админский флоу (Back to list → admin:pw:list)
          - False → юзерский флоу (Back to list → menu:my_prompts:0)
          - None  → autodetect по prompt.owner_tg_id и user.is_admin (вызывающий код может пробросить явно)
        """
        ref_id = prompt.get("reference_photo_file_id")
        examples = prompt.get("example_file_ids")
        logger.info(
            "show_prompt_edit_actions: prompt_id=%s title=%r ref_id=%r examples=%r is_admin_view=%r",
            prompt.get("id"),
            prompt.get("title"),
            ref_id,
            examples,
            is_admin_view,
        )
        has_ref_or_example = bool(ref_id) or bool(examples)
        reference_text = "set" if has_ref_or_example else "not set"
        # Определяем, куда должен вести Back:
        # - если явно передали is_admin_view, используем его
        # - иначе: owner_tg_id есть → юзерский флоу (My prompts), иначе админский
        if is_admin_view is None:
            is_admin_view = prompt.get("owner_tg_id") is None
        back_cb = "admin:pw:list" if is_admin_view else "menu:my_prompts:0"
        await message.answer(
            "Prompt edit menu:\n"
            f"Title: {prompt['title']}\n"
            f"Reference image: {reference_text}\n"
            "Choose what to change:",
            reply_markup=build_prompt_edit_menu(int(prompt["id"]), back_callback=back_cb),
        )

    async def persist_prompt_edit_state(self, state: FSMContext) -> Optional[asyncpg.Record]:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        if prompt_id is None:
            return None
        await self.repo.update_prompt(
            prompt_id=int(prompt_id),
            title=data["prompt_title"],
            template=data["prompt_template"],
            variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
            reference_photo_file_id=data.get("reference_photo_file_id"),
        )
        return await self.repo.get_prompt_by_id(int(prompt_id))

    async def show_variable_pick_menu(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if prompt_id is None:
            await message.answer("Prompt edit session expired. Open edit menu again.")
            return
        if not variables:
            await message.answer("Template has no variables to edit.")
            prompt = await self.repo.get_prompt_by_id(int(prompt_id))
            if prompt:
                await self.show_prompt_edit_actions(message, prompt)
            return
        await message.answer(
            "Choose variable to edit:",
            reply_markup=build_prompt_edit_variables_menu(int(prompt_id), variables),
        )

    async def show_variable_actions_menu(self, message: Message, state: FSMContext, var_idx: int) -> None:
        data = await state.get_data()
        prompt_id = data.get("editing_prompt_id")
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        if prompt_id is None or var_idx < 0 or var_idx >= len(variables):
            await message.answer("Variable not found. Open variable list again.")
            return
        var = variables[var_idx]
        token = variable_token(var)
        descriptions = ensure_dict(data.get("variable_descriptions", {}))
        cfg = self.get_variable_config(descriptions, token, var["type"])
        options = [str(x) for x in (cfg.get("options") or []) if str(x).strip()]
        allow_custom = bool(cfg.get("allow_custom", True))
        await state.update_data(edit_var_idx=var_idx)
        details = (
            f"Variable: {token}\n"
            f"Type: {var['type']}\n"
            f"Description: {str(cfg.get('description') or '') or '(empty)'}\n"
        )
        if var["type"] == "text":
            details += f"Options: {', '.join(options) if options else '(none)'}\nMy own: {'ON' if allow_custom else 'OFF'}"
        # Владелец промпта (user или admin) должен возвращаться в свою карточку,
        # а не в админский список.
        prompt = await self.repo.get_prompt_by_id(int(prompt_id))
        owner_tg_id = prompt.get("owner_tg_id") if prompt else None
        is_owner_view = owner_tg_id == message.from_user.id if owner_tg_id is not None else False
        await message.answer(
            details,
            reply_markup=build_prompt_edit_variable_actions_menu(int(prompt_id), var_idx, var, is_owner_view=is_owner_view),
        )

    def build_text_options_keyboard(self, options: list[str], allow_custom: bool) -> InlineKeyboardMarkup:
        keyboard = [
            [InlineKeyboardButton(text=(opt[:20] if len(opt) > 20 else opt), callback_data=f"gen:opt:{idx}")]
            for idx, opt in enumerate(options)
        ]
        if allow_custom:
            keyboard.append([InlineKeyboardButton(text="My own", callback_data="gen:myown")])
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    async def ask_admin_text_options(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            await self.ask_admin_next_var_description(message, state)
            return
        var = variables[idx]
        if var["type"] != "text":
            await state.update_data(var_desc_idx=idx + 1)
            await self.ask_admin_next_var_description(message, state)
            return
        token = variable_token(var)
        await state.set_state(AdminStates.waiting_text_options)
        await message.answer(
            f"Set answer options for {token}.\n"
            "Send options separated by ';' (example: Mars; Venus; Jupiter), or /skip for no options."
        )

    async def ask_admin_allow_custom(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        var = variables[idx]
        token = variable_token(var)
        await state.set_state(AdminStates.waiting_text_allow_custom)
        await message.answer(
            f"Allow 'My own' custom text for {token}?",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text="Yes", callback_data="admin:allow_custom:yes"),
                        InlineKeyboardButton(text="No", callback_data="admin:allow_custom:no"),
                    ]
                ]
            ),
        )

    async def ask_admin_next_var_description(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("prompt_variables", [])
        idx: int = data.get("var_desc_idx", 0)
        if idx >= len(variables):
            admin_mode = data.get("admin_mode", "create")
            if admin_mode == "edit_variables" and data.get("editing_prompt_id") is not None:
                await self.repo.update_prompt(
                    prompt_id=int(data["editing_prompt_id"]),
                    title=data["prompt_title"],
                    template=data["prompt_template"],
                    variable_descriptions=ensure_dict(data.get("variable_descriptions", {})),
                    reference_photo_file_id=data.get("reference_photo_file_id"),
                )
                prompt = await self.repo.get_prompt_by_id(int(data["editing_prompt_id"]))
                await state.clear()
                await message.answer("Variable descriptions updated.")
                if prompt:
                    await self.show_prompt_edit_actions(message, prompt)
            else:
                await state.set_state(AdminStates.waiting_prompt_reference)
                await message.answer(
                    "Send optional reference image now, or type /skip to continue without it."
                )
            return
        var = variables[idx]
        token = variable_token(var)
        await message.answer(
            f"Write a user-facing description for {token}.\n"
            "User will see only this text.\n"
            "Type /skip to leave description empty."
        )

    async def ask_next_variable(self, message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        variables: list[dict[str, str]] = data.get("variables", [])
        current_idx: int = data.get("current_idx", 0)
        if current_idx >= len(variables):
            await self.run_generation(message, state)
            return
        variable = variables[current_idx]
        var_name = variable["name"]
        var_type = variable["type"]
        token = variable_token(variable)
        variable_descriptions = ensure_dict(data.get("variable_descriptions", {}))
        config = self.get_variable_config(variable_descriptions, token, var_type)
        description = str(config.get("description") or "").strip()
        options: list[str] = [str(x) for x in (config.get("options") or []) if str(x).strip()]
        allow_custom = bool(config.get("allow_custom", True))
        if var_type == "text":
            if len(options) == 1 and not allow_custom:
                answers: dict[str, str] = data.get("answers", {})
                answers[var_name] = options[0]
                await state.update_data(
                    answers=answers,
                    current_idx=current_idx + 1,
                    awaiting_custom_for=None,
                )
                await self.ask_next_variable(message, state)
                return
            if not options and not allow_custom:
                template = str(data.get("template") or "")
                template = template.replace(token, "")
                await state.update_data(
                    template=template,
                    current_idx=current_idx + 1,
                    awaiting_custom_for=None,
                )
                await self.ask_next_variable(message, state)
                return
        if description:
            await message.answer(description)
        elif var_type == "image":
            await message.answer("Please send a photo.")
        else:
            await message.answer("Please send text.")
        if var_type == "text" and options:
            await message.answer(
                "Choose one option:",
                reply_markup=self.build_text_options_keyboard(options, allow_custom),
            )

    async def run_generation(self, message: Message, state: FSMContext, cost: int = 1) -> None:
        data = await state.get_data()
        user_tg_id = int(
            data.get("request_user_id")
            or ((message.from_user.id if message.from_user else 0))
        )
        if not user_tg_id:
            await message.answer("Cannot detect user account for billing.")
            return
        # Generation is free for admins (requested for community prompts, but generally applies to admin tasks)
        is_admin = user_tg_id in self.settings.admin_ids
        if is_admin:
            new_balance = await self.repo.get_user_balance(user_tg_id)
        else:
            new_balance = await self.repo.consume_tokens(user_tg_id, cost)

        if new_balance is None:
            balance = await self.repo.get_user_balance(user_tg_id)
            await message.answer(
                f"Not enough balance for generation ({cost} tokens needed).\n"
                f"Your balance: {balance}\n"
                "Apply a promo code via your start link."
            )
            await state.clear()
            await self.show_prompt_buttons(message)
            return
        template = data["template"]
        answers: dict[str, str] = data.get("answers", {})
        image_urls: list[str] = data.get("image_urls", [])
        prompt_title = data["prompt_title"]
        final_prompt = render_prompt(template, answers)

        # Add signature if enabled
        signature_enabled = await self.repo.get_signature_enabled()
        if signature_enabled and self.settings.public_name:
            signature_text = f"\n\nAdd the text \"{self.settings.public_name}\" somewhere on the image, integrate it naturally and organically."
            final_prompt += signature_text

        progress_message = await message.answer(
            f"Generating image for prompt: {prompt_title}\nStatus: queued"
        )
        last_progress_text = progress_message.text or ""
        try:
            task_id = await self.evo.create_task(final_prompt, image_urls=image_urls)

            async def update_progress(status: Any, progress: Any) -> None:
                nonlocal last_progress_text
                status_text = str(status or "processing")
                progress_text = "?" if progress is None else str(progress)
                text = (
                    f"Generating image for prompt: {prompt_title}\n"
                    f"Status: {status_text}\n"
                    f"Progress: {progress_text}%"
                )
                if text == last_progress_text:
                    return
                try:
                    await progress_message.edit_text(text)
                    last_progress_text = text
                except TelegramBadRequest:
                    pass

            details = await self.evo.wait_for_completion(task_id, on_progress=update_progress)
            status = details.get("status")
            if status != "completed":
                await progress_message.delete()
                error = (details.get("error") or {}) if isinstance(details, dict) else {}
                error_code = error.get("code")
                error_message = (error.get("message") or "").strip()
                user_friendly = "Image generation failed."
                if error_message:
                    user_friendly = f"{user_friendly}\nReason: {error_message}"
                if error_code == "content_policy_violation":
                    user_friendly += (
                        "\n\nIt looks like the request may include brand logos, "
                        "trademarks, or copyrighted characters. "
                        "Please remove logos, brand names, and protected characters, "
                        "then try again."
                    )
                await message.answer(user_friendly)
                return
            results = details.get("results") or []
            if not results:
                await progress_message.delete()
                await message.answer("Generation completed, but no images were returned.")
                return
            await progress_message.delete()
            for url in results:
                await message.answer_photo(photo=url)
            await message.answer(f"Your balance: {new_balance}")
            await self.maybe_notify_admins_balance_checkpoint(data)
        except Exception as e:
            try:
                await progress_message.delete()
            except Exception:
                pass
            await message.answer(f"Error: {e}")
        finally:
            await state.clear()
            await self.show_prompt_buttons(message)

    async def maybe_notify_admins_balance_checkpoint(self, state_data: dict[str, Any]) -> None:
        credits = await self.evo.get_credits()
        user_data = (credits.get("data") or {}).get("user") or {}
        remaining_raw = user_data.get("remaining_credits")
        if remaining_raw is None:
            return
        try:
            remaining = float(remaining_raw)
        except (TypeError, ValueError):
            return
        current_bucket = int(remaining // 20)
        prev_bucket_raw = await self.repo.get_state_value(BALANCE_BUCKET_KEY)
        if prev_bucket_raw is None:
            await self.repo.set_state_value(BALANCE_BUCKET_KEY, str(current_bucket))
            return
        try:
            prev_bucket = int(prev_bucket_raw)
        except ValueError:
            prev_bucket = current_bucket
        if current_bucket < prev_bucket:
            admin_text = (
                "Balance checkpoint reached.\n"
                f"User remaining credits: {remaining}\n"
            )
            for admin_id in self.settings.admin_ids:
                try:
                    await self.bot.send_message(admin_id, admin_text)
                except Exception:
                    logging.exception("Failed to send admin balance notification to %s", admin_id)
        await self.repo.set_state_value(BALANCE_BUCKET_KEY, str(current_bucket))

    async def telegram_file_url(self, file_id: str) -> str:
        file = await self.bot.get_file(file_id)
        return f"https://api.telegram.org/file/bot{self.settings.bot_token}/{file.file_path}"
