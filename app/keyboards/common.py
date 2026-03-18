"""Shared keyboard helpers and components used by both user and admin keyboards."""
from typing import Any, Optional

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils import btn_label, get_feach_option_enabled

PAGE_SIZE = 20


def _pagination_buttons(
    page: int, total: int, base_callback: str, back_callback: str, per_page: int = PAGE_SIZE
) -> list[list[InlineKeyboardButton]]:
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="◀ Prev", callback_data=f"{base_callback}:{page - 1}"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton(text="Next ▶", callback_data=f"{base_callback}:{page + 1}"))
    row.append(InlineKeyboardButton(text="◀ Back", callback_data=back_callback))
    return [row]


def build_feature_config_menu(
    prompt_id: int,
    feat_key: str,
    feature: dict[str, Any],
    back_callback: Optional[str] = None,
) -> InlineKeyboardMarkup:
    opts = feature.get("options") or {}
    custom = list(feature.get("custom") or [])
    rows = []
    for opt_key, opt_val in opts.items():
        text_short = btn_label(opt_key, 20)
        enabled = get_feach_option_enabled(opt_val)
        rows.append([
            InlineKeyboardButton(text=text_short, callback_data=f"admin:optview:{prompt_id}:{feat_key}:{opt_key}"),
            InlineKeyboardButton(
                text="🟢" if enabled else "🔴",
                callback_data=f"admin:opt:{prompt_id}:{feat_key}:{opt_key}:{'0' if enabled else '1'}",
            ),
        ])
    for i, c in enumerate(custom):
        opt_key = f"custom_{i}"
        text_short = btn_label(c.get("text", str(c)) if isinstance(c, dict) else str(c), 20)
        enabled = c.get("enabled", True) if isinstance(c, dict) else True
        rows.append([
            InlineKeyboardButton(text=text_short, callback_data=f"admin:optview:{prompt_id}:{feat_key}:{opt_key}"),
            InlineKeyboardButton(
                text="🟢" if enabled else "🔴",
                callback_data=f"admin:opt:{prompt_id}:{feat_key}:{opt_key}:{'0' if enabled else '1'}",
            ),
        ])
    my_own = feature.get("my_own", True)
    rows.append([
        InlineKeyboardButton(text=btn_label("My own (user types)", 20), callback_data=f"admin:myown:{prompt_id}:{feat_key}"),
        InlineKeyboardButton(text="ON" if my_own else "OFF", callback_data=f"admin:myown:{prompt_id}:{feat_key}"),
    ])
    rows.append([InlineKeyboardButton(text="Add option", callback_data=f"admin:featadd:{prompt_id}:{feat_key}")])
    rows.append([InlineKeyboardButton(text="Done", callback_data=f"admin:featdone:{prompt_id}:{feat_key}")])
    back_cb = back_callback or f"menu:my_prompt_item:{prompt_id}"
    rows.append([InlineKeyboardButton(text="Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
