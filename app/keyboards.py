"""Inline keyboard builders for menus."""
from typing import Any, Optional

import asyncpg
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .utils import btn_label, get_feach_option_enabled, variable_token, pretty_variable_label

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
                text=btn_label(p['title'], 20),
                callback_data=f"prompt:select:{p['id']}",
            )
        ]
        for p in main_menu_prompts
    ]
    buttons.append([InlineKeyboardButton(text="✨ All postcards", callback_data="menu:tags")])
    buttons.append([InlineKeyboardButton(text="👤 My postcards", callback_data="menu:my_prompts:0")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_tags_menu(
    tags: list[asyncpg.Record], page: int = 0, total: int = 0
) -> InlineKeyboardMarkup:
    """List of tags for Generate submenu; pagination 20 per page."""
    buttons: list[list[InlineKeyboardButton]] = []
    # Special virtual tag "All" – always first, not stored in DB
    buttons.append(
        [InlineKeyboardButton(text="All (System)", callback_data="menu:tag:0")]
    )
    # Community prompts (Public user prompts)
    buttons.append(
        [InlineKeyboardButton(text="👥 Community (Users)", callback_data="menu:community_tags:0")]
    )
    # All real tags except "Main Menu" and "Users"
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


def build_community_tags_menu(
    tags: list[asyncpg.Record], page: int = 0, total: int = 0
) -> InlineKeyboardMarkup:
    """Categories specifically for user-generated public prompts."""
    buttons: list[list[InlineKeyboardButton]] = []
    buttons.append(
        [InlineKeyboardButton(text="All Community Prompts", callback_data="menu:community_tag:0")]
    )
    for t in tags:
        name = str(t.get("name") or "")
        if name in ["Main Menu", "Users"]:
            continue
        buttons.append([
            InlineKeyboardButton(text=btn_label(name, 24), callback_data=f"menu:community_tag:{t['id']}")
        ])
    # Back from community categories should return to the main menu, not reopen this screen
    buttons.extend(_pagination_buttons(page, total, "menu:community_tags", "menu:main"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_prompts_by_tag_menu(
    prompts: list[asyncpg.Record], tag_id: int, page: int = 0, total: int = 0
) -> InlineKeyboardMarkup:
    """Prompts for one tag; Back returns to tags list; pagination 20 per page."""
    buttons = [
        [
            InlineKeyboardButton(
                text=btn_label(p['title'], 20),
                callback_data=f"prompt:select:{p['id']}",
            )
        ]
        for p in prompts
    ]
    buttons.extend(_pagination_buttons(page, total, f"menu:tag:{tag_id}", "menu:tags"))
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_prompt_preview_menu(prompt_id: int, back_callback: str = "menu:main") -> InlineKeyboardMarkup:
    """Клавиатура экрана превью промпта: описание + иллюстрации, затем Генерировать (1 🪙) и Назад."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Generate (1 🪙)", callback_data=f"prompt:generate:{prompt_id}")],
            [InlineKeyboardButton(text="◀ Back", callback_data=back_callback)],
        ]
    )


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
        # Юзерское меню "My prompts" → свой пункт (полное меню с редактированием); админ-панель → admin:pw:item
        cb = f"menu:my_prompt_item:{p['id']}" if not is_admin_view else f"admin:pw:item:{p['id']}"
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
            [InlineKeyboardButton(text="Greeting", callback_data="admin:greeting")],
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


def build_prompt_feach_menu(
    prompt_id: int,
    feach_data: dict[str, Any],
    is_active: bool,
    owner_tg_id: Optional[int] = None,
    is_public: bool = False,
    is_admin_view: bool = False,
    template: str = "",
    back_callback: Optional[str] = None,
    show_clone: Optional[bool] = None,
) -> InlineKeyboardMarkup:
    features = feach_data.get("features") or {}
    rows = []
    
    draft_idea = feach_data.get("idea", "")
    is_draft = (template == draft_idea) or (not template) or (template == "Your prompt template here") or ("[" not in template and "<" not in template)

    is_community_admin = is_admin_view and owner_tg_id is not None

    if not is_community_admin:
        for feat_key, feat in features.items():
            label = btn_label(str((feat.get("varname") or feat_key) if isinstance(feat, dict) else feat_key), 18)
            rows.append([
                InlineKeyboardButton(text=f"🔹 {label}", callback_data=f"admin:feach:{prompt_id}:{feat_key}"),
            ])
        # Кнопка добавления переменной сразу под списком фич
        rows.append([
            InlineKeyboardButton(text="➕ Add variable", callback_data=f"admin:editvar:add:{prompt_id}"),
        ])

        # Generate final is only for non-community admin view
        rows.append([
            InlineKeyboardButton(text="🪄 Generate final template", callback_data=f"admin:final:{prompt_id}"),
        ])

    if not is_draft:
        # These buttons only if NOT draft
        if not is_community_admin:
            rows.append([
                InlineKeyboardButton(text=f"🚀 Generate (1 🪙)", callback_data=f"prompt:select:{prompt_id}"),
            ])
            test_label = "1 🪙 Test" if owner_tg_id else "Test"
            rows.append([InlineKeyboardButton(text=test_label, callback_data=f"admin:test:{prompt_id}")])
            rows.append([InlineKeyboardButton(text="Tags", callback_data=f"admin:editpart:tags:{prompt_id}")])
            # В юзерском меню (owner_tg_id задан) не показываем Deactivate/Activate,
            # чтобы не дублировать управление доступностью с Make Public/Make Private.
            if owner_tg_id is None:
                rows.append([
                    InlineKeyboardButton(
                        text="Deactivate" if is_active else "Activate",
                        callback_data=f"admin:active:{prompt_id}",
                    ),
                ])
            rows.append([InlineKeyboardButton(text="Edit", callback_data=f"admin:edit:{prompt_id}")])

            if owner_tg_id is not None:
                # Toggle public/private for user prompts
                label = "🔒 Make Private" if is_public else "🟢 Make Public"
                rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:toggle_public:{prompt_id}")])
        else:
            # Community admin view: only Test (Free) and Generate (Free)
            rows.append([
                InlineKeyboardButton(text="🚀 Generate (Free)", callback_data=f"prompt:select:{prompt_id}"),
            ])
            rows.append([InlineKeyboardButton(text="Test (Free)", callback_data=f"admin:test:{prompt_id}")])
        
        rows.append([InlineKeyboardButton(text="Export JSON", callback_data=f"admin:export:{prompt_id}")])

    if (show_clone if show_clone is not None else is_admin_view) and not is_draft:
        rows.append([InlineKeyboardButton(text="Clone to All (System)", callback_data=f"admin:clone:{prompt_id}")])

    rows.append([InlineKeyboardButton(text="Delete", callback_data=f"admin:delete:{prompt_id}")])
    
    # Navigation back
    if back_callback is not None:
        back_cb = back_callback
    elif is_admin_view and owner_tg_id:
        back_cb = "admin:pw:users:0"
    elif owner_tg_id:
        back_cb = "menu:my_prompts:0"
    else:
        back_cb = "admin:pw:list"
        
    rows.append([InlineKeyboardButton(text="◀ Back", callback_data=back_cb)])
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
    # Back: владелец возвращается в свою карточку (My prompts), админ — в admin:pw:item.
    back_cb = f"menu:my_prompt_item:{prompt_id}"
    rows.append([InlineKeyboardButton(text="Back", callback_data=back_cb)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_menu(prompt_id: int, back_callback: str = "admin:pw:list") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Description", callback_data=f"admin:editpart:description:{prompt_id}")],
            [InlineKeyboardButton(text="Change title", callback_data=f"admin:editpart:title:{prompt_id}")],
            [InlineKeyboardButton(text="Change template", callback_data=f"admin:editpart:template:{prompt_id}")],
            [InlineKeyboardButton(text="Replace ref. image", callback_data=f"admin:editpart:ref:set:{prompt_id}")],
            [InlineKeyboardButton(text="Remove ref. image", callback_data=f"admin:editpart:ref:clear:{prompt_id}")],
            [InlineKeyboardButton(text="Examples (1–3)", callback_data=f"admin:editpart:examples:{prompt_id}")],
            [InlineKeyboardButton(text="Test", callback_data=f"admin:test:{prompt_id}")],
            [InlineKeyboardButton(text="Back to list", callback_data=back_callback)],
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
    """All tags with 🟢 (assigned) / 🔴 (not assigned); click toggles; pagination 20 per page."""
    rows: list[list[InlineKeyboardButton]] = []

    # Сначала — специальный тег "Main Menu" (если есть)
    main_menu_rows: list[list[InlineKeyboardButton]] = []
    other_rows: list[list[InlineKeyboardButton]] = []

    for t in tags:
        name_raw = str(t.get("name") or "")
        # Тег "Users" нельзя выбирать при редактировании промпта
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

    # Main Menu сверху, затем остальные теги в исходном порядке
    rows.extend(main_menu_rows)
    rows.extend(other_rows)

    # Кнопка добавления тега (доступна и админу, и владельцу промпта)
    rows.append(
        [InlineKeyboardButton(text="➕ Add tag", callback_data=f"admin:editpart:tag_add:{prompt_id}:{page}")]
    )

    base_cb = f"admin:editpart:tags:{prompt_id}"
    back_cb = back_callback or f"admin:pw:item:{prompt_id}"
    rows.extend(_pagination_buttons(page, total, base_cb, back_cb))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_edit_variables_menu(prompt_id: int, variables: list[dict[str, str]]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for idx, var in enumerate(variables):
        # Читабельные имена переменных: CHARACTER_POSITION → "Character position"
        label = pretty_variable_label(var.get("name", ""), max_length=25)
        text = label or variable_token(var)
        rows.append(
            [InlineKeyboardButton(text=text, callback_data=f"admin:editvar:pick:{prompt_id}:{idx}")]
        )
    # Кнопка добавления новой переменной
    rows.append([InlineKeyboardButton(text="➕ Add variable", callback_data=f"admin:editvar:add:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Back to prompt edit", callback_data=f"admin:edit:{prompt_id}")])
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
        rows.append(
            [
                InlineKeyboardButton(text="My own: ON", callback_data=f"admin:editvar:allow:{prompt_id}:{var_idx}:yes"),
                InlineKeyboardButton(text="My own: OFF", callback_data=f"admin:editvar:allow:{prompt_id}:{var_idx}:no"),
            ]
        )
    # Для владельца промпта Back ведёт в карточку промпта (My prompts),
    # для чисто админского режима — в список переменных/промптов.
    back_cb = f"menu:my_prompt_item:{prompt_id}" if is_owner_view else f"admin:editpart:variables:{prompt_id}"
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
