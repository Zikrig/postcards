"""Inline keyboard builders for menus."""
from typing import Any, Optional

import asyncpg
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .utils import btn_label, get_feach_option_enabled, variable_token

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


def build_main_menu(main_menu_prompts: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    """Main menu: prompts with 'Main Menu' tag first, then My prompts and Generate button."""
    buttons = [
        [
            InlineKeyboardButton(
                text=btn_label(f"1 🪙 {p['title']}", 20),
                callback_data=f"prompt:select:{p['id']}",
            )
        ]
        for p in main_menu_prompts
    ]
    buttons.append([InlineKeyboardButton(text="👤 My prompts", callback_data="menu:my_prompts:0")])
    buttons.append([InlineKeyboardButton(text="✨ Generate", callback_data="menu:tags")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_tags_menu(
    tags: list[asyncpg.Record], page: int = 0, total: int = 0
) -> InlineKeyboardMarkup:
    """List of tags for Generate submenu; pagination 20 per page."""
    buttons: list[list[InlineKeyboardButton]] = []
    # Special virtual tag "All" – always first, not stored in DB
    buttons.append(
        [InlineKeyboardButton(text="All", callback_data="menu:tag:0")]
    )
    # User's own prompts shortcut
    buttons.append(
        [InlineKeyboardButton(text="Users (My prompts)", callback_data="menu:my_prompts:0")]
    )
    # All real tags except "Main Menu" (оно только для главного меню, не как категория)
    buttons.extend(
        [
            [
                InlineKeyboardButton(
                    text=btn_label(str(t["name"]), 24),
                    callback_data=f"menu:tag:{t['id']}",
                )
            ]
            for t in tags
            if str(t.get("name") or "") not in ["Main Menu", "Users"]
        ]
    )
    buttons.extend(_pagination_buttons(page, total, "menu:tags", "menu:main"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_prompts_by_tag_menu(
    prompts: list[asyncpg.Record], tag_id: int, page: int = 0, total: int = 0
) -> InlineKeyboardMarkup:
    """Prompts for one tag; Back returns to tags list; pagination 20 per page."""
    buttons = [
        [
            InlineKeyboardButton(
                text=btn_label(f"1 🪙 {p['title']}", 20),
                callback_data=f"prompt:select:{p['id']}",
            )
        ]
        for p in prompts
    ]
    buttons.extend(_pagination_buttons(page, total, f"menu:tag:{tag_id}", "menu:tags"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_user_prompts_menu(
    prompts: list[asyncpg.Record], page: int = 0, total: int = 0, is_admin_view: bool = False, owner_tg_id: Optional[int] = None
) -> InlineKeyboardMarkup:
    """User's own prompts (or admin's view of them)."""
    buttons: list[list[InlineKeyboardButton]] = []
    # Button to create a new prompt (only for user view)
    if not is_admin_view:
        buttons.append([InlineKeyboardButton(text="➕ Create new prompt (2 🪙)", callback_data="menu:create_prompt")])

    for p in prompts:
        is_public = p.get("is_public", False)
        emoji = "🟢" if is_public else "🔒"
        label = f"{emoji} {p['title']}"
        cb = f"admin:pw:item:{p['id']}"
        buttons.append([InlineKeyboardButton(text=btn_label(label, 20), callback_data=cb)])
    
    if is_admin_view and owner_tg_id:
        base_cb = f"admin:pw:user_prompts:{owner_tg_id}"
    else:
        base_cb = "menu:my_prompts"
    
    back_cb = "admin:pw:users:0" if is_admin_view else "menu:main"
    
    buttons.extend(_pagination_buttons(page, total, base_cb, back_cb))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Prompt work", callback_data="admin:prompt_work")],
            [InlineKeyboardButton(text="Tags", callback_data="admin:tags")],
            [InlineKeyboardButton(text="Promo codes", callback_data="admin:promo_menu")],
        ]
    )


def build_admin_tags_menu(
    tags: list[asyncpg.Record], page: int = 0, total: int = 0
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
    prompts: list[asyncpg.Record], page: int = 0, total: int = 0
) -> InlineKeyboardMarkup:
    rows = []
    for p in prompts:
        active = p.get("is_active", True)
        label = btn_label(f"{'🟢' if active else '🔴'} {p['title']}", 20)
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:pw:item:{p['id']}")])
    # Back from prompt list goes to tag list, not to Prompt work
    rows.extend(_pagination_buttons(page, total, "admin:pw:list", "admin:pw:list"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_prompt_tags_menu(
    tags: list[asyncpg.Record], page: int = 0, total: int = 0
) -> InlineKeyboardMarkup:
    """
    Tag filter for 'List of prompts' in admin:
    - First: All (all prompts)
    - Second: Main Menu (prompts with Main Menu tag)
    - Third: Users (all user prompts)
    - Then all other tags (paginated, 20 per page).
    """
    rows: list[list[InlineKeyboardButton]] = []
    # All prompts
    rows.append(
        [InlineKeyboardButton(text="All", callback_data="admin:pw:list_tag:all:0")]
    )
    # Main Menu
    rows.append(
        [InlineKeyboardButton(text="Main Menu", callback_data="admin:pw:list_tag:main:0")]
    )
    # Users (special view by user list)
    rows.append(
        [InlineKeyboardButton(text="Users (User list)", callback_data="admin:pw:users:0")]
    )
    # Other tags (excluding system ones)
    for t in tags:
        name = str(t.get("name") or "")
        if name in ["Main Menu", "Users"]:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=btn_label(name, 24),
                    callback_data=f"admin:pw:list_tag:{t['id']}:0",
                )
            ]
        )
    # Pagination over real tags; Back to Prompt work
    rows.extend(_pagination_buttons(page, total, "admin:pw:list", "admin:prompt_work"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_admin_users_with_prompts_menu(
    users: list[asyncpg.Record], page: int = 0, total: int = 0
) -> InlineKeyboardMarkup:
    """Admin: list of users who have prompts."""
    rows: list[list[InlineKeyboardButton]] = []
    for u in users:
        label = f"{u['full_name'] or u['username'] or u['tg_id']} ({u['tg_id']})"
        rows.append([
            InlineKeyboardButton(text=btn_label(label, 24), callback_data=f"admin:pw:user_prompts:{u['tg_id']}:0")
        ])
    rows.extend(_pagination_buttons(page, total, "admin:pw:users", "admin:pw:list"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_item_menu(prompt_id: int, is_active: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Edit", callback_data=f"admin:edit:{prompt_id}")],
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


def build_prompt_feach_menu(
    prompt_id: int,
    feach_data: dict[str, Any],
    is_active: bool,
    owner_tg_id: Optional[int] = None,
    is_public: bool = False,
    is_admin_view: bool = False,
) -> InlineKeyboardMarkup:
    features = feach_data.get("features") or {}
    rows = []
    for feat_key, feat in features.items():
        label = btn_label(str((feat.get("varname") or feat_key) if isinstance(feat, dict) else feat_key), 18)
        rows.append([
            InlineKeyboardButton(text=f"🔹 {label}", callback_data=f"admin:feach:{prompt_id}:{feat_key}"),
        ])
    rows.append([
        InlineKeyboardButton(text="Generate final", callback_data=f"admin:final:{prompt_id}"),
    ])
    rows.append([
        InlineKeyboardButton(text="🚀 Generate (1 🪙)", callback_data=f"prompt:select:{prompt_id}"),
    ])
    rows.append([InlineKeyboardButton(text="Edit", callback_data=f"admin:edit:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Tags", callback_data=f"admin:editpart:tags:{prompt_id}")])
    
    if is_admin_view:
        rows.append([InlineKeyboardButton(text="Clone to All (System)", callback_data=f"admin:clone:{prompt_id}")])

    if owner_tg_id is not None:
        # Toggle public/private for user prompts
        label = "🔒 Make Private" if is_public else "🟢 Make Public"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:toggle_public:{prompt_id}")])

    rows.append([
        InlineKeyboardButton(
            text="Deactivate" if is_active else "Activate",
            callback_data=f"admin:active:{prompt_id}",
        ),
    ])
    rows.append([InlineKeyboardButton(text="Export JSON", callback_data=f"admin:export:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Test", callback_data=f"admin:test:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Delete", callback_data=f"admin:delete:{prompt_id}")])
    
    # Navigation back based on context
    back_cb = "admin:pw:users:0" if is_admin_view and owner_tg_id else "admin:pw:list"
    rows.append([InlineKeyboardButton(text="Back to list", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_feature_config_menu(
    prompt_id: int,
    feat_key: str,
    feature: dict[str, Any],
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
    rows.append([InlineKeyboardButton(text="Don't specify", callback_data=f"admin:featdel:{prompt_id}:{feat_key}")])
    rows.append([InlineKeyboardButton(text="Done", callback_data=f"admin:featdone:{prompt_id}:{feat_key}")])
    rows.append([InlineKeyboardButton(text="Back", callback_data=f"admin:pw:item:{prompt_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_menu(prompt_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Change title", callback_data=f"admin:editpart:title:{prompt_id}")],
            [InlineKeyboardButton(text="Change template", callback_data=f"admin:editpart:template:{prompt_id}")],
            [InlineKeyboardButton(text="Edit variables", callback_data=f"admin:editpart:variables:{prompt_id}")],
            [InlineKeyboardButton(text="Replace ref. image", callback_data=f"admin:editpart:ref:set:{prompt_id}")],
            [InlineKeyboardButton(text="Remove ref. image", callback_data=f"admin:editpart:ref:clear:{prompt_id}")],
            [InlineKeyboardButton(text="Examples (1–3)", callback_data=f"admin:editpart:examples:{prompt_id}")],
            [InlineKeyboardButton(text="Test", callback_data=f"admin:test:{prompt_id}")],
            [InlineKeyboardButton(text="Back to list", callback_data="admin:pw:list")],
        ]
    )


def build_prompt_edit_tags_menu(
    prompt_id: int,
    tags: list[asyncpg.Record],
    assigned_ids: set[int],
    page: int = 0,
    total: int = 0,
) -> InlineKeyboardMarkup:
    """All tags with 🟢 (assigned) / 🔴 (not assigned); click toggles; pagination 20 per page."""
    rows = []
    for t in tags:
        tid = int(t["id"])
        name = btn_label(str(t["name"]), 18)
        emoji = "🟢" if tid in assigned_ids else "🔴"
        rows.append([
            InlineKeyboardButton(
                text=f"{emoji} {name}",
                callback_data=f"admin:editpart:tag_toggle:{prompt_id}:{tid}:{page}",
            )
        ])
    rows.extend(
        _pagination_buttons(page, total, f"admin:editpart:tags:{prompt_id}", f"admin:pw:item:{prompt_id}")
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_variables_menu(prompt_id: int, variables: list[dict[str, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, var in enumerate(variables):
        token = variable_token(var)
        rows.append(
            [InlineKeyboardButton(text=btn_label(token, 20), callback_data=f"admin:editvar:pick:{prompt_id}:{idx}")]
        )
    rows.append([InlineKeyboardButton(text="Back to prompt edit", callback_data=f"admin:edit:{prompt_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_variable_actions_menu(
    prompt_id: int,
    var_idx: int,
    variable: dict[str, str],
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Rename variable", callback_data=f"admin:editvar:field:name:{prompt_id}:{var_idx}")],
        [InlineKeyboardButton(text="Change description", callback_data=f"admin:editvar:field:desc:{prompt_id}:{var_idx}")],
    ]
    if variable.get("type") == "text":
        rows.append(
            [InlineKeyboardButton(text="Change options", callback_data=f"admin:editvar:field:opts:{prompt_id}:{var_idx}")]
        )
        rows.append(
            [
                InlineKeyboardButton(text="My own: ON", callback_data=f"admin:editvar:allow:{prompt_id}:{var_idx}:yes"),
                InlineKeyboardButton(text="My own: OFF", callback_data=f"admin:editvar:allow:{prompt_id}:{var_idx}:no"),
            ]
        )
    rows.append([InlineKeyboardButton(text="Back to variables", callback_data=f"admin:editpart:variables:{prompt_id}")])
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
            [InlineKeyboardButton(text="Delete", callback_data=f"admin:promo:delete:{promo_id}")],
            [InlineKeyboardButton(text="Back to list", callback_data="admin:promo_menu")],
        ]
    )
