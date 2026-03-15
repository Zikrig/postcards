"""
Client for Evolink chat/completions (DeepSeek) to refine a photo idea
and get structured features (style + variables) in feach.json format.
"""
import json
import os
import re
from typing import Any, Optional

import aiohttp
from dotenv import load_dotenv

LOAD_FEACH_PROMPT = """You are an assistant that helps turn a short photo idea into a structured brief for image generation.

LANGUAGE: Use English only for all output (idea, about, option texts). Regardless of the user's input language, respond in English.

RULES:
- Reply ONLY with valid JSON. No markdown, no code fence, no extra text.
- The JSON must have exactly two top-level keys: "idea" and "features".

1) "idea" (string): Slightly refined and clear one-sentence description of the image idea, in English.

2) "features" (object): Between 5 and 8 feature keys. One key MUST be "style". The rest can be "feature1", "feature2", "feature3", etc.

   For "style": suggest 3–5 visual styles (e.g. documentary, anime, horror, photorealistic). In "about" briefly explain what this variable controls, in English.

   Do NOT add a feature for "who is in the photo" or "person/character" with text options (e.g. "I", "my friend"). The person in the scene is always the user's reference photo ([USER_PHOTO]), not a variable choice.

   For each other feature: think of concrete variable aspects of the scene (e.g. what the astronaut plants, what is in the sky, background object). Each feature has:
   - "varname": short Latin/keyboard-friendly name for the variable (e.g. FLAG_OBJECT, SKY_CONTENT).
   - "about": short explanation for the user, in English.
   - "options": object with keys "option1", "option2", "option3" (and optionally "option4", "option5"). Each value is a short option text in English.

Structure (strict):
{
  "idea": "<refined idea string>",
  "features": {
    "style": {
      "varname": "style",
      "about": "<explanation>",
      "options": { "option1": "", "option2": "", "option3": "" }
    },
    "feature1": { "varname": "", "about": "", "options": { "option1": "", "option2": "", "option3": "" } },
    ...
  }
}

Return only this JSON, nothing else."""


FINAL_PROMPT_SYSTEM = """You are an assistant that generates a detailed, multi-paragraph image-generation prompt template from an idea and a list of variables.

LANGUAGE: Use English only. The template and all variable_descriptions (descriptions, options) must be in English. No other language is allowed.

RULES:
- Reply ONLY with valid JSON. No markdown, no code fence.
- JSON has two keys: "template" and "variable_descriptions".

1) "template" (string): A LONG, STRUCTURED prompt in English (several paragraphs) that will be sent to an image model.
   - Use placeholders: [VARNAME] for image (e.g. [USER_PHOTO]), <VARNAME> for text (e.g. <STYLE>).
   - Whenever the user's reference photo or the person from that photo is mentioned, use exactly [USER_PHOTO] in square brackets (e.g. "the person from [USER_PHOTO]", "Use [USER_PHOTO] as the appearance of the real person", "standing next to [USER_PHOTO]").
   - Structure the template with clear sections on separate lines, for example:
     • Opening line: "Use the attached reference photo [USER_PHOTO] as the appearance of the real person." or similar.
     • Scene: (short scene description, use <STYLE> and other text variables where relevant)
     • Characters: (who appears, how they interact; mention [USER_PHOTO] when referring to the person from the photo)
     • Style: (visual style, e.g. cartoon vs realistic; use variables like <STYLE>)
     • Environment: (setting, location, background)
     • Composition: (framing, group shot, etc.)
     • Lighting and integration: (how the person from [USER_PHOTO] blends into the scene)
     • Mood: (atmosphere, tone)
     • Quality: (resolution, colors, etc.)
   - Each section can be 1–3 sentences. Total template length: at least 150 words. Use newlines between sections.
   - Include [USER_PHOTO] multiple times where it makes sense (e.g. in Scene, Characters, Style, Lighting). Do not explain; output only the template string.

2) "variable_descriptions" (object): Keys are the placeholder strings exactly as in the template ([VARNAME] or <VARNAME>). Each value is an object:
   - "description": short user-facing text in English
   - "options": array of strings in English (empty [] if free text only)
   - "allow_custom": boolean
   - "type": "text" or "image"

Return only this JSON. All text must be in English."""


