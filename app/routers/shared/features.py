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
from app.states import AdminStates, FinalPromptSetupStates, PrimaryPromptOnboardingStates
from app.utils import ensure_dict, get_feach_option_enabled, get_feach_option_text, variable_token
from app.routers.common import RouterCtx


async def resolve_primary_onboard_feature_back_callback(
    state: FSMContext,
    prompt_id: int,
    is_owner: bool,
    feat_key: str,
) -> str:
    """During primary onboarding, Back goes to previous variable; otherwise default prompt card."""
    cur = await state.get_state()
    if cur != PrimaryPromptOnboardingStates.reviewing_variables:
        return f"menu:my_prompt_item:{prompt_id}" if is_owner else f"admin:pw:item:{prompt_id}"
    data = await state.get_data()
    try:
        pid = int(data.get("ponboard_prompt_id"))
    except (TypeError, ValueError):
        return f"menu:my_prompt_item:{prompt_id}" if is_owner else f"admin:pw:item:{prompt_id}"
    keys = list(data.get("ponboard_keys") or [])
    try:
        idx = int(data.get("ponboard_idx", 0))
    except (TypeError, ValueError):
        idx = 0
    if pid != prompt_id or idx < 0 or idx >= len(keys) or keys[idx] != feat_key:
        return f"menu:my_prompt_item:{prompt_id}" if is_owner else f"admin:pw:item:{prompt_id}"
    return f"user:ponboard_feat_back:{prompt_id}:{idx}"


async def is_primary_onboard_feature_step(
    state: FSMContext,
    prompt_id: int,
    feat_key: str,
) -> bool:
    """True if this variable is the current step in primary onboarding."""
    cur = await state.get_state()
    if cur != PrimaryPromptOnboardingStates.reviewing_variables:
        return False
    data = await state.get_data()
    try:
        pid = int(data.get("ponboard_prompt_id"))
    except (TypeError, ValueError):
        return False
    keys = list(data.get("ponboard_keys") or [])
    try:
        idx = int(data.get("ponboard_idx", 0))
    except (TypeError, ValueError):
        idx = 0
    if pid != prompt_id or idx < 0 or idx >= len(keys):
        return False
    return keys[idx] == feat_key


def _prompt_is_draft(prompt: Any) -> bool:
    """
    Detect draft purely from DB fields.
    We cannot rely on FSM state because `admin:feach:*` callbacks are handled with `state.get_state()==None`.
    """
    feach_data_raw = {}
    if prompt is not None and hasattr(prompt, "get"):
        feach_data_raw = prompt.get("feach_data") or {}
    feach_data = ensure_dict(feach_data_raw)
    draft_idea = str(feach_data.get("idea") or "")
    template = str(prompt.get("template") or "") if prompt is not None and hasattr(prompt, "get") else ""
    return (template == draft_idea) or (not template) or (template == "Your prompt template here")


