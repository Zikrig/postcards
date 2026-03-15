"""Prompt export and variable_descriptions from features."""
from typing import Any

from utils import (
    ensure_dict,
    ensure_unique_option_key,
    extract_variables,
    make_option_key,
    variable_token,
)


def build_prompt_export_payload(prompt_record: Any) -> dict[str, Any]:
    """
    Build export JSON: title, template, idea, features (feach-like).
    No reference_photo_file_id, feach_data, example_file_ids, is_active.
    """
    title = str(prompt_record.get("title") or "")
    template = str(prompt_record.get("template") or "")
    var_desc = ensure_dict(prompt_record.get("variable_descriptions") or {})
    variables = extract_variables(template)
    features: dict[str, Any] = {}
    for var in variables:
        if var["type"] == "image":
            continue
        token = variable_token(var)
        raw = var_desc.get(token)
        if isinstance(raw, str):
            config = {"description": raw, "options": [], "allow_custom": True}
        elif isinstance(raw, dict):
            config = {
                "description": str(raw.get("description") or ""),
                "options": [str(x) for x in (raw.get("options") or []) if str(x).strip()],
                "allow_custom": bool(raw.get("allow_custom", True)),
            }
        else:
            config = {"description": "", "options": [], "allow_custom": True}
        key = var["name"].lower().replace(" ", "_")
        options_obj: dict[str, str] = {}
        used_keys: set[str] = set()
        for opt in config["options"]:
            base_key = make_option_key(opt, max_length=20)
            opt_key = ensure_unique_option_key(base_key, used_keys, max_length=20)
            used_keys.add(opt_key)
            options_obj[opt_key] = opt
        features[key] = {
            "varname": var["name"],
            "about": config["description"],
            "options": options_obj,
            "my_own": config["allow_custom"],
        }
    return {"title": title, "template": template, "idea": "", "features": features}


def variable_descriptions_from_features(template: str, features: dict[str, Any]) -> dict[str, Any]:
    """
    Build variable_descriptions from template placeholders and feach-like features.
    [USER_PHOTO] and other [] variables are always image type; features only for text <NAME>.
    """
    variables = extract_variables(template)
    var_desc: dict[str, Any] = {}
    for var in variables:
        token = variable_token(var)
        if var["type"] == "image":
            var_desc[token] = {
                "description": "",
                "options": [],
                "allow_custom": True,
                "type": "image",
            }
            continue
        key = var["name"].lower().replace(" ", "_")
        feat = (features or {}).get(key)
        if not feat or not isinstance(feat, dict):
            name_upper = var["name"].upper().replace(" ", "_")
            for f in (features or {}).values():
                if isinstance(f, dict) and (f.get("varname") or "").upper().replace(" ", "_") == name_upper:
                    feat = f
                    break
        if feat and isinstance(feat, dict):
            about = str(feat.get("about") or "")
            opts = feat.get("options")
            if isinstance(opts, dict):
                opts = [str(v) for v in opts.values() if str(v).strip()]
            elif isinstance(opts, list):
                opts = [str(x) for x in opts if str(x).strip()]
            else:
                opts = []
            var_desc[token] = {
                "description": about,
                "options": opts,
                "allow_custom": bool(feat.get("my_own", True)),
                "type": "text",
            }
        else:
            var_desc[token] = {
                "description": "",
                "options": [],
                "allow_custom": True,
                "type": "text",
            }
    return var_desc