def _ensure_feach_shape(data: Any) -> dict[str, Any]:
    """Ensure response has idea + features with required keys per feature."""
    if not isinstance(data, dict):
        raise ValueError("Response must be a JSON object")
    idea = data.get("idea")
    if idea is None:
        raise ValueError("Missing 'idea'")
    features = data.get("features")
    if not isinstance(features, dict):
        raise ValueError("'features' must be an object")
    if "style" not in features:
        raise ValueError("'features' must contain 'style'")
    if not (5 <= len(features) <= 8):
        raise ValueError("'features' must have between 5 and 8 keys")

    for key, val in features.items():
        if not isinstance(val, dict):
            raise ValueError(f"feature '{key}' must be an object")
        if "varname" not in val or "about" not in val or "options" not in val:
            raise ValueError(f"feature '{key}' must have varname, about, options")
        opts = val["options"]
        if not isinstance(opts, dict):
            raise ValueError(f"feature '{key}.options' must be an object")
        for k, v in opts.items():
            if not isinstance(v, str):
                opts[k] = str(v) if v is not None else ""

    return {"idea": str(idea), "features": features}


def _extract_json_from_content(content: str) -> dict[str, Any]:
    """Extract JSON from model response (strip markdown if present)."""
    text = (content or "").strip()
    # Remove optional markdown code block
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()
    return json.loads(text)


class DeepSeekClient:
    """Evolink chat/completions client for DeepSeek (idea → feach.json structure)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base_url: Optional[str] = None,
        model: str = "deepseek-chat",
    ):
        load_dotenv()
        self.api_key = (api_key or os.getenv("API_KEY", "")).strip()
        self.api_base_url = (api_base_url or os.getenv("API_BASE_URL", "https://api.evolink.ai")).rstrip("/")
        self.model = model
        if not self.api_key:
            raise ValueError("API_KEY is required (env or constructor)")

    @property
    def _url(self) -> str:
        return f"{self.api_base_url}/v1/chat/completions"

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def refine_idea(self, idea: str) -> dict[str, Any]:
        """
        Send the photo idea to DeepSeek; returns structure matching jsons/feach.json.
        - idea: short user idea (e.g. "Астронавт на луне")
        - Returns: dict with "idea" (refined) and "features" (style + feature1.., each with varname, about, options).
        """
        idea = (idea or "").strip()
        if not idea:
            raise ValueError("idea must be non-empty")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": LOAD_FEACH_PROMPT},
                {"role": "user", "content": idea},
            ],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._url,
                headers=self._headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"DeepSeek API error [{resp.status}]: {text}")

                data = await resp.json() if resp.content_type == "application/json" else json.loads(text)
                content = None
                for choice in data.get("choices") or []:
                    msg = choice.get("message") or {}
                    if "content" in msg:
                        content = msg["content"]
                        break
                if content is None:
                    raise ValueError("No content in chat completion response")

        raw = _extract_json_from_content(content)
        return _ensure_feach_shape(raw)

    async def generate_final_prompt(
        self,
        idea: str,
        variables_spec: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        Generate final template and variable_descriptions from idea and configured variables.
        variables_spec: list of {
          "name": str (Latin name),
          "type": "text" | "image",
          "constant": str | None (if set, this variable is replaced by this value; no placeholder),
          "options": list[str] | None (if not constant: choices),
          "allow_custom": bool,
          "about": str (user-facing description),
        }
        Optional: one variable with name like NUM_PEOPLE / PEOPLE_FROM_PHOTO for "how many people from user photo".
        Returns: {"template": str, "variable_descriptions": dict} with keys [VAR] / <VAR>.
        """
        idea = (idea or "").strip()
        if not idea:
            raise ValueError("idea must be non-empty")

        spec_text = json.dumps(
            {"idea": idea, "variables": variables_spec},
            ensure_ascii=False,
            indent=2,
        )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": FINAL_PROMPT_SYSTEM},
                {"role": "user", "content": spec_text},
            ],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._url,
                headers=self._headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"DeepSeek API error [{resp.status}]: {text}")

                data = (
                    await resp.json()
                    if resp.content_type == "application/json"
                    else json.loads(text)
                )
                content = None
                for choice in data.get("choices") or []:
                    msg = choice.get("message") or {}
                    if "content" in msg:
                        content = msg["content"]
                        break
                if content is None:
                    raise ValueError("No content in chat completion response")

        raw = _extract_json_from_content(content)
        if not isinstance(raw, dict) or "template" not in raw or "variable_descriptions" not in raw:
            raise ValueError("Response must contain template and variable_descriptions")
        return {
            "template": str(raw["template"]),
            "variable_descriptions": raw.get("variable_descriptions") or {},
        }
