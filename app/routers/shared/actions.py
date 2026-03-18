import asyncio
import json
import logging
import random
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from app.prompt_utils import build_prompt_export_payload
from app.utils import ensure_dict, extract_variables, render_prompt, variable_token
from app.routers.common import RouterCtx


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


def register_shared_actions(router: Router, ctx: RouterCtx) -> None:

    @router.callback_query(F.data.startswith("admin:test:"))
    async def admin_test_prompt(callback: CallbackQuery, state: FSMContext) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        logging.info("admin_test_prompt: data=%r, user_tg_id=%s, user_record=%r", callback.data, callback.from_user.id, user)
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except ValueError:
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        logging.info(
            "admin_test_prompt: prompt_id=%s, owner_tg_id=%s, is_admin=%s, is_owner=%s",
            prompt_id,
            prompt.get("owner_tg_id"),
            is_admin,
            is_owner,
        )
        if not (is_admin or is_owner):
            logging.warning("admin_test_prompt: no permission, answering Not allowed")
            await callback.answer("Not allowed", show_alert=True)
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
        _test_image_url = (
            "https://static0.srcdn.com/wordpress/wp-content/uploads/2025/11/homelander-poster.jpg"
            "?q=49&fit=crop&w=1600&h=900&dpr=2"
        )
        image_urls: list[str] = [_test_image_url]
        user_tg_id = callback.from_user.id
        is_community_test = (prompt.get("owner_tg_id") is not None and prompt.get("owner_tg_id") != user_tg_id)

        if is_community_test:
            new_balance = await ctx.repo.get_user_balance(user_tg_id)
        else:
            new_balance = await ctx.repo.consume_tokens(user_tg_id, 1)

        if new_balance is None:
            balance = await ctx.repo.get_user_balance(user_tg_id)
            await callback.message.answer(f"Not enough balance for test (1 🪙 needed). Your balance: {balance}")
            return

        msg_text = f"Test generation started (Balance: {new_balance})…"
        if not is_community_test:
            msg_text = f"Test generation started (1 🪙 deducted, Balance: {new_balance})…"
        progress_msg = await callback.message.answer(msg_text)
        try:
            task_id = await ctx.evo.create_task(final_prompt, image_urls=image_urls)

            async def update_progress(status: any, progress: any) -> None:
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
            try:
                sent = await callback.message.answer_photo(photo=results[0])
            except TelegramBadRequest:
                sent = await ctx._send_photo_via_download_return(callback.message, results[0])
            file_id = (sent.photo[-1].file_id if sent and sent.photo else None)
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
        logging.info("admin_test_add_to_examples: data=%r, user_tg_id=%s, user_record=%r", callback.data, callback.from_user.id, user)
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
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        logging.info(
            "admin_test_add_to_examples: prompt_id=%s, owner_tg_id=%s, is_admin=%s, is_owner=%s",
            prompt_id,
            prompt.get("owner_tg_id"),
            is_admin,
            is_owner,
        )
        if not (is_admin or is_owner):
            logging.warning("admin_test_add_to_examples: no permission, answering Not allowed")
            await callback.answer("Not allowed", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
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

    @router.callback_query(F.data.startswith("admin:export:"))
    async def admin_export_prompt(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        logging.info("admin_export_prompt: callback.data=%r", callback.data)
        user = await ctx.repo.get_user(callback.from_user.id)
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
            is_admin = bool(user and user.get("is_admin"))
            is_owner = prompt.get("owner_tg_id") == callback.from_user.id
            if not (is_admin or is_owner):
                await callback.answer("Not allowed", show_alert=True)
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

    @router.callback_query(F.data.startswith("admin:delete:"))
    async def admin_delete_prompt_ask_confirm(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except (TypeError, ValueError):
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
            return
        title = prompt.get("title") or "Untitled"
        cancel_cb = f"menu:my_prompt_item:{prompt_id}" if is_owner else f"admin:pw:item:{prompt_id}"
        await callback.message.answer(
            f"Delete prompt «{title}»? This cannot be undone.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="Yes, delete", callback_data=f"admin:delete_confirm:{prompt_id}"),
                    InlineKeyboardButton(text="Cancel", callback_data=cancel_cb),
                ],
            ]),
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("admin:delete_confirm:"))
    async def admin_delete_prompt_confirm(callback: CallbackQuery) -> None:
        if not callback.message:
            return
        user = await ctx.repo.get_user(callback.from_user.id)
        try:
            prompt_id = int((callback.data or "").split(":")[-1])
        except (TypeError, ValueError):
            await callback.answer("Invalid prompt id", show_alert=True)
            return
        prompt = await ctx.repo.get_prompt_by_id(prompt_id)
        if not prompt:
            await callback.answer("Prompt not found", show_alert=True)
            return
        is_admin = bool(user and user.get("is_admin"))
        is_owner = prompt.get("owner_tg_id") == callback.from_user.id
        if not (is_admin or is_owner):
            await callback.answer("Not allowed", show_alert=True)
            return
        async with ctx.repo.pool.acquire() as conn:
            await conn.execute("DELETE FROM prompts WHERE id = $1", prompt_id)
        title = prompt.get("title") or "Untitled"
        await callback.message.answer(f"Prompt deleted: {title}")
        await callback.answer("Deleted")
