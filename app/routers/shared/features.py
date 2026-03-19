import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.final_prompt_wizard import (
    FREE_FORM_INCLUDE,
    build_final_setup_steps,
    build_variables_spec_from_wizard_choices,
    build_variables_spec_legacy_no_wizard,
)
from app.keyboards.common import (
    build_draft_variable_settings_menu,
    build_feature_config_menu,
    build_final_wizard_step_keyboard,
)
from app.states import AdminStates, FinalPromptSetupStates
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
        await ctx.present_prompt_card(callback.message, prompt, callback.from_user.id)
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
        await ctx.present_prompt_card(callback.message, prompt, callback.from_user.id)
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

    @router.callback_query(F.data.startswith("admin:dfm:"))
    async def admin_draft_var_settings_menu(callback: CallbackQuery) -> None:
        """Advanced: list 🔹 variables (draft card no longer shows them all at once)."""
        if not callback.message:
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 3:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
            return
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        if not features:
            await callback.answer("No variables yet.", show_alert=True)
            return
        await callback.answer()
        kb = build_draft_variable_settings_menu(
            prompt_id,
            feach_data,
            back_callback=f"admin:dfb:{prompt_id}",
        )
        await callback.message.answer("Variable settings (advanced):", reply_markup=kb)

    @router.callback_query(F.data.startswith("admin:dfb:"))
    async def admin_draft_var_settings_back(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
            return
        await callback.answer()
        await _reopen_prompt_card_message(callback.message, prompt_id, callback.from_user.id)

    @router.callback_query(F.data.startswith("admin:fpcan:"))
    async def admin_final_wizard_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        data = await state.get_data()
        if data.get("final_wizard_prompt_id") != prompt_id:
            await callback.answer("Nothing to cancel.", show_alert=True)
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await state.clear()
            await callback.answer()
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
            return
        await state.clear()
        await callback.answer("Cancelled")
        await _reopen_prompt_card_message(callback.message, prompt_id, callback.from_user.id)

    @router.callback_query(
        StateFilter(FinalPromptSetupStates.choosing),
        F.data.startswith("admin:fpc:"),
    )
    async def admin_final_wizard_pick(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        parts = (callback.data or "").split(":")
        if len(parts) < 5:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
            step_idx = int(parts[3])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        choice_raw = parts[4]
        data = await state.get_data()
        if data.get("final_wizard_prompt_id") != prompt_id:
            await callback.answer("Session expired.", show_alert=True)
            return
        if data.get("final_wizard_idx") != step_idx:
            await callback.answer("Stale step.", show_alert=True)
            return
        keys: list[str] = data["final_wizard_keys"]
        meta: list[dict[str, Any]] = data["final_wizard_meta"]
        feat_key = keys[step_idx]
        m = meta[step_idx]
        opts: list[str] = m["enabled_opts"]
        mode = m["mode"]

        user = await ctx.repo.get_user(callback.from_user.id)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await state.clear()
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await state.clear()
            await callback.answer("Not allowed", show_alert=True)
            return

        choices = dict(data.get("final_wizard_choices") or {})
        if choice_raw == "sk":
            choices[feat_key] = None
        elif choice_raw == "ff":
            if mode != "freeform":
                await callback.answer("Invalid action", show_alert=True)
                return
            choices[feat_key] = FREE_FORM_INCLUDE
        elif choice_raw.startswith("o") and choice_raw[1:].isdigit():
            opt_i = int(choice_raw[1:])
            if mode != "pick" or opt_i < 0 or opt_i >= len(opts):
                await callback.answer("Invalid option", show_alert=True)
                return
            choices[feat_key] = opts[opt_i]
        else:
            await callback.answer("Invalid", show_alert=True)
            return

        next_idx = step_idx + 1
        await callback.answer("Saved")

        if next_idx >= len(keys):
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            features = feach_data.get("features") or {}
            idea = feach_data.get("idea", "")
            steps_full = [{"feat_key": k, "feat": features.get(k) or {}} for k in keys]
            variables_spec = build_variables_spec_from_wizard_choices(steps_full, choices)
            await state.clear()
            await _run_final_deepseek_generate(
                callback.message,
                prompt_id,
                idea,
                variables_spec,
                callback.from_user.id,
            )
            return

        await state.update_data(final_wizard_idx=next_idx, final_wizard_choices=choices)
        await _send_final_wizard_step(callback.message, state)

    @router.callback_query(F.data.startswith("admin:final:"))
    async def admin_final_prompt(callback: CallbackQuery, state: FSMContext) -> None:
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
        steps = build_final_setup_steps(features)
        if not steps:
            variables_spec = build_variables_spec_legacy_no_wizard(features)
            await _run_final_deepseek_generate(
                callback.message,
                prompt_id,
                idea,
                variables_spec,
                callback.from_user.id,
            )
            return
        await state.set_state(FinalPromptSetupStates.choosing)
        await state.update_data(
            final_wizard_prompt_id=prompt_id,
            final_wizard_keys=[s["feat_key"] for s in steps],
            final_wizard_meta=[{"mode": s["mode"], "enabled_opts": list(s["enabled_opts"])} for s in steps],
            final_wizard_idx=0,
            final_wizard_choices={},
        )
        await _send_final_wizard_step(callback.message, state)
