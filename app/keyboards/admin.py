"""Admin-facing keyboards (no is_admin_view branching)."""
from typing import Any, Optional

import logging

import asyncpg

logger = logging.getLogger(__name__)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.utils import btn_label, variable_token, pretty_variable_label

from .common import PAGE_SIZE, _pagination_buttons


def build_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Prompt work", callback_data="admin:prompt_work")],
            [InlineKeyboardButton(text="Tags", callback_data="admin:tags")],
            [InlineKeyboardButton(text="Promo codes", callback_data="admin:promo_menu")],
            [InlineKeyboardButton(text="Greeting", callback_data="admin:greeting")],
            [InlineKeyboardButton(text="Initial tokens", callback_data="admin:initial_tokens")],
        ]
    )


def build_admin_tags_menu(
    tags: list[asyncpg.Record], page: int = 0, total: int = 0,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=btn_label(str(t["name"]), 24), callback_data=f"admin:tag:item:{t['id']}")]
        for t in tags
    ]
    rows.append([InlineKeyboardButton(text="Add tag", callback_data="admin:tag:add")])
    rows.extend(_pagination_buttons(page, total, "admin:tags", "admin:tags:back"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_tag_item_menu(tag_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Rename", callback_data=f"admin:tag:edit:{tag_id}")],
            [InlineKeyboardButton(text="Delete", callback_data=f"admin:tag:delete:{tag_id}")],
            [InlineKeyboardButton(text="Back", callback_data="admin:tags")],
        ]
    )


def build_prompt_work_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Generate new prompt", callback_data="admin:gen:start")],
            [InlineKeyboardButton(text="List of prompts", callback_data="admin:pw:list")],
            [InlineKeyboardButton(text="Add prompt (manual)", callback_data="admin:pw:add")],
            [InlineKeyboardButton(text="Import JSON", callback_data="admin:import")],
            [InlineKeyboardButton(text="Back", callback_data="admin:pw:back")],
        ]
    )


