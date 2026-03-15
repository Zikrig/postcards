"""Inline keyboard builders for menus."""
from typing import Any

import asyncpg
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from utils import btn_label, get_feach_option_enabled, variable_token


def build_main_menu(prompts: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=btn_label(str(p["title"]), 20), callback_data=f"prompt:select:{p['id']}")]
        for p in prompts
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def build_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Prompt work", callback_data="admin:prompt_work")],
            [InlineKeyboardButton(text="Promo codes", callback_data="admin:promo_menu")],
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


def build_prompt_list_menu(prompts: list[asyncpg.Record]) -> InlineKeyboardMarkup:
    rows = []
    for p in prompts:
        active = p.get("is_active", True)
        label = btn_label(f"{'🟢' if active else '🔴'} {p['title']}", 20)
        rows.append([InlineKeyboardButton(text=label, callback_data=f"admin:pw:item:{p['id']}")])
    rows.append([InlineKeyboardButton(text="Back", callback_data="admin:prompt_work")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_item_menu(prompt_id: int, is_active: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="Edit", callback_data=f"admin:edit:{prompt_id}")],
        [InlineKeyboardButton(
            text="Deactivate" if is_active else "Activate",
            callback_data=f"admin:active:{prompt_id}",
        )],
        [InlineKeyboardButton(text="Export JSON", callback_data=f"admin:export:{prompt_id}")],
        [InlineKeyboardButton(text="Delete", callback_data=f"admin:delete:{prompt_id}")],
        [InlineKeyboardButton(text="Back to list", callback_data="admin:pw:list")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_prompt_feach_menu(prompt_id: int, feach_data: dict[str, Any], is_active: bool) -> InlineKeyboardMarkup:
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
        InlineKeyboardButton(
            text="Deactivate" if is_active else "Activate",
            callback_data=f"admin:active:{prompt_id}",
        ),
    ])
    rows.append([InlineKeyboardButton(text="Export JSON", callback_data=f"admin:export:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Edit", callback_data=f"admin:edit:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Test", callback_data=f"admin:test:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Delete", callback_data=f"admin:delete:{prompt_id}")])
    rows.append([InlineKeyboardButton(text="Back to list", callback_data="admin:pw:list")])
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
