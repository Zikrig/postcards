"""User flow: my prompts listing, prompt creation."""
import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton, InlineKeyboardMarkup

from app.keyboards.common import build_feature_config_menu
from app.states import AdminStates, PrimaryPromptOnboardingStates
from app.utils import ensure_dict
from app.routers.common import RouterCtx

logger = logging.getLogger(__name__)


async def _send_primary_onboard_step(
    message: Message,
    ctx: RouterCtx,
    prompt_id: int,
    keys: list[str],
    feats: dict[str, Any],
    idx: int,
) -> None:
    """Same variable editor as admin:feach (toggles, My own, Add option, Done, Back)."""
    total = len(keys)
    if idx < 0 or idx >= total:
        return
    k = keys[idx]
    feat = feats.get(k) if isinstance(feats.get(k), dict) else {}
    varname = feat.get("varname", k)
    about = feat.get("about", "")
    back_cb = f"user:ponboard_feat_back:{prompt_id}:{idx}"
    await message.answer(
        f"Variable: {varname}\nAbout: {about}",
        reply_markup=build_feature_config_menu(
            prompt_id, k, feat, back_callback=back_cb
        ),
    )


def register_user_my_prompts(router: Router, ctx: RouterCtx) -> None:
    @router.callback_query(F.data.startswith("menu:my_prompts:"))
    async def my_prompts_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        parts = (callback.data or "").split(":")
        page = int(parts[2]) if len(parts) > 2 else 0
        await callback.answer()
        await ctx.edit_to_user_prompts(callback.message, callback.from_user.id, page=page)

    @router.callback_query(F.data.startswith("menu:my_prompt_item:"))
    async def my_prompt_item_callback(callback: CallbackQuery) -> None:
        """Юзерское меню «My prompts»: открыть свой промпт с полным меню (редактирование и т.д.)."""
        if not callback.message:
            return
        logger.info("my_prompt_item_callback: data=%r, from_user_id=%s", callback.data, callback.from_user.id)
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
        owner_tg_id = prompt.get("owner_tg_id")
        is_owner = owner_tg_id == callback.from_user.id
        is_admin = bool(user.get("is_admin"))

        logger.info(
            "my_prompt_item_callback: prompt_id=%s, owner_tg_id=%s, user_tg_id=%s, is_admin=%s",
            prompt_id,
            owner_tg_id,
            callback.from_user.id,
            is_admin,
        )
        if not is_owner and not is_admin:
            await callback.answer("Not your prompt", show_alert=True)
            return

        # If draft primary-variable editing wasn't finished, we should open the
        # Prompt Generation Menu directly (draft cards are intentionally "minimal").
        feach_data = ensure_dict(prompt.get("feach_data") or {})
        draft_idea = feach_data.get("idea", "")
        template = str(prompt.get("template") or "")
        is_draft = (template == draft_idea) or (not template) or (template == "Your prompt template here")

        if is_draft:
            await ctx.send_prompt_generation_menu(callback.message, prompt_id, callback.from_user.id)
            await callback.answer()
            return

        await ctx.present_prompt_card(
            callback.message,
            prompt,
            callback.from_user.id,
            back_callback="menu:my_prompts:0",
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("user:ponboard_feat_back:"))
    async def primary_onboard_feat_back(callback: CallbackQuery, state: FSMContext) -> None:
        """During onboarding: Back → previous variable editor, or exit on first."""
        if not callback.message:
            return
        cur = await state.get_state()
        if cur != PrimaryPromptOnboardingStates.reviewing_variables:
            await callback.answer("This step is no longer active.", show_alert=True)
            return
        data = await state.get_data()
        parts = (callback.data or "").split(":")
        if len(parts) < 4:
            await callback.answer("Invalid", show_alert=True)
            return
        try:
            prompt_id = int(parts[2])
            step_idx = int(parts[3])
        except ValueError:
            await callback.answer("Invalid", show_alert=True)
            return
        pid = data.get("ponboard_prompt_id")
        keys = list(data.get("ponboard_keys") or [])
        idx = int(data.get("ponboard_idx", 0))
        if pid != prompt_id or idx != step_idx or not keys:
            await callback.answer("Session out of sync.", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt or prompt.get("owner_tg_id") != callback.from_user.id:
            await callback.answer("Not your prompt", show_alert=True)
            await state.clear()
            return

        if step_idx <= 0:
            await state.clear()
            await callback.answer()
            await callback.message.edit_text(
                "Variable setup cancelled. Open «My postcards» to continue editing your draft.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="👤 My postcards", callback_data="menu:my_prompts:0")]
                    ]
                ),
            )
            return

        prev_idx = step_idx - 1
        await state.update_data(ponboard_idx=prev_idx)
        feach = ensure_dict(prompt.get("feach_data") or {})
        feats = feach.get("features") or {}
        if not isinstance(feats, dict):
            feats = {}
        k = keys[prev_idx]
        feat = feats.get(k) if isinstance(feats.get(k), dict) else {}
        varname = feat.get("varname", k)
        about = feat.get("about", "")
        back_cb = f"user:ponboard_feat_back:{prompt_id}:{prev_idx}"
        await callback.answer()
        try:
            await callback.message.edit_text(
                f"Variable: {varname}\nAbout: {about}",
                reply_markup=build_feature_config_menu(
                    prompt_id, k, feat, back_callback=back_cb
                ),
            )
        except TelegramBadRequest:
            await _send_primary_onboard_step(
                callback.message, ctx, prompt_id, keys, feats, prev_idx
            )

    @router.callback_query(F.data == "menu:create_prompt")
    async def create_prompt_callback(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.ensure_user_from_tg(callback.from_user)
        if not user["is_authorized"]:
            await callback.answer("Please use /start first.", show_alert=True)
            return
        await callback.answer()
        await callback.message.answer("Enter a title for your new prompt:")
        await state.set_state(AdminStates.waiting_user_prompt_title)

    @router.message(AdminStates.waiting_user_prompt_title)
    async def user_prompt_title_handler(message: Message, state: FSMContext) -> None:
        user = await ctx.ensure_user(message)
        if not user["is_authorized"]:
            return
        title = (message.text or "").strip()
        if not title:
            return

        await state.update_data(user_prompt_title=title)
        await state.set_state(AdminStates.waiting_user_prompt_idea)
        await message.answer(f"Title: {title}\nNow enter the main idea for your image (2 🪙 will be charged):")

    @router.message(AdminStates.waiting_user_prompt_idea)
    async def user_prompt_idea_handler(message: Message, state: FSMContext) -> None:
        user = await ctx.ensure_user(message)
        if not user["is_authorized"]:
            return
        idea = (message.text or "").strip()
        if not idea:
            return

        data = await state.get_data()
        title = data.get("user_prompt_title")
        if not title:
            await message.answer("Session expired. Please start over.")
            await state.clear()
            return

        # Charge 2 tokens
        new_balance = await ctx.repo.consume_tokens(message.from_user.id, 2)
        if new_balance is None:
            balance = await ctx.repo.get_user_balance(message.from_user.id)
            await message.answer(f"Not enough balance to create a prompt (2 🪙 needed).\nYour balance: {balance}")
            await state.clear()
            return

        if not ctx.deepseek:
            await message.answer("Error: AI client unavailable.")
            await state.clear()
            return

        msg = await message.answer("Calling AI to refine your idea…")
        try:
            from app.utils import normalize_feach_for_storage

            api_feach = await ctx.deepseek.refine_idea(idea)
            normalized = normalize_feach_for_storage(api_feach)
            draft_template = normalized.get("idea") or idea
            feats = (normalized.get("features") or {}) if isinstance(normalized.get("features"), dict) else {}
            logger.info(
                "user_prompt_idea_handler: primary feach after refine_idea prompt_title=%r n_features=%s feature_keys=%s idea_len=%s",
                title,
                len(feats),
                list(feats.keys()),
                len(str(draft_template or "")),
            )

            prompt_id = await ctx.repo.insert_prompt(
                title=title,
                template=draft_template,
                variable_descriptions={},
                reference_photo_file_id=None,
                created_by=message.from_user.id,
                owner_tg_id=message.from_user.id,
                is_public=False,
                feach_data=normalized,
                is_active=False,
            )

            users_tag = await ctx.repo.get_tag_by_name("Users")
            if users_tag:
                await ctx.repo.set_prompt_tags(prompt_id, [users_tag["id"]])

            try:
                await msg.delete()
            except Exception:
                pass

            await message.answer(
                f"Prompt '{title}' created! 2 🪙 deducted (Balance: {new_balance})."
            )

            keys = list(feats.keys())
            if keys:
                await state.update_data(
                    ponboard_prompt_id=prompt_id,
                    ponboard_keys=keys,
                    ponboard_idx=0,
                )
                await state.set_state(PrimaryPromptOnboardingStates.reviewing_variables)
                await _send_primary_onboard_step(message, ctx, prompt_id, keys, feats, 0)
            else:
                await ctx.send_prompt_generation_menu(message, prompt_id, message.from_user.id)
                await state.clear()
        except Exception as e:
            logger.exception("user_prompt_idea_handler failed")
            await message.answer(f"Error refining idea: {e}")
            await state.clear()