def build_prompt_list_menu(
    prompts: list[asyncpg.Record], page: int = 0, total: int = 0,
) -> InlineKeyboardMarkup:
    rows = []
    for p in prompts:
        active = p.get("is_active", True)
        label = btn_label(f"{'🟢' if active else '🔴'} {p['title']}", 20)
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:pw:item:{p['id']}")])
    rows.extend(_pagination_buttons(page, total, "admin:pw:list", "admin:prompt_work"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_prompt_tags_menu(
    tags: list[asyncpg.Record], page: int = 0, total: int = 0,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text="All", callback_data="admin:pw:list_tag:all:0")])
    rows.append([InlineKeyboardButton(text="Main Menu", callback_data="admin:pw:list_tag:main:0")])
    rows.append([InlineKeyboardButton(text="Users (User list)", callback_data="admin:pw:users:0")])
    for t in tags:
        name = str(t.get("name") or "")
        if name in ["Main Menu", "Users"]:
            continue
        rows.append([InlineKeyboardButton(text=btn_label(name, 24), callback_data=f"admin:pw:list_tag:{t['id']}:0")])
    rows.extend(_pagination_buttons(page, total, "admin:pw:list", "admin:prompt_work"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_users_with_prompts_menu(
    users: list[asyncpg.Record], page: int = 0, total: int = 0,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for u in users:
        label = f"{u['full_name'] or u['username'] or u['tg_id']} ({u['tg_id']})"
        rows.append([InlineKeyboardButton(text=btn_label(label, 24), callback_data=f"admin:pw:user_prompts:{u['tg_id']}:0")])
    rows.extend(_pagination_buttons(page, total, "admin:pw:users", "admin:pw:list"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_user_prompts_menu(
    prompts: list[asyncpg.Record], owner_tg_id: int, page: int = 0, total: int = 0,
) -> InlineKeyboardMarkup:
    """Admin viewing a specific user's prompts."""
    buttons: list[list[InlineKeyboardButton]] = []
    for p in prompts:
        is_public = p.get("is_public", False)
        emoji = "🟢" if is_public else "🔒"
        label = f"{emoji} {p['title']}"
        buttons.append([InlineKeyboardButton(text=btn_label(label, 20), callback_data=f"admin:pw:item:{p['id']}")])
    buttons.extend(_pagination_buttons(page, total, f"admin:pw:user_prompts:{owner_tg_id}", "admin:pw:users:0"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_prompt_item_menu(prompt_id: int, is_active: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Prompt Edit menu", callback_data=f"admin:edit:{prompt_id}")],
        [InlineKeyboardButton(text="➕ Add variable", callback_data=f"admin:editvar:add:{prompt_id}")],
        [InlineKeyboardButton(text="Tags", callback_data=f"admin:editpart:tags:{prompt_id}")],
        [InlineKeyboardButton(
            text="Deactivate" if is_active else "Activate",
            callback_data=f"admin:active:{prompt_id}",
        )],
        [InlineKeyboardButton(text="Export JSON", callback_data=f"admin:export:{prompt_id}")],
        [InlineKeyboardButton(text="Delete", callback_data=f"admin:delete:{prompt_id}")],
        [InlineKeyboardButton(text="Back to list", callback_data="admin:pw:list")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_menu(
    prompt_id: int,
    back_callback: str = "admin:pw:list",
    show_clone: bool = False,
    is_draft: bool = False,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="📝 Description", callback_data=f"admin:editpart:description:{prompt_id}")],
        [InlineKeyboardButton(text="Change title", callback_data=f"admin:editpart:title:{prompt_id}")],
        [InlineKeyboardButton(text="Change template", callback_data=f"admin:editpart:template:{prompt_id}")],
        [InlineKeyboardButton(text="🖼 Images & examples", callback_data=f"admin:editpart:images:{prompt_id}")],
        [InlineKeyboardButton(text="📥 Import JSON", callback_data=f"admin:editpart:import_json:{prompt_id}")],
        [InlineKeyboardButton(text="📤 Export JSON", callback_data=f"admin:export:{prompt_id}")],
        [InlineKeyboardButton(text="🗑 Delete", callback_data=f"admin:delete:{prompt_id}")],
    ]

    if show_clone and not is_draft:
        rows.append([InlineKeyboardButton(text="🧩 Clone to All (System)", callback_data=f"admin:clone:{prompt_id}")])

    rows.append([InlineKeyboardButton(text="◀ Back to list", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_generation_menu(
    prompt_id: int,
    is_draft: bool,
    back_callback: str,
    feach_data: Optional[dict[str, Any]] = None,
) -> InlineKeyboardMarkup:
    """
    Submenu for draft → final template and variable configuration.
    Actual image generation is triggered separately by the prompt card.
    """
    rows: list[list[InlineKeyboardButton]] = []

    # Per-variable / feach configuration: needed both for drafts (template ≈ idea, primary AI pass)
    # and after final template so users can tune options. Card level stays clean; submenu shows 🔹.
    n_feat_rows = 0
    feat_keys: list[str] = []
    if feach_data:
        features = feach_data.get("features") or {}
        feat_keys = list(features.keys())
        for feat_key, feat in features.items():
            label = btn_label(
                str((feat.get("varname") or feat_key) if isinstance(feat, dict) else feat_key),
                18,
            )
            rows.append([
                InlineKeyboardButton(
                    text=f"🔹 {label}",
                    callback_data=f"admin:feach:{prompt_id}:{feat_key}",
                )
            ])
            n_feat_rows += 1

    logger.info(
        "build_prompt_generation_menu: prompt_id=%s is_draft=%s feach_keys=%s n_feature_rows=%s total_keyboard_rows_so_far=%s",
        prompt_id,
        is_draft,
        feat_keys,
        n_feat_rows,
        len(rows),
    )

    rows.append([InlineKeyboardButton(text="➕ Add variable", callback_data=f"admin:editvar:add:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="🪄 Generate Prompt from Draft", callback_data=f"admin:final:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="◀ Back to prompt", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_images_menu(prompt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Replace ref. image", callback_data=f"admin:editpart:ref:set:{prompt_id}")],
            [InlineKeyboardButton(text="Remove ref. image", callback_data=f"admin:editpart:ref:clear:{prompt_id}")],
            [InlineKeyboardButton(text="Examples (1–3)", callback_data=f"admin:editpart:examples:{prompt_id}")],
            [InlineKeyboardButton(text="◀ Back to edit", callback_data=f"admin:edit:{prompt_id}")],
        ]
    )


def build_prompt_edit_tags_menu(
    prompt_id: int,
    tags: list[asyncpg.Record],
    assigned_ids: set[int],
    page: int = 0,
    total: int = 0,
    back_callback: Optional[str] = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    main_menu_rows: list[list[InlineKeyboardButton]] = []
    other_rows: list[list[InlineKeyboardButton]] = []

    for t in tags:
        name_raw = str(t.get("name") or "")
        if name_raw == "Users":
            continue
        tid = int(t["id"])
        name = btn_label(name_raw, 18)
        emoji = "🟢" if tid in assigned_ids else "🔴"
        row = [
            InlineKeyboardButton(
                text=f"{emoji} {name}",
                callback_data=f"admin:editpart:tag_toggle:{prompt_id}:{tid}:{page}",
            )
        ]
        if name_raw == "Main Menu":
            main_menu_rows.append(row)
        else:
            other_rows.append(row)

    rows.extend(main_menu_rows)
    rows.extend(other_rows)
    rows.append([InlineKeyboardButton(text="➕ Add tag", callback_data=f"admin:editpart:tag_add:{prompt_id}:{page}")])

    base_cb = f"admin:editpart:tags:{prompt_id}"
    back_cb = back_callback or f"admin:pw:item:{prompt_id}"
    rows.extend(_pagination_buttons(page, total, base_cb, back_cb))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_variables_menu(
    prompt_id: int, variables: list[dict[str, str]], back_callback: Optional[str] = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, var in enumerate(variables):
        label = pretty_variable_label(var.get("name", ""), max_length=25)
        text = label or variable_token(var)
        rows.append([InlineKeyboardButton(text=text, callback_data=f"admin:editvar:pick:{prompt_id}:{idx}")])
    rows.append([InlineKeyboardButton(text="➕ Add variable", callback_data=f"admin:editvar:add:{prompt_id}")])
    back_cb = back_callback or f"admin:edit:{prompt_id}"
    rows.append([InlineKeyboardButton(text="Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_variable_actions_menu(
    prompt_id: int,
    var_idx: int,
    variable: dict[str, str],
    is_owner_view: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Rename variable", callback_data=f"admin:editvar:field:name:{prompt_id}:{var_idx}")],
        [InlineKeyboardButton(text="Change description", callback_data=f"admin:editvar:field:desc:{prompt_id}:{var_idx}")],
    ]
    if variable.get("type") == "text":
        rows.append(
            [InlineKeyboardButton(text="Change options", callback_data=f"admin:editvar:field:opts:{prompt_id}:{var_idx}")]
        )
        rows.append([
            InlineKeyboardButton(text="My own: ON", callback_data=f"admin:editvar:allow:{prompt_id}:{var_idx}:yes"),
            InlineKeyboardButton(text="My own: OFF", callback_data=f"admin:editvar:allow:{prompt_id}:{var_idx}:no"),
        ])
    back_cb = f"admin:editpart:variables:{prompt_id}"
    rows.append([InlineKeyboardButton(text="Back to variables", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_promo_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Single-user promo", callback_data="admin:promo:create:single")],
            [InlineKeyboardButton(text="Multi-user promo", callback_data="admin:promo:create:multi")],
            [InlineKeyboardButton(text="Back", callback_data="admin:promo:back")],
        ]
    )


def build_promo_list_menu(promos: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=btn_label(str(p["code"]), 20), callback_data=f"admin:promo:item:{p['id']}")]
        for p in promos
    ]
    rows.extend(build_promo_menu().inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_promo_item_menu(promo_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Edit", callback_data=f"admin:promo:edit:{promo_id}")],
            [InlineKeyboardButton(text="Toggle active", callback_data=f"admin:promo:toggle_active:{promo_id}")],
            [InlineKeyboardButton(text="Reset uses", callback_data=f"admin:promo:reset_uses:{promo_id}")],
            [InlineKeyboardButton(text="Delete", callback_data=f"admin:promo:delete:{promo_id}")],
            [InlineKeyboardButton(text="Back to list", callback_data="admin:promo_menu")],
        ]
    )


def build_admin_prompt_card(
    prompt_id: int,
    feach_data: dict[str, Any],
    is_active: bool,
    template: str = "",
    back_callback: str = "admin:pw:list",
    show_clone: bool = False,
) -> InlineKeyboardMarkup:
    """Prompt card for admin viewing a system prompt (no owner)."""
    features = feach_data.get("features") or {}
    rows: list[list[InlineKeyboardButton]] = []

    draft_idea = feach_data.get("idea", "")
    is_draft = (
        (template == draft_idea)
        or (not template)
        or (template == "Your prompt template here")
    )

    # Variables/feature configuration is handled inside "Prompt Generation Menu" (Variable settings),
    # so we do not show "🔹 ..." buttons on the card level.
    # Variables & "draft → final template" moved to a submenu
    rows.append([InlineKeyboardButton(text="🧩 Prompt Generation Menu", callback_data=f"admin:genmenu:{prompt_id}")])

    if not is_draft:
        rows.append(
            [InlineKeyboardButton(text="🚀 Generate postcard", callback_data=f"prompt:select:{prompt_id}")]
        )
        rows.append([InlineKeyboardButton(text="Tags", callback_data=f"admin:editpart:tags:{prompt_id}")])
        rows.append(
            [InlineKeyboardButton(
                text="Deactivate" if is_active else "Activate",
                callback_data=f"admin:active:{prompt_id}",
            )]
        )

    # Export/Delete/Clone moved into Prompt Edit menu
    rows.append([InlineKeyboardButton(text="Prompt Edit menu", callback_data=f"admin:edit:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_community_card(
    prompt_id: int,
    feach_data: dict[str, Any],
    template: str = "",
    back_callback: str = "admin:pw:users:0",
) -> InlineKeyboardMarkup:
    """Simplified card for admin viewing someone else's user prompt."""
    rows: list[list[InlineKeyboardButton]] = []

    draft_idea = feach_data.get("idea", "")
    is_draft = (
        (template == draft_idea)
        or (not template)
        or (template == "Your prompt template here")
    )

    if not is_draft:
        rows.append(
            [InlineKeyboardButton(text="🚀 Generate postcard (Free)", callback_data=f"prompt:select:{prompt_id}")]
        )

    rows.append([InlineKeyboardButton(text="🧩 Prompt Generation Menu", callback_data=f"admin:genmenu:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Prompt Edit menu", callback_data=f"admin:edit:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
