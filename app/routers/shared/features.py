import logging
from typing import Any
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from app.keyboards.common import build_feature_config_menu
from app.states import AdminStates
from app.utils import ensure_dict, get_feach_option_enabled, get_feach_option_text, variable_token
from app.routers.common import RouterCtx


def register_shared_features(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query(F.data.startswith("admin:feach:"))
    async def admin_feach_feature(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        logging.info("admin_feach_feature: data=%r, user_tg_id=%s, user_record=%r", callback.data, callback.from_user.id, user)
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
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        logging.info(
            "admin_feach_feature: prompt_id=%s, feat_key=%s, owner_tg_id=%s, is_admin=%s, is_owner=%s",
            prompt_id,
            feat_key,
            prompt.get("owner_tg_id"),
            is_admin,
            is_owner,
        )
        if not (is_admin or is_owner):
            logging.warning("admin_feach_feature: no permission, answering Not allowed")
            await callback.answer("Not allowed", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return
        feat = features[feat_key]
        varname = feat.get("varname", feat_key)
        about = feat.get("about", "")
        back_cb = f"menu:my_prompt_item:{prompt_id}" if is_owner else f"admin:pw:item:{prompt_id}"
        try:
            await callback.message.edit_text(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feat, back_callback=back_cb),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feat, back_callback=back_cb),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:opt:"))
    async def admin_opt_toggle(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        logging.info("admin_opt_toggle: data=%r, user_tg_id=%s, user_record=%r", callback.data, callback.from_user.id, user)
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
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        logging.info(
            "admin_opt_toggle: prompt_id=%s, feat_key=%s, opt_key=%s, owner_tg_id=%s, is_admin=%s, is_owner=%s",
            prompt_id,
            feat_key,
            opt_key,
            prompt.get("owner_tg_id"),
            is_admin,
            is_owner,
        )
        if not (is_admin or is_owner):
            logging.warning("admin_opt_toggle: no permission, answering Not allowed")
            await callback.answer("Not allowed", show_alert=True)
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
            back_cb = f"menu:my_prompt_item:{prompt_id}" if is_owner else f"admin:pw:item:{prompt_id}"
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        feat_key,
                        feach_data.get("features", {}).get(feat_key, {}),
                        back_callback=back_cb,
                    )
                )
            except TelegramBadRequest:
                pass
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:featdel:"))
    async def admin_feature_delete(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        logging.info("admin_feature_delete: data=%r, user_tg_id=%s, user_record=%r", callback.data, callback.from_user.id, user)
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
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        logging.info(
            "admin_feature_delete: prompt_id=%s, feat_key=%s, owner_tg_id=%s, is_admin=%s, is_owner=%s",
            prompt_id,
            feat_key,
            prompt.get("owner_tg_id"),
            is_admin,
            is_owner,
        )
        if not (is_admin or is_owner):
            logging.warning("admin_feature_delete: no permission, answering Not allowed")
            await callback.answer("Not allowed", show_alert=True)
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
        text = await ctx.format_prompt_description(prompt)
        markup = ctx.build_prompt_card_markup(prompt, callback.from_user.id)
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=markup)
        await callback.answer("Feature deleted")

    @router.callback_query(F.data.startswith("admin:myown:"))
    async def admin_myown_toggle(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        logging.info("admin_myown_toggle: data=%r, user_tg_id=%s, user_record=%r", callback.data, callback.from_user.id, user)
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
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        logging.info(
            "admin_myown_toggle: prompt_id=%s, feat_key=%s, owner_tg_id=%s, is_admin=%s, is_owner=%s",
            prompt_id,
            feat_key,
            prompt.get("owner_tg_id"),
            is_admin,
            is_owner,
        )
        if not (is_admin or is_owner):
            logging.warning("admin_myown_toggle: no permission, answering Not allowed")
            await callback.answer("Not allowed", show_alert=True)
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
            back_cb = f"menu:my_prompt_item:{prompt_id}" if is_owner else f"admin:pw:item:{prompt_id}"
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        feat_key,
                        feach_data.get("features", {}).get(feat_key, {}),
                        back_callback=back_cb,
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
            is_admin = bool((await ctx.repo.get_user(message.from_user.id) or {}).get("is_admin"))
            is_owner = prompt.get("owner_tg_id") == message.from_user.id
            back_cb = f"menu:my_prompt_item:{prompt_id}" if is_owner else f"admin:pw:item:{prompt_id}"
            await message.answer(
                "Option added.",
                reply_markup=build_feature_config_menu(prompt_id, feat_key, feach_data.get("features", {}).get(feat_key, {}), back_callback=back_cb),
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
        user = await ctx.repo.get_user(callback.from_user.id)
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        markup = ctx.build_prompt_card_markup(prompt, callback.from_user.id)
        try:
            await callback.message.edit_text(
                "Done. Back to prompt.",
                reply_markup=markup,
            )
        except TelegramBadRequest:
            await callback.message.answer(
                "Done. Back to prompt.",
                reply_markup=markup,
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
        logging.info("admin_final_prompt: data=%r, user_tg_id=%s, user_record=%r", callback.data, callback.from_user.id, user)
        if not user:
            await callback.answer("Access denied", show_alert=True)
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
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        logging.info(
            "admin_final_prompt: prompt_id=%s, owner_tg_id=%s, is_admin=%s, is_owner=%s",
            prompt_id,
            prompt.get("owner_tg_id"),
            is_admin,
            is_owner,
        )
        if not (is_admin or is_owner):
            logging.warning("admin_final_prompt: no permission, answering Not allowed")
            await callback.answer("Not allowed", show_alert=True)
            return
        try:
            await callback.answer()
        except TelegramBadRequest:
            pass
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        idea = feach_data.get("idea", "")
        variables_spec: list[dict[str, Any]] = [
            {
                "name": "USER_PHOTO",
                "type": "image",
                "constant": None,
                "options": None,
                "allow_custom": False,
                "about": "Reference photo of the person to integrate into the scene",
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
            if not enabled_opts and not my_own and not custom:
                continue
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
        if "[USER_PHOTO]" in template:
            var_descriptions["[USER_PHOTO]"] = {
                "description": "Reference photo of the person",
                "options": [],
                "allow_custom": True,
                "type": "image",
            }
        await ctx.repo.update_prompt(prompt_id, prompt["title"], template, var_descriptions, prompt.get("reference_photo_file_id"))
        desc = (result.get("description") or "").strip()
        if desc:
            await ctx.repo.update_prompt_description(prompt_id, desc)
        await callback.message.answer("Final prompt saved. You can activate it or edit further.")
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if prompt:
            template = str(prompt.get("template") or "")
            markup = ctx.build_prompt_card_markup(prompt, callback.from_user.id)
            await callback.message.answer(
                f"Template: {template[:300]}…" if len(template) > 300 else f"Template: {template}",
                reply_markup=markup,
            )