def register_shared_features(router: Router, ctx: RouterCtx) -> None:
    async def _reopen_prompt_card_message(message: Message, prompt_id: int, viewer_tg_id: int) -> None:
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if prompt:
            await ctx.present_prompt_card(message, prompt, viewer_tg_id)

    async def _send_final_wizard_step(message: Message, state: FSMContext) -> None:
        data = await state.get_data()
        prompt_id = data.get("final_wizard_prompt_id")
        keys: list[str] = data.get("final_wizard_keys") or []
        meta: list[dict[str, Any]] = data.get("final_wizard_meta") or []
        idx = int(data.get("final_wizard_idx", 0))
        if prompt_id is None or not keys or idx < 0 or idx >= len(keys):
            await message.answer("Wizard session expired or invalid step.")
            await state.clear()
            return
        prompt = await ctx.repo.get_prompt_by_id(int(prompt_id))
        if not prompt:
            await message.answer("Prompt not found.")
            await state.clear()
            return
        feat_key = keys[idx]
        m = meta[idx]
        mode = str(m.get("mode") or "pick")
        opts: list[str] = list(m.get("enabled_opts") or [])
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        features = feach_data.get("features") or {}
        feat = features.get(feat_key) or {}
        if not isinstance(feat, dict):
            feat = {}
        varname = feat.get("varname", feat_key)
        about = str(feat.get("about") or "").strip()
        step_n = idx + 1
        total = len(keys)
        header = f"Step {step_n}/{total}: {varname}"
        text = f"{header}\n{about}" if about else header
        kb = build_final_wizard_step_keyboard(int(prompt_id), idx, opts, mode)
        await message.answer(text, reply_markup=kb)

    async def _run_final_deepseek_generate(
        message: Message,
        prompt_id: int,
        idea: str,
        variables_spec: list[dict[str, Any]],
        user_tg_id: int,
    ) -> None:
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await message.answer("Prompt not found.")
            return
        progress_msg = await message.answer("Generating final prompt…")
        try:
            result = await ctx.deepseek.generate_final_prompt(idea, variables_spec)
        except Exception as e:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            await message.answer(f"DeepSeek error: {e}")
            return
        template = str(result.get("template") or "")
        var_descriptions = ensure_dict(result.get("variable_descriptions") or {})
        if "[USER_PHOTO]" in template:
            var_descriptions["[USER_PHOTO]"] = {
                "description": "Reference photo of the person",
                "options": [],
                "allow_custom": True,
                "type": "image",
            }
        await ctx.repo.update_prompt(
            prompt_id,
            str(prompt["title"]),
            template,
            var_descriptions,
            prompt.get("reference_photo_file_id"),
        )
        desc = (result.get("description") or "").strip()
        if desc:
            await ctx.repo.update_prompt_description(prompt_id, desc)
        try:
            await progress_msg.delete()
        except Exception:
            pass
        # UX: show a short post with a single action button.
        # The actual prompt card (with full controls) opens on demand.
        await message.answer(
            "Пост готов. Нажми TEST MY PROMPT, чтобы открыть карточку с настройками и генерацией.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="TEST MY PROMPT",
                            callback_data=f"prompt:test_my_prompt:{prompt_id}",
                        )
                    ]
                ]
            ),
        )

    @router.callback_query(F.data.startswith("admin:feach:"))
    async def admin_feach_feature(callback: CallbackQuery, state: FSMContext) -> None:
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
        back_cb = await resolve_primary_onboard_feature_back_callback(state, prompt_id, is_owner, feat_key)
        show_dont = _prompt_is_draft(prompt)
        try:
            await callback.message.edit_text(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(
                    prompt_id,
                    feat_key,
                    feat,
                    back_callback=back_cb,
                    show_dont_specify=show_dont,
                ),
            )
        except TelegramBadRequest:
            await callback.message.answer(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(
                    prompt_id,
                    feat_key,
                    feat,
                    back_callback=back_cb,
                    show_dont_specify=show_dont,
                ),
            )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:opt:"))
    async def admin_opt_toggle(callback: CallbackQuery, state: FSMContext) -> None:
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
            back_cb = await resolve_primary_onboard_feature_back_callback(state, prompt_id, is_owner, feat_key)
            show_dont = _prompt_is_draft(prompt)
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        feat_key,
                        feach_data.get("features", {}).get(feat_key, {}),
                        back_callback=back_cb,
                        show_dont_specify=show_dont,
                    )
                )
            except TelegramBadRequest:
                pass
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:nospec:"))
    async def admin_nospec_feature(callback: CallbackQuery, state: FSMContext) -> None:
        """
        Draft UX helper: "Dont specify" disables this feature entirely
        so it won't be included as a prompt variable during final template generation.
        """
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        feat_key = parts[3]

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
        if feat_key not in features:
            await callback.answer("Feature not found", show_alert=True)
            return

        feat = features.get(feat_key)
        if not isinstance(feat, dict):
            feat = {}

        # Disable feature entirely: no preset options, no custom, no my_own.
        feat["my_own"] = False
        opts = feat.get("options") or {}
        if isinstance(opts, dict):
            for _k, opt_v in opts.items():
                if isinstance(opt_v, dict):
                    opt_v["enabled"] = False
        feat["options"] = opts
        feat["custom"] = []
        features[feat_key] = feat
        feach_data["features"] = features

        await ctx.repo.update_prompt_feach_data(prompt_id, feach_data)
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if prompt:
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            back_cb = await resolve_primary_onboard_feature_back_callback(state, prompt_id, is_owner, feat_key)
            show_dont = _prompt_is_draft(prompt)
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        feat_key,
                        feach_data.get("features", {}).get(feat_key, {}),
                        back_callback=back_cb,
                        show_dont_specify=show_dont,
                    )
                )
            except TelegramBadRequest:
                pass

        await callback.answer("Variable disabled")

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
    async def admin_myown_toggle(callback: CallbackQuery, state: FSMContext) -> None:
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
            back_cb = await resolve_primary_onboard_feature_back_callback(state, prompt_id, is_owner, feat_key)
            show_dont = _prompt_is_draft(prompt)
            try:
                await callback.message.edit_reply_markup(
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        feat_key,
                        feach_data.get("features", {}).get(feat_key, {}),
                        back_callback=back_cb,
                        show_dont_specify=show_dont,
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
        if not user:
            await callback.answer("Not allowed", show_alert=True)
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
        is_admin = bool(user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
            return
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
        rec = await ctx.repo.get_user(message.from_user.id)
        is_adm = bool(rec and rec.get("is_admin"))
        is_own = prompt.get("owner_tg_id") == message.from_user.id
        if not (is_adm or is_own):
            await message.answer("Not allowed.")
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
        # Preserve primary onboarding across Add option flow (FSM was switched to waiting_feach_add_option).
        pon_pid = data.get("ponboard_prompt_id")
        pon_keys = data.get("ponboard_keys")
        pon_idx = data.get("ponboard_idx")
        has_ponboard = pon_pid is not None and pon_keys
        await ctx.repo.update_prompt_feach_data(prompt_id, feach_data)
        await state.clear()
        if has_ponboard:
            await state.set_state(PrimaryPromptOnboardingStates.reviewing_variables)
            await state.update_data(
                ponboard_prompt_id=pon_pid,
                ponboard_keys=pon_keys,
                ponboard_idx=pon_idx,
            )
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if prompt:
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            is_admin = bool((await ctx.repo.get_user(message.from_user.id) or {}).get("is_admin"))
            is_owner = prompt.get("owner_tg_id") == message.from_user.id
            back_cb = await resolve_primary_onboard_feature_back_callback(state, prompt_id, is_owner, str(feat_key))
            await message.answer(
                "Option added.",
                reply_markup=build_feature_config_menu(
                    prompt_id,
                    str(feat_key),
                    feach_data.get("features", {}).get(feat_key, {}),
                    back_callback=back_cb,
                    show_dont_specify=_prompt_is_draft(prompt),
                ),
            )

    @router.callback_query(F.data.startswith("admin:featdone:"))
    async def admin_feat_done(callback: CallbackQuery, state: FSMContext) -> None:
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
        feat_key = parts[3]
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
            return

        if await is_primary_onboard_feature_step(state, prompt_id, feat_key):
            data = await state.get_data()
            keys = list(data.get("ponboard_keys") or [])
            idx = int(data.get("ponboard_idx", 0))
            next_idx = idx + 1
            if next_idx >= len(keys):
                await ctx.send_prompt_generation_menu(callback.message, prompt_id, callback.from_user.id)
                await state.clear()
                await callback.answer()
                return
            await state.update_data(ponboard_idx=next_idx)
            prompt = await ctx.repo.get_prompt_by_id(prompt_id)
            if not prompt:
                await state.clear()
                await callback.answer("Prompt not found", show_alert=True)
                return
            show_dont = _prompt_is_draft(prompt)
            feach_data = ensure_dict(prompt.get("feach_data") or {})
            feats = feach_data.get("features") or {}
            if not isinstance(feats, dict):
                feats = {}
            next_key = keys[next_idx]
            nf = feats.get(next_key) if isinstance(feats.get(next_key), dict) else {}
            varname = nf.get("varname", next_key)
            about = nf.get("about", "")
            back_cb = await resolve_primary_onboard_feature_back_callback(state, prompt_id, is_owner, next_key)
            try:
                await callback.message.edit_text(
                    f"Variable: {varname}\nAbout: {about}",
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        next_key,
                        nf,
                        back_callback=back_cb,
                        show_dont_specify=show_dont,
                    ),
                )
            except TelegramBadRequest:
                await callback.message.answer(
                    f"Variable: {varname}\nAbout: {about}",
                    reply_markup=build_feature_config_menu(
                        prompt_id,
                        next_key,
                        nf,
                        back_callback=back_cb,
                        show_dont_specify=show_dont,
                    ),
                )
            await callback.answer()
            return

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
        # UX: "Generate Prompt from Draft" should not ask variable-by-variable choices.
        # We pass a fully specified variables_spec to DeepSeek (legacy behavior),
        # so generation happens immediately.
        variables_spec = build_variables_spec_legacy_no_wizard(features)
        await _run_final_deepseek_generate(
            callback.message,
            prompt_id,
            idea,
            variables_spec,
            callback.from_user.id,
        )
