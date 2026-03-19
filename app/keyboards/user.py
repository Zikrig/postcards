"""User-facing keyboards (no admin-view branching)."""
from typing import Any

import asyncpg
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils import btn_label

from .common import PAGE_SIZE, _pagination_buttons


def build_main_menu(main_menu_prompts: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=btn_label(p["title"], 20), callback_data=f"prompt:select:{p['id']}")]
        for p in main_menu_prompts
    ]
    buttons.append([InlineKeyboardButton(text="✨ All postcards", callback_data="menu:tags")])
    buttons.append([InlineKeyboardButton(text="👤 My postcards", callback_data="menu:my_prompts:0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_tags_menu(
    tags: list[asyncpg.Record], page: int = 0, total: int = 0,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    buttons.append([InlineKeyboardButton(text="All (System)", callback_data="menu:tag:0")])
    buttons.append([InlineKeyboardButton(text="👥 Community (Users)", callback_data="menu:community_tags:0")])
    buttons.extend(
        [
            [InlineKeyboardButton(text=btn_label(str(t["name"]), 24), callback_data=f"menu:tag:{t['id']}")]
            for t in tags
            if str(t.get("name") or "") not in ["Main Menu", "Users"]
        ]
    )
    buttons.extend(_pagination_buttons(page, total, "menu:tags", "menu:main"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_community_tags_menu(
    tags: list[asyncpg.Record], page: int = 0, total: int = 0,
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    buttons.append([InlineKeyboardButton(text="All Community Prompts", callback_data="menu:community_tag:0")])
    for t in tags:
        name = str(t.get("name") or "")
        if name in ["Main Menu", "Users"]:
            continue
        buttons.append([InlineKeyboardButton(text=btn_label(name, 24), callback_data=f"menu:community_tag:{t['id']}")])
    buttons.extend(_pagination_buttons(page, total, "menu:community_tags", "menu:main"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_prompts_by_tag_menu(
    prompts: list[asyncpg.Record], tag_id: int, page: int = 0, total: int = 0,
) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=btn_label(p["title"], 20), callback_data=f"prompt:select:{p['id']}")]
        for p in prompts
    ]
    buttons.extend(_pagination_buttons(page, total, f"menu:tag:{tag_id}", "menu:tags"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_prompt_preview_menu(prompt_id: int, back_callback: str = "menu:main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Generate 1K (1 🪙)", callback_data=f"prompt:generate:1k:{prompt_id}")],
            [InlineKeyboardButton(text="🚀 Generate 2K (2 🪙)", callback_data=f"prompt:generate:2k:{prompt_id}")],
            [InlineKeyboardButton(text="🚀 Generate 4K (4 🪙)", callback_data=f"prompt:generate:4k:{prompt_id}")],
            [InlineKeyboardButton(text="◀ Back", callback_data=back_callback)],
        ]
    )


def build_my_prompts_menu(
    prompts: list[asyncpg.Record], page: int = 0, total: int = 0,
) -> InlineKeyboardMarkup:
    """User's own prompt list with a 'Create' button."""
    buttons: list[list[InlineKeyboardButton]] = []
    buttons.append([InlineKeyboardButton(text="➕ Create new prompt (2 🪙)", callback_data="menu:create_prompt")])
    for p in prompts:
        is_public = p.get("is_public", False)
        emoji = "🟢" if is_public else "🔒"
        label = f"{emoji} {p['title']}"
        buttons.append([InlineKeyboardButton(text=btn_label(label, 20), callback_data=f"menu:my_prompt_item:{p['id']}")])
    buttons.extend(_pagination_buttons(page, total, "menu:my_prompts", "menu:main"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_user_prompt_card(
    prompt_id: int,
    feach_data: dict[str, Any],
    is_active: bool,
    is_public: bool = False,
    template: str = "",
    back_callback: str = "menu:my_prompts:0",
    show_clone: bool = False,
) -> InlineKeyboardMarkup:
    """Prompt card for the owner viewing their own prompt."""
    features = feach_data.get("features") or {}
    rows: list[list[InlineKeyboardButton]] = []

    draft_idea = feach_data.get("idea", "")
    is_draft = (
        (template == draft_idea)
        or (not template)
        or (template == "Your prompt template here")
        or ("[" not in template and "<" not in template)
    )

    if is_draft:
        rows.append(
            [
                InlineKeyboardButton(
                    text="⚙️ Variable settings",
                    callback_data=f"admin:dfm:{prompt_id}",
                )
            ]
        )
    else:
        for feat_key, feat in features.items():
            label = btn_label(str((feat.get("varname") or feat_key) if isinstance(feat, dict) else feat_key), 18)
            rows.append([InlineKeyboardButton(text=f"🔹 {label}", callback_data=f"admin:feach:{prompt_id}:{feat_key}")])

    rows.append([InlineKeyboardButton(text="➕ Add variable", callback_data=f"admin:editvar:add:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="🪄 Generate final template", callback_data=f"admin:final:{prompt_id}")])

    if not is_draft:
        rows.append([InlineKeyboardButton(text="🚀 Generate", callback_data=f"prompt:select:{prompt_id}")])
        rows.append([InlineKeyboardButton(text="1 🪙 Test", callback_data=f"admin:test:{prompt_id}")])
        rows.append([InlineKeyboardButton(text="Tags", callback_data=f"admin:editpart:tags:{prompt_id}")])
        rows.append([InlineKeyboardButton(text="Edit", callback_data=f"admin:edit:{prompt_id}")])
        pub_label = "🔒 Make Private" if is_public else "🟢 Make Public"
        rows.append([InlineKeyboardButton(text=pub_label, callback_data=f"admin:toggle_public:{prompt_id}")])
        rows.append([InlineKeyboardButton(text="Export JSON", callback_data=f"admin:export:{prompt_id}")])

    if show_clone and not is_draft:
        rows.append([InlineKeyboardButton(text="Clone to All (System)", callback_data=f"admin:clone:{prompt_id}")])

    rows.append([InlineKeyboardButton(text="Delete", callback_data=f"admin:delete:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
