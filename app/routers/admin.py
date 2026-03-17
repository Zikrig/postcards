"""Admin handlers: prompts, feach, promo, edit, delete, import, test."""
import asyncio
import json
import logging
import os
import random
from typing import Any, Optional

import asyncpg
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.keyboards import (
    build_admin_menu,
    build_admin_tag_item_menu,
    build_admin_tags_menu,
    build_feature_config_menu,
    build_prompt_edit_menu,
    build_prompt_edit_tags_menu,
    build_prompt_edit_variable_actions_menu,
    build_prompt_edit_variables_menu,
    build_prompt_feach_menu,
    build_prompt_item_menu,
    build_prompt_list_menu,
    build_prompt_work_menu,
    build_promo_item_menu,
    build_promo_list_menu,
    build_promo_menu,
)
from app.prompt_utils import build_prompt_export_payload, variable_descriptions_from_features
from app.states import AdminStates
from app.utils import (
    ensure_dict,
    extract_variables,
    get_feach_option_enabled,
    get_feach_option_text,
    make_option_key,
    normalize_feach_for_storage,
    render_prompt,
    variable_token,
)

from .common import RouterCtx


def register_admin(router: Router, ctx: RouterCtx) -> None:
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

    @router.callback_query((F.data == "admin:pw:list") | F.data.startswith("admin:pw:list:"))
    async def admin_prompt_list(callback: CallbackQuery) -> None:
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
                pass
        prompts, total = await ctx.repo.list_prompts_paginated(active_only=False, page=page, per_page=ctx.repo.PAGE_SIZE)
        if not total:
            await callback.message.answer(
                "No prompts yet. Use «Generate new prompt» or «Add prompt (manual)».",
                reply_markup=build_prompt_work_menu(),
            )
        else:
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

    @router.callback_query(
        (F.data == "admin:tags") | (F.data.startswith("admin:tags:") & (F.data != "admin:tags:back"))
    )
    async def admin_tags_menu(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        data = (callback.data or "").strip()
        page = 0
        if data.startswith("admin:tags:") and data != "admin:tags:back":
            try:
                page = int(data.split(":")[-1])
            except ValueError:
                pass
        tags, total = await ctx.repo.list_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
        try:
            await callback.message.edit_text(
                "Tags:",
                reply_markup=build_admin_tags_menu(tags, page=page, total=total),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "Tags:",
                reply_markup=build_admin_tags_menu(tags, page=page, total=total),
            )
        await callback.answer()

    @router.callback_query(F.data == "admin:tags:back")
    async def admin_tags_back(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            await callback.message.edit_text("Admin panel:", reply_markup=build_admin_menu())
        except TelegramBadRequest:
            await callback.message.answer("Admin panel:", reply_markup=build_admin_menu())
        await callback.answer()

    @router.callback_query(F.data == "admin:tag:add")
    async def admin_tag_add(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        await state.set_state(AdminStates.waiting_tag_name)
        await callback.message.answer("Enter tag name:")
        await callback.answer()

    @router.message(AdminStates.waiting_tag_name)
    async def admin_tag_name_entered(message: Message, state: FSMContext) -> None:
        user = await ctx.repo.get_user(message.from_user.id)
        if not user or not user["is_admin"]:
            return
        name = (message.text or "").strip()
        if not name:
            await message.answer("Tag name cannot be empty. Enter tag name:")
            return
        await ctx.repo.create_tag(name)
        await state.clear()
        tags, total = await ctx.repo.list_tags_paginated(page=0, per_page=ctx.repo.PAGE_SIZE)
        await message.answer("Tag added. Tags:", reply_markup=build_admin_tags_menu(tags, page=0, total=total))

    @router.callback_query(F.data.startswith("admin:tag:item:"))
    async def admin_tag_item(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            tag_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid tag", show_alert=True)
            return
        tag = await ctx.repo.get_tag_by_id(tag_id)
        if not tag:
            await callback.answer("Tag not found", show_alert=True)
            return
        await callback.message.answer(f"Tag: {tag['name']}", reply_markup=build_admin_tag_item_menu(tag_id))
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:tag:edit:"))
    async def admin_tag_edit(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            tag_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid tag", show_alert=True)
            return
        await state.update_data(editing_tag_id=tag_id)
        await state.set_state(AdminStates.waiting_tag_edit_name)
        await callback.message.answer("Enter new tag name:")
        await callback.answer()

    @router.message(AdminStates.waiting_tag_edit_name)
    async def admin_tag_edit_name_entered(message: Message, state: FSMContext) -> None:
        user = await ctx.repo.get_user(message.from_user.id)
        if not user or not user["is_admin"]:
            return
        data = await state.get_data()
        tag_id = data.get("editing_tag_id")
        if tag_id is None:
            await state.clear()
            return
        name = (message.text or "").strip()
        if not name:
            await message.answer("Tag name cannot be empty. Enter new tag name:")
            return
        await ctx.repo.update_tag(int(tag_id), name)
        await state.clear()
        tags, total = await ctx.repo.list_tags_paginated(page=0, per_page=ctx.repo.PAGE_SIZE)
        await message.answer("Tag renamed. Tags:", reply_markup=build_admin_tags_menu(tags, page=0, total=total))

    @router.callback_query(F.data.startswith("admin:tag:delete:"))
    async def admin_tag_delete(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            tag_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid tag", show_alert=True)
            return
        await ctx.repo.delete_tag(tag_id)
        tags, total = await ctx.repo.list_tags_paginated(page=0, per_page=ctx.repo.PAGE_SIZE)
        try:
            await callback.message.edit_text("Tags:", reply_markup=build_admin_tags_menu(tags, page=0, total=total))
        except TelegramBadRequest:
            await callback.message.answer("Tags:", reply_markup=build_admin_tags_menu(tags, page=0, total=total))
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:tags:"))
    async def admin_editpart_tags(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid prompt", show_alert=True)
            return
        try:
            prompt_id = int(parts[3])
            page = int(parts[4]) if len(parts) > 4 else 0
        except (ValueError, IndexError):
            await callback.answer("Invalid prompt", show_alert=True)
            return
        tag_ids = await ctx.repo.get_prompt_tag_ids(prompt_id)
        assigned_ids = set(tag_ids)
        tags, total = await ctx.repo.list_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
        try:
            await callback.message.edit_text(
                "Tags: 🟢 = assigned, 🔴 = not assigned. Click to toggle.",
                reply_markup=build_prompt_edit_tags_menu(prompt_id, tags, assigned_ids, page=page, total=total),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "Tags: 🟢 = assigned, 🔴 = not assigned. Click to toggle.",
                reply_markup=build_prompt_edit_tags_menu(prompt_id, tags, assigned_ids, page=page, total=total),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:tag_toggle:"))
    async def admin_editpart_tag_toggle(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 6:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[3])
            tag_id = int(parts[4])
            page = int(parts[5])
        except (ValueError, IndexError):
            await callback.answer("Invalid", show_alert=True)
            return
        tag_ids = await ctx.repo.get_prompt_tag_ids(prompt_id)
        if tag_id in tag_ids:
            tag_ids.remove(tag_id)
        else:
            tag_ids.append(tag_id)
        await ctx.repo.set_prompt_tags(prompt_id, tag_ids)
        assigned_ids = set(await ctx.repo.get_prompt_tag_ids(prompt_id))
        tags, total = await ctx.repo.list_tags_paginated(page=page, per_page=ctx.repo.PAGE_SIZE)
        try:
            await callback.message.edit_text(
                "Tags: 🟢 = assigned, 🔴 = not assigned. Click to toggle.",
                reply_markup=build_prompt_edit_tags_menu(prompt_id, tags, assigned_ids, page=page, total=total),
            )
        except TelegramBadRequest:
            pass
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
        except Exception as e:
            await message.answer(f"DeepSeek error: {e}")
            await state.clear()
            return
        try:
            await ctx.repo.insert_prompt(
                title=title,
                template=idea,
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

    @router.callback_query(F.data.startswith("admin:feach:"))
    async def admin_feach_feature(callback: CallbackQuery) -> None:
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
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        feat = features[feat_key]
        varname = feat.get("varname", feat_key)
        about = feat.get("about", "")
        try:
            await callback.message.edit_text(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feat),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feat),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:opt:"))
    async def admin_opt_toggle(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 6:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        opt_key = parts[4]
        enabled = parts[5] == "1"
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        feat = features[feat_key]
        opts = feat.get("options") or {}
        if opt_key.startswith("custom_"):
            custom = list(feat.get("custom") or [])
            idx = int(opt_key.replace("custom_", "")) if opt_key.replace("custom_", "").isdigit() else -1
            if 0 <= idx < len(custom):
                if isinstance(custom[idx], dict):
                    custom[idx] = {**custom[idx], "enabled": enabled}
                else:
                    custom[idx] = {"text": str(custom[idx]), "enabled": enabled}
                feat["custom"] = custom
        else:
            if opt_key in opts:
                if isinstance(opts[opt_key], dict):
                    opts[opt_key]["enabled"] = enabled
                else:
                    opts[opt_key] = {"text": get_feach_option_text(opts[opt_key]), "enabled": enabled}
        await ctx.repo.update_prompt_feach_data(prompt_id, feach_data)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if prompt:
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        feat_key,
                        feach_data.get("features", {}).get(feat_key, {}),
                    )
                )
            except TelegramBadRequest:
                # Ignore harmless edit errors (e.g. message was changed elsewhere).
                pass
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:featdel:"))
    async def admin_feature_delete(callback: CallbackQuery) -> None:
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
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        del features[feat_key]
        feach_data["features"] = features
        await ctx.repo.update_prompt_feach_data(prompt_id, feach_data)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer()
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        is_active = bool(prompt.get("is_active", True))
        if feach_data.get("features"):
            idea = feach_data.get("idea", "")
            try:
                await callback.message.edit_text(
                    f"Prompt: {prompt['title']}\n\nIdea: {idea}",
                    reply_markup=build_prompt_feach_menu(prompt_id, feach_data, is_active),
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    f"Prompt: {prompt['title']}\n\nIdea: {idea}",
                    reply_markup=build_prompt_feach_menu(prompt_id, feach_data, is_active),
                )
        else:
            try:
                await callback.message.edit_text(
                    f"Prompt: {prompt['title']}",
                    reply_markup=build_prompt_item_menu(prompt_id, is_active),
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    f"Prompt: {prompt['title']}",
                    reply_markup=build_prompt_item_menu(prompt_id, is_active),
                )
        await callback.answer("Feature deleted")

    @router.callback_query(F.data.startswith("admin:myown:"))
    async def admin_myown_toggle(callback: CallbackQuery) -> None:
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
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        feat = features[feat_key]
        feat["my_own"] = not feat.get("my_own", True)
        await ctx.repo.update_prompt_feach_data(prompt_id, feach_data)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if prompt:
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        feat_key,
                        feach_data.get("features", {}).get(feat_key, {}),
                    )
                )
            except TelegramBadRequest:
                pass
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:featadd:"))
    async def admin_feat_add_start(callback: CallbackQuery, state: FSMContext) -> None:
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
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        await state.update_data(feach_add_prompt_id=prompt_id, feach_add_feat_key=feat_key)
        await state.set_state(AdminStates.waiting_feach_add_option)
        await callback.message.answer("Send the new option text:")
        await callback.answer()

    @router.message(AdminStates.waiting_feach_add_option)
    async def admin_feach_add_option_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text:
            await message.answer("Send non-empty option text:")
            return
        data = await state.get_data()
        prompt_id = data.get("feach_add_prompt_id")
        feat_key = data.get("feach_add_feat_key")
        if prompt_id is None or not feat_key:
            await message.answer("Session expired.")
            await state.clear()
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await message.answer("Prompt not found.")
            await state.clear()
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await message.answer("Feature not found.")
            await state.clear()
            return
        feat = features[feat_key]
        custom = list(feat.get("custom") or [])
        custom.append({"text": text, "enabled": True})
        feat["custom"] = custom
        await ctx.repo.update_prompt_feach_data(prompt_id, feach_data)
        await state.clear()
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if prompt:
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            await message.answer(
                "Option added.",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feach_data.get("features", {}).get(feat_key, {})),
            )

    @router.callback_query(F.data.startswith("admin:featdone:"))
    async def admin_feat_done(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        is_active = bool(prompt.get("is_active", True))
        try:
            await callback.message.edit_text(
                "Done. Back to prompt.",
                reply_markup=build_prompt_feach_menu(prompt_id, feach_data, is_active),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "Done. Back to prompt.",
                reply_markup=build_prompt_feach_menu(prompt_id, feach_data, is_active),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:optview:"))
    async def admin_opt_view(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 5:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        feat_key = parts[3]
        opt_key = parts[4]
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        feat = features[feat_key]
        opts = feat.get("options") or {}
        custom = feat.get("custom") or []
        if opt_key.startswith("custom_"):
            idx = int(opt_key.replace("custom_", "")) if opt_key.replace("custom_", "").isdigit() else -1
            text = custom[idx].get("text", str(custom[idx])) if 0 <= idx < len(custom) and isinstance(custom[idx], dict) else (str(custom[idx]) if 0 <= idx < len(custom) else "")
        else:
            opt_val = opts.get(opt_key)
            text = get_feach_option_text(opt_val)
        await callback.answer(text[:200] if text else "—", show_alert=True)

    @router.callback_query(F.data.startswith("admin:final:"))
    async def admin_final_prompt(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        if not ctx.deepseek:
            await callback.answer("DeepSeek not available", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        idea = feach_data.get("idea", "")
        # Персона в сцене — всегда приложенное фото [USER_PHOTO], не текстовая переменная
        variables_spec: list[dict[str, Any]] = [
            {
                "name": "USER_PHOTO",
                "type": "image",
                "constant": None,
                "options": None,
                "allow_custom": False,
                "about": "Reference photo of the person to integrate into the scene",
            },
            {
                "name": "CHARACTER_POSITION",
                "type": "text",
                "constant": None,
                "options": [
                    "facing the camera",
                    "back to camera",
                    "looking left",
                    "looking right",
                    "profile view",
                    "in dialogue with someone",
                ],
                "allow_custom": True,
                "about": "Position or pose of the main character (the person from the reference photo)",
            },
        ]
        for feat_key, feat in features.items():
            varname = (feat.get("varname") or feat_key).upper().replace(" ", "_")
            opts = feat.get("options") or {}
            custom = feat.get("custom") or []
            enabled_opts = []
            for opt_k, opt_v in opts.items():
                if get_feach_option_enabled(opt_v):
                    enabled_opts.append(get_feach_option_text(opt_v))
            for c in custom:
                if isinstance(c, dict) and c.get("enabled", True):
                    enabled_opts.append(c.get("text", ""))
                elif isinstance(c, str):
                    enabled_opts.append(c)
            my_own = feat.get("my_own", True)
            # If no enabled options and no custom values allowed, completely drop this variable
            if not enabled_opts and not my_own and not custom:
                continue
            # If exactly one enabled option and no custom, treat it as constant (no placeholder)
            if len(enabled_opts) == 1 and not my_own and not custom:
                variables_spec.append(
                    {
                        "name": varname,
                        "type": "text",
                        "constant": enabled_opts[0],
                        "options": None,
                        "allow_custom": False,
                        "about": feat.get("about", ""),
                    }
                )
            else:
                variables_spec.append(
                    {
                        "name": varname,
                        "type": "text",
                        "constant": None,
                        "options": enabled_opts,
                        "allow_custom": my_own,
                        "about": feat.get("about", ""),
                    }
                )
        try:
            await callback.message.answer("Generating final prompt…")
            result = await ctx.deepseek.generate_final_prompt(idea, variables_spec)
        except Exception as e:
            await callback.message.answer(f"DeepSeek error: {e}")
            await callback.answer()
            return
        template = result.get("template", "")
        var_descriptions = ensure_dict(result.get("variable_descriptions") or {})
        # Всегда помечаем [USER_PHOTO] как image, чтобы не смешивать с текстовыми переменными
        if "[USER_PHOTO]" in template:
            var_descriptions["[USER_PHOTO]"] = {
                "description": "Reference photo of the person",
                "options": [],
                "allow_custom": True,
                "type": "image",
            }
        await ctx.repo.update_prompt(prompt_id, prompt["title"], template, var_descriptions, prompt.get("reference_photo_file_id"))
        await callback.message.answer("Final prompt saved. You can activate it or edit further.")
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if prompt:
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            await callback.message.answer(
                f"Template: {template[:300]}…" if len(template) > 300 else f"Template: {template}",
                reply_markup=build_prompt_feach_menu(prompt_id, feach_data, bool(prompt.get("is_active", True))),
            )
        await callback.answer()

    def _swap_test_button_label(markup: InlineKeyboardMarkup, prompt_id: int, label: str) -> InlineKeyboardMarkup:
        prefix = f"admin:test:{prompt_id}"
        new_rows: list[list[InlineKeyboardButton]] = []
        for row in markup.inline_keyboard:
            new_row: list[InlineKeyboardButton] = []
            for btn in row:
                if getattr(btn, "callback_data", None) == prefix:
                    new_row.append(InlineKeyboardButton(text=label, callback_data=prefix))
                else:
                    new_row.append(btn)
            new_rows.append(new_row)
        return InlineKeyboardMarkup(inline_keyboard=new_rows)

    @router.callback_query(F.data.startswith("admin:test:"))
    async def admin_test_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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

        async def answer_callback_soon() -> None:
            await asyncio.sleep(0.5)
            await callback.answer()

        asyncio.create_task(answer_callback_soon())

        orig_markup = callback.message.reply_markup
        if orig_markup and orig_markup.inline_keyboard:
            try:
                green_markup = _swap_test_button_label(orig_markup, prompt_id, "✅ Test")
                await callback.message.edit_reply_markup(reply_markup=green_markup)
            except TelegramBadRequest:
                pass
            else:

                async def restore_test_button() -> None:
                    await asyncio.sleep(5)
                    try:
                        await callback.message.edit_reply_markup(reply_markup=orig_markup)
                    except Exception:
                        pass

                asyncio.create_task(restore_test_button())

        template = str(prompt.get("template") or "")
        var_desc = ensure_dict(prompt.get("variable_descriptions") or {})
        variables = extract_variables(template)
        answers: dict[str, str] = {}
        for var in variables:
            token = variable_token(var)
            cfg = ctx.get_variable_config(var_desc, token, var["type"])
            opts = [str(x) for x in (cfg.get("options") or []) if str(x).strip()]
            if var["type"] == "text" and opts:
                answers[var["name"]] = random.choice(opts)
            else:
                answers[var["name"]] = ""
        final_prompt = render_prompt(template, answers)
        # Тестовая генерация: API принимает URL изображения (публичная ссылка)
        _test_image_url = (
            "https://static0.srcdn.com/wordpress/wp-content/uploads/2025/11/homelander-poster.jpg"
            "?q=49&fit=crop&w=1600&h=900&dpr=2"
        )
        image_urls: list[str] = [_test_image_url]
        admin_tg_id = callback.from_user.id
        new_balance = await ctx.repo.consume_generation_token(admin_tg_id)
        if new_balance is None:
            balance = await ctx.repo.get_user_balance(admin_tg_id)
            await callback.message.answer(f"Not enough balance for test. Your balance: {balance}")
            return
        progress_msg = await callback.message.answer("Test generation…")
        try:
            task_id = await ctx.evo.create_task(final_prompt, image_urls=image_urls)

            async def update_progress(status: Any, progress: Any) -> None:
                try:
                    await progress_msg.edit_text(f"Test generation… {status or 'processing'} {progress or '?'}%")
                except TelegramBadRequest:
                    pass

            details = await ctx.evo.wait_for_completion(task_id, on_progress=update_progress)
            status = details.get("status")
            if status != "completed":
                await progress_msg.delete()
                err = (details.get("error") or {}) if isinstance(details, dict) else {}
                await callback.message.answer(f"Test failed: {err.get('message', status)}")
                return
            results = details.get("results") or []
            if not results:
                await progress_msg.delete()
                await callback.message.answer("No image returned.")
                return
            await progress_msg.delete()
            sent = await callback.message.answer_photo(photo=results[0])
            file_id = sent.photo[-1].file_id if sent.photo else None
            if file_id:
                await state.update_data(admin_test_prompt_id=prompt_id, admin_test_file_id=file_id)
                await callback.message.answer(
                    "Add this image to prompt examples?",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Yes", callback_data=f"admin:test_add_ex:{prompt_id}")],
                        [InlineKeyboardButton(text="No", callback_data="admin:test_add_no")],
                    ]),
                )
            else:
                await callback.message.answer("Test done (could not get file_id).")
        except Exception as e:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            await callback.message.answer(f"Test error: {e}")

    @router.callback_query(F.data.startswith("admin:test_add_ex:"))
    async def admin_test_add_to_examples(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        data = await state.get_data()
        file_id = data.get("admin_test_file_id")
        if not file_id:
            await callback.answer("Session expired", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        current = prompt.get("example_file_ids") or []
        if not isinstance(current, list):
            current = []
        current = [str(x) for x in current][:3]
        if len(current) >= 3:
            current = current[:2] + [str(file_id)]
        else:
            current = current + [str(file_id)]
        await ctx.repo.set_prompt_examples(prompt_id, current)
        await state.clear()
        try:
            await callback.message.edit_text("Added to examples.")
        except TelegramBadRequest:
            await callback.message.answer("Added to examples.")
        await callback.answer("Added")

    @router.callback_query(F.data == "admin:test_add_no")
    async def admin_test_add_no(callback: CallbackQuery, state: FSMContext) -> None:
        await state.clear()
        if callback.message:
            try:
                await callback.message.edit_text("Cancelled.")
            except TelegramBadRequest:
                pass
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:active:"))
    async def admin_toggle_active(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        new_active = not bool(prompt.get("is_active", True))
        await ctx.repo.set_prompt_active(prompt_id, new_active)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer()
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {}) if prompt.get("feach_data") else None
        is_active = bool(prompt.get("is_active", True))
        if feach_data and feach_data.get("features"):
            await callback.message.answer(
                f"Prompt: {prompt['title']}\nIdea: {feach_data.get('idea', '')}",
                reply_markup=build_prompt_feach_menu(prompt_id, feach_data, is_active),
            )
        else:
            await callback.message.answer(f"Prompt: {prompt['title']}", reply_markup=build_prompt_item_menu(prompt_id, is_active))
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:export:"))
    async def admin_export_prompt(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        logging.info("admin_export_prompt: callback.data=%r", callback.data)
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            try:
                prompt_id = int((callback.data or "").split(":")[-1])
            except ValueError:
                logging.warning("admin_export_prompt: invalid prompt id in data=%r", callback.data)
                await callback.answer("Invalid", show_alert=True)
                return
            logging.info("admin_export_prompt: prompt_id=%s", prompt_id)
            prompt = await ctx.repo.get_prompt_by_id(prompt_id)
            if not prompt:
                logging.warning("admin_export_prompt: prompt %s not found", prompt_id)
                await callback.answer("Prompt not found", show_alert=True)
                return
            payload = build_prompt_export_payload(prompt)
            logging.info("admin_export_prompt: payload built for prompt_id=%s (title=%r)", prompt_id, payload["title"])
            from io import BytesIO
            buf = BytesIO(json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8"))
            buf.seek(0)
            from aiogram.types import BufferedInputFile
            await callback.message.answer_document(
                BufferedInputFile(buf.getvalue(), filename=f"prompt_{prompt_id}.json"),
            )
            logging.info("admin_export_prompt: document sent for prompt_id=%s", prompt_id)
            await callback.answer()
        except Exception as e:
            logging.exception("admin_export_prompt: unexpected error")
            err_msg = str(e).strip() or type(e).__name__
            if len(err_msg) > 80:
                err_msg = err_msg[:77] + "..."
            try:
                await callback.answer(f"Export failed: {err_msg}", show_alert=True)
            except Exception:
                pass

    @router.callback_query(F.data == "admin:import")
    async def admin_import_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
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
        existing = await ctx.repo.get_prompt_by_title(title)
        try:
            if existing:
                # Keep existing reference when updating (import does not touch ref/feach/examples)
                keep_ref = existing.get("reference_photo_file_id") if ref_id is None else ref_id
                await ctx.repo.update_prompt(existing["id"], title, template, var_descriptions, keep_ref)
                await message.answer(f"Prompt «{title}» updated.")
            else:
                await ctx.repo.insert_prompt(
                    title, template, var_descriptions, ref_id, user["tg_id"],
                    is_active=True, feach_data=feach_data,
                )
                new_prompt = await ctx.repo.get_prompt_by_title(title)
                if new_prompt:
                    await ctx.repo.set_prompt_examples(new_prompt["id"], example_ids)
                await message.answer(f"Prompt «{title}» created.")
        except Exception as e:
            await message.answer(f"Error: {e}")
        await state.clear()

    @router.callback_query(F.data == "admin:pw:back")
    async def admin_prompt_work_back(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        await callback.message.answer("Admin panel:", reply_markup=build_admin_menu())
        await callback.answer()

    @router.callback_query(F.data == "admin:promo_menu")
    async def admin_promo_menu(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        promos = await ctx.repo.list_promo_codes()
        if promos:
            await callback.message.answer("Promo code list:", reply_markup=build_promo_list_menu(promos))
        else:
            await callback.message.answer(
                "Promo code list is empty.",
                reply_markup=build_promo_menu(),
            )
        await callback.answer()

    @router.callback_query(F.data == "admin:promo:back")
    async def admin_promo_back(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        await callback.message.answer("Admin panel:", reply_markup=build_admin_menu())
        await callback.answer()

    @router.callback_query(F.data == "admin:promo:create:single")
    async def admin_promo_create_single(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        await state.update_data(promo_mode="single")
        await state.update_data(promo_action="create", editing_promo_id=None)
        await state.set_state(AdminStates.waiting_promo_code)
        await callback.message.answer("Send promo code text (for start link payload).")
        await callback.answer()

    @router.callback_query(F.data == "admin:promo:create:multi")
    async def admin_promo_create_multi(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        await state.clear()
        await state.update_data(promo_mode="multi")
        await state.update_data(promo_action="create", editing_promo_id=None)
        await state.set_state(AdminStates.waiting_promo_code)
        await callback.message.answer("Send promo code text (for start link payload).")
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:item:"))
    async def admin_promo_item_actions(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await ctx.repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return
        max_uses = promo["max_uses"]
        max_uses_text = "unlimited" if max_uses is None else str(max_uses)
        await callback.message.answer(
            "Promo code details:\n"
            f"Code: {promo['code']}\n"
            f"Credits: {promo['credits_amount']}\n"
            f"Uses: {promo['uses_count']}/{max_uses_text}\n"
            f"Active: {promo['is_active']}",
            reply_markup=build_promo_item_menu(promo_id),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:edit:"))
    async def admin_promo_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await ctx.repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return

        mode = "single" if promo["max_uses"] == 1 else "multi"
        await state.clear()
        await state.update_data(
            promo_mode=mode,
            promo_action="edit",
            editing_promo_id=promo_id,
        )
        await state.set_state(AdminStates.waiting_promo_code)
        await callback.message.answer(
            f"Editing promo '{promo['code']}'.\n"
            "Send new promo code text:"
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:promo:delete:"))
    async def admin_promo_delete(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            promo_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid promo id", show_alert=True)
            return
        promo = await ctx.repo.get_promo_code_by_id(promo_id)
        if not promo:
            await callback.answer("Promo not found", show_alert=True)
            return
        deleted = await ctx.repo.delete_promo_code(promo_id)
        if deleted:
            await callback.message.answer(f"Promo deleted: {promo['code']}")
        else:
            await callback.message.answer("Promo was not deleted.")
        await callback.answer()

    @router.message(AdminStates.waiting_promo_code)
    async def admin_promo_code_value(message: Message, state: FSMContext) -> None:
        code = (message.text or "").strip()
        if not code or len(code) < 3:
            await message.answer("Promo code is too short. Send at least 3 characters.")
            return
        await state.update_data(promo_code=code)
        await state.set_state(AdminStates.waiting_promo_credits)
        await message.answer("How many generation tokens should this promo grant?")

    @router.message(AdminStates.waiting_promo_credits)
    async def admin_promo_credits_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text.isdigit() or int(text) <= 0:
            await message.answer("Send a positive integer.")
            return
        credits = int(text)
        data = await state.get_data()
        mode = data.get("promo_mode")
        await state.update_data(promo_credits=credits)

        if mode == "single":
            await state.update_data(promo_max_uses=1)
            await finalize_promo_creation(message, state)
            return

        await state.set_state(AdminStates.waiting_promo_max_uses)
        await message.answer("How many users can redeem it? Send positive integer, or 0 for unlimited.")

    @router.message(AdminStates.waiting_promo_max_uses)
    async def admin_promo_max_uses_value(message: Message, state: FSMContext) -> None:
        text = (message.text or "").strip()
        if not text.isdigit() or int(text) < 0:
            await message.answer("Send 0 or a positive integer.")
            return
        max_uses = int(text)
        await state.update_data(promo_max_uses=(None if max_uses == 0 else max_uses))
        await finalize_promo_creation(message, state)

    async def finalize_promo_creation(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        user = await ctx.repo.get_user(message.from_user.id)
        try:
            promo_action = data.get("promo_action", "create")
            if promo_action == "edit" and data.get("editing_promo_id") is not None:
                await ctx.repo.update_promo_code(
                    promo_id=int(data["editing_promo_id"]),
                    code=str(data["promo_code"]),
                    credits_amount=int(data["promo_credits"]),
                    max_uses=data.get("promo_max_uses"),
                )
            else:
                await ctx.repo.create_promo_code(
                    code=str(data["promo_code"]),
                    credits_amount=int(data["promo_credits"]),
                    max_uses=data.get("promo_max_uses"),
                    created_by=user["tg_id"] if user else message.from_user.id,
                )
            me = await ctx.bot.get_me()
            header = "Promo code updated." if promo_action == "edit" else "Promo code created."
            if me.username:
                link = f"https://t.me/{me.username}?start={data['promo_code']}"
                await message.answer(
                    f"{header}\n"
                    f"Start link: {link}"
                )
            else:
                await message.answer(
                    f"{header}\n"
                    f"Use payload in /start: {data['promo_code']}"
                )
        except asyncpg.UniqueViolationError:
            await message.answer("Promo code already exists. Choose another code.")
        finally:
            await state.clear()

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

    @router.callback_query(F.data.startswith("admin:pw:item:"))
    async def admin_prompt_item_actions(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        is_active = bool(prompt.get("is_active", True))
        idea = feach_data.get("idea", "") if feach_data else ""
        text = f"Prompt: {prompt['title']}"
        if idea:
            text = f"{text}\n\nIdea: {idea}"
        await callback.message.answer(
            text,
            reply_markup=build_prompt_feach_menu(prompt_id, feach_data or {}, is_active),
        )
        await callback.answer()
    @router.callback_query(F.data.startswith("admin:edit:"))
    async def admin_edit_prompt_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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

        await state.clear()
        await ctx.show_prompt_edit_actions(callback.message, prompt)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:title:"))
    async def admin_edit_prompt_title_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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
        await state.clear()
        await state.update_data(
            editing_prompt_id=prompt_id,
            prompt_title=prompt["title"],
            prompt_template=prompt["template"],
            variable_descriptions=ensure_dict(prompt.get("variable_descriptions") or {}),
            reference_photo_file_id=prompt["reference_photo_file_id"],
        )
        await state.set_state(AdminStates.waiting_prompt_edit_template)
        await callback.message.answer(
            "Send new template.\n"
            "- Use [var] for image variables\n"
            "- Use <var> for text variables"
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editpart:variables:"))
    async def admin_edit_prompt_variables_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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
        )
        await state.set_state(None)
        await ctx.show_variable_pick_menu(callback.message, state)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:pick:"))
    async def admin_edit_variable_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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
            await state.clear()
            await state.update_data(
                editing_prompt_id=prompt_id,
                prompt_title=prompt["title"],
                prompt_template=template,
                prompt_variables=variables,
                variable_descriptions=descriptions,
                reference_photo_file_id=prompt["reference_photo_file_id"],
            )
            await state.set_state(None)

        await ctx.show_variable_actions_menu(callback.message, state, var_idx)
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:editvar:field:name:"))
    async def admin_edit_variable_name_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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

    @router.callback_query(F.data.startswith("admin:editpart:ref:set:"))
    async def admin_edit_prompt_reference_set_start(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
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

    @router.callback_query(F.data.startswith("admin:delete:"))
    async def admin_delete_prompt_ask_confirm(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except (TypeError, ValueError):
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        title = prompt.get("title") or "Untitled"
        await callback.message.answer(
            f"Delete prompt «{title}»? This cannot be undone.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Yes, delete", callback_data=f"admin:delete_confirm:{prompt_id}"),
                    InlineKeyboardButton(text="Cancel", callback_data=f"admin:pw:item:{prompt_id}"),
                ],
            ]),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:delete_confirm:"))
    async def admin_delete_prompt_confirm(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        if not user or not user["is_admin"]:
            await callback.answer("Admin only", show_alert=True)
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except (TypeError, ValueError):
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        async with ctx.repo.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT title FROM prompts WHERE id = $1", prompt_id)
            if not row:
                await callback.answer("Prompt not found", show_alert=True)
                return
            await conn.execute("DELETE FROM prompts WHERE id = $1", prompt_id)
        await callback.message.answer(f"Prompt deleted: {row['title']}")
        await callback.answer("Deleted")

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
