"""Shared helpers: template variables, feach normalization, option keys."""
import json
import re
from typing import Any

VARIABLE_RE = re.compile(r"\[([^\[\]<>]+)\]|<([^<>]+)>")


def extract_variables(template: str) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for m in VARIABLE_RE.finditer(template):
        if m.group(1):
            var_name = m.group(1).strip()
            var_type = "image"
        else:
            var_name = (m.group(2) or "").strip()
            var_type = "text"

        key = (var_name, var_type)
        if var_name and key not in seen:
            values.append({"name": var_name, "type": var_type})
            seen.add(key)
    return values


def variable_token(variable: dict[str, str]) -> str:
    name = variable["name"]
    if variable["type"] == "image":
        return f"[{name}]"
    return f"<{name}>"


def pretty_variable_label(name: str, max_length: int = 25) -> str:
    """
    Human‑readable label from VARIABLE_NAME:
    - CHARACTER_POSITION → "Character position"
    - Max length limited to max_length.
    """
    s = (name or "").strip()
    if not s:
        return ""
    # Replace separators with spaces and normalize whitespace
    s = re.sub(r"[_\-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Lowercase then capitalize first letter
    s = s.lower()
    s = s[0].upper() + s[1:]
    if len(s) > max_length:
        s = s[:max_length]
    return s


def render_prompt(template: str, answers: dict[str, str]) -> str:
    result = template
    for key, value in answers.items():
        result = result.replace(f"[{key}]", value)
        result = result.replace(f"<{key}>", value)
    return result


def ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def normalize_feach_for_storage(api_feach: dict[str, Any]) -> dict[str, Any]:
    """Convert DeepSeek feach response to storage format: options as {text, enabled}, my_own, custom."""
    idea = api_feach.get("idea", "")
    features = api_feach.get("features") or {}
    out_features: dict[str, Any] = {}
    for key, feat in features.items():
        if not isinstance(feat, dict):
            continue
        opts = feat.get("options") or {}
        if not isinstance(opts, dict):
            opts = {}
        normalized_opts: dict[str, Any] = {}
        used_opt_keys: set[str] = set()
        for opt_k, opt_v in opts.items():
            if isinstance(opt_v, dict) and "text" in opt_v:
                text = str(opt_v.get("text", ""))
                enabled = bool(opt_v.get("enabled", True))
            else:
                text = str(opt_v) if opt_v is not None else ""
                enabled = True
            base_key = make_option_key(text, max_length=20)
            norm_key = ensure_unique_option_key(base_key, used_opt_keys, max_length=20)
            used_opt_keys.add(norm_key)
            normalized_opts[norm_key] = {"text": text, "enabled": enabled}
        out_features[key] = {
            "varname": feat.get("varname", key),
            "about": feat.get("about", ""),
            "options": normalized_opts,
            "my_own": feat.get("my_own", True),
            "custom": list(feat.get("custom") or []),
        }

    # Ensure character_position is present (explicitly requested)
    # We check both by key and by varname
    has_char_pos = any(
        (f.get("varname") or k).upper().replace(" ", "_") == "CHARACTER_POSITION"
        for k, f in out_features.items()
    )
    if not has_char_pos:
        out_features["character_position"] = {
            "varname": "CHARACTER_POSITION",
            "about": "Position or pose of the main character",
            "options": {
                "facing_camera": {"text": "facing the camera", "enabled": True},
                "back_to_camera": {"text": "back to camera", "enabled": True},
                "looking_left": {"text": "looking left", "enabled": True},
                "looking_right": {"text": "looking right", "enabled": True},
                "profile_view": {"text": "profile view", "enabled": True},
                "in_dialogue": {"text": "in dialogue with someone", "enabled": True},
            },
            "my_own": True,
            "custom": [],
        }

    return {"idea": idea, "features": out_features}


def get_feach_option_text(opt_value: Any) -> str:
    if isinstance(opt_value, dict) and "text" in opt_value:
        return str(opt_value.get("text", ""))
    return str(opt_value) if opt_value is not None else ""


def get_feach_option_enabled(opt_value: Any) -> bool:
    if isinstance(opt_value, dict):
        return bool(opt_value.get("enabled", True))
    return True


def make_option_key(text: str, max_length: int = 20) -> str:
    """Осмысленный ключ опции из текста: только a-z, 0-9, _, не длиннее max_length (для JSON и callback_data)."""
    s = (text or "").strip().lower()
    if not s:
        return "opt"
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    if not s:
        return "opt"
    return s[:max_length].rstrip("_")


def btn_label(text: str, max_length: int = 20) -> str:
    """Подпись кнопки не длиннее max_length символов."""
    s = (text or "").strip()
    return s[:max_length] if len(s) > max_length else s


def ensure_unique_option_key(base_key: str, existing: set[str], max_length: int = 20) -> str:
    """Уникальный ключ: base_key или base_key_2, base_key_3, ... (укладываемся в max_length)."""
    key = (base_key or "opt")[:max_length]
    if key not in existing:
        return key
    for i in range(2, 100):
        suffix = f"_{i}"
        candidate = (base_key or "opt")[: max_length - len(suffix)] + suffix
        if candidate not in existing:
            return candidate
    return base_key[:max_length] + "_0"


def prompt_record_is_draft(prompt: Any) -> bool:
    """
    True while the prompt has not received a final template yet (DB fields only).
    Same notion as template ≈ feach idea / empty / placeholder.
    """
    feach_data_raw = {}
    if prompt is not None and hasattr(prompt, "get"):
        feach_data_raw = prompt.get("feach_data") or {}
    feach_data = ensure_dict(feach_data_raw)
    draft_idea = str(feach_data.get("idea") or "")
    template = str(prompt.get("template") or "") if prompt is not None and hasattr(prompt, "get") else ""
    return (template == draft_idea) or (not template) or (template == "Your prompt template here")
