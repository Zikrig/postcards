"""Helpers for step-by-step choices before DeepSeek «generate final template»."""
from typing import Any, Optional

from app.utils import ensure_dict, get_feach_option_enabled, get_feach_option_text


def enabled_option_texts(feat: dict[str, Any]) -> list[str]:
    opts = feat.get("options") or {}
    custom = list(feat.get("custom") or [])
    out: list[str] = []
    for _k, opt_v in opts.items():
        if get_feach_option_enabled(opt_v):
            t = get_feach_option_text(opt_v)
            if str(t).strip():
                out.append(str(t).strip())
    for c in custom:
        if isinstance(c, dict) and c.get("enabled", True):
            t = c.get("text", "")
            if str(t).strip():
                out.append(str(t).strip())
        elif isinstance(c, str) and str(c).strip():
            out.append(str(c).strip())
    return out


def should_ask_in_final_wizard(feat: dict[str, Any], enabled_opts: list[str]) -> bool:
    """Same filter as the old loop: skip features that would not be passed to DeepSeek."""
    my_own = feat.get("my_own", True)
    custom = list(feat.get("custom") or [])
    if not enabled_opts and not my_own and not custom:
        return False
    return True


def build_final_setup_steps(features: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Ordered list of wizard steps. Each item:
      feat_key, feat, enabled_opts, mode: 'pick' | 'freeform' (no presets, my_own)
    """
    steps: list[dict[str, Any]] = []
    for feat_key, feat in features.items():
        if not isinstance(feat, dict):
            feat = {}
        enabled_opts = enabled_option_texts(feat)
        if not should_ask_in_final_wizard(feat, enabled_opts):
            continue
        my_own = feat.get("my_own", True)
        custom = list(feat.get("custom") or [])
        if not enabled_opts and my_own:
            steps.append(
                {
                    "feat_key": feat_key,
                    "feat": feat,
                    "enabled_opts": [],
                    "mode": "freeform",
                }
            )
        else:
            steps.append(
                {
                    "feat_key": feat_key,
                    "feat": feat,
                    "enabled_opts": enabled_opts,
                    "mode": "pick",
                }
            )
    return steps


FREE_FORM_INCLUDE = "__freeform_include__"


def build_variables_spec_from_wizard_choices(
    steps: list[dict[str, Any]],
    choices: dict[str, Optional[str]],
) -> list[dict[str, Any]]:
    """
    After the wizard: choices[feat_key] is None (don't specify), str (picked text),
    or FREE_FORM_INCLUDE for «AI picks wording» on a no-presets variable.
    """
    variables_spec: list[dict[str, Any]] = [
        {
            "name": "USER_PHOTO",
            "type": "image",
            "constant": None,
            "options": None,
            "allow_custom": False,
            "about": "Reference photo of the person to integrate into the scene",
        },
    ]
    for step in steps:
        feat_key = step["feat_key"]
        feat = step["feat"]
        if not isinstance(feat, dict):
            feat = {}
        varname = (feat.get("varname") or feat_key).upper().replace(" ", "_")
        choice_val = choices.get(feat_key)

        if choice_val is None:
            continue
        if choice_val == FREE_FORM_INCLUDE:
            variables_spec.append(
                {
                    "name": varname,
                    "type": "text",
                    "constant": None,
                    "options": [],
                    "allow_custom": True,
                    "about": feat.get("about", ""),
                }
            )
            continue

        variables_spec.append(
            {
                "name": varname,
                "type": "text",
                "constant": str(choice_val),
                "options": None,
                "allow_custom": False,
                "about": feat.get("about", ""),
            }
        )
    return variables_spec


def build_variables_spec_legacy_no_wizard(features: dict[str, Any]) -> list[dict[str, Any]]:
    """Original behaviour when there are zero wizard steps (call DeepSeek immediately)."""
    variables_spec: list[dict[str, Any]] = [
        {
            "name": "USER_PHOTO",
            "type": "image",
            "constant": None,
            "options": None,
            "allow_custom": False,
            "about": "Reference photo of the person to integrate into the scene",
        },
    ]
    for feat_key, feat in features.items():
        if not isinstance(feat, dict):
            feat = {}
        enabled_opts = enabled_option_texts(feat)
        if not should_ask_in_final_wizard(feat, enabled_opts):
            continue
        varname = (feat.get("varname") or feat_key).upper().replace(" ", "_")
        my_own = feat.get("my_own", True)
        custom = list(feat.get("custom") or [])
        if len(enabled_opts) == 1 and not my_own and not custom:
            variables_spec.append(
                {
                    "name": varname,
                    "type": "text",
                    "constant": enabled_opts[0],
                    "options": None,
                    "allow_custom": False,
                    "about": feat.get("about", ""),
                }
            )
        else:
            variables_spec.append(
                {
                    "name": varname,
                    "type": "text",
                    "constant": None,
                    "options": enabled_opts,
                    "allow_custom": my_own,
                    "about": feat.get("about", ""),
                }
            )
    return variables_spec
