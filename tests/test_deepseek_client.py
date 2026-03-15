"""
Tests for DeepSeekClient (Evolink chat/completions → feach.json format).
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.deepseek_client import (
    DeepSeekClient,
    _ensure_feach_shape,
    _extract_json_from_content,
)


# --- Helpers: valid feach-shaped payload (5–8 features) ---

def _make_feach_response(idea: str, num_extra_features: int = 4) -> dict:
    """Build a response dict that matches jsons/feach.json structure."""
    features = {
        "style": {
            "varname": "style",
            "about": "Визуальный стиль изображения.",
            "options": {"option1": "документальный", "option2": "аниме", "option3": "хоррор"},
        },
    }
    for i in range(1, num_extra_features + 1):
        key = f"feature{i}"
        features[key] = {
            "varname": f"FEATURE_{i}",
            "about": f"Пояснение для переменной {i}.",
            "options": {"option1": "вариант A", "option2": "вариант B", "option3": "вариант C"},
        }
    return {"idea": idea, "features": features}


def _chat_completion_body(content: str) -> dict:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}},
        ],
    }


# --- _extract_json_from_content ---


def test_extract_json_plain():
    data = _make_feach_response("Астронавт на Луне", num_extra_features=4)
    raw = json.dumps(data, ensure_ascii=False)
    assert _extract_json_from_content(raw) == data


def test_extract_json_inside_markdown():
    data = _make_feach_response("Test idea")
    raw = json.dumps(data)
    wrapped = "```json\n" + raw + "\n```"
    assert _extract_json_from_content(wrapped) == data


def test_extract_json_markdown_no_lang():
    data = {"idea": "x", "features": {"style": {"varname": "s", "about": "a", "options": {"option1": "o1"}}}}
    # 5 features required; add 4 more to have 5 total
    data["features"]["feature1"] = {"varname": "f1", "about": "a", "options": {"option1": "a", "option2": "b", "option3": "c"}}
    data["features"]["feature2"] = {"varname": "f2", "about": "a", "options": {"option1": "a", "option2": "b", "option3": "c"}}
    data["features"]["feature3"] = {"varname": "f3", "about": "a", "options": {"option1": "a", "option2": "b", "option3": "c"}}
    data["features"]["feature4"] = {"varname": "f4", "about": "a", "options": {"option1": "a", "option2": "b", "option3": "c"}}
    raw = json.dumps(data)
    wrapped = "```\n" + raw + "\n```"
    assert _extract_json_from_content(wrapped) == data


def test_extract_json_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        _extract_json_from_content("not json at all")


# --- _ensure_feach_shape ---


def test_ensure_feach_shape_valid():
    data = _make_feach_response("Refined idea", num_extra_features=4)
    out = _ensure_feach_shape(data)
    assert out["idea"] == "Refined idea"
    assert "style" in out["features"]
    assert len(out["features"]) == 5


def test_ensure_feach_shape_max_features():
    data = _make_feach_response("Idea", num_extra_features=7)
    out = _ensure_feach_shape(data)
    assert len(out["features"]) == 8


def test_ensure_feach_shape_missing_idea_raises():
    data = {"features": _make_feach_response("x")["features"]}
    with pytest.raises(ValueError, match="idea"):
        _ensure_feach_shape(data)


def test_ensure_feach_shape_missing_features_raises():
    with pytest.raises(ValueError, match="features"):
        _ensure_feach_shape({"idea": "x"})


def test_ensure_feach_shape_no_style_raises():
    data = {"idea": "x", "features": {"feature1": {"varname": "f1", "about": "a", "options": {"option1": "a", "option2": "b", "option3": "c"}}}}
    for i in range(2, 6):
        data["features"][f"feature{i}"] = {"varname": f"f{i}", "about": "a", "options": {"option1": "a", "option2": "b", "option3": "c"}}
    with pytest.raises(ValueError, match="style"):
        _ensure_feach_shape(data)


def test_ensure_feach_shape_too_few_features_raises():
    data = {"idea": "x", "features": {"style": {"varname": "s", "about": "a", "options": {"option1": "a", "option2": "b", "option3": "c"}}}}
    with pytest.raises(ValueError, match="5 and 8"):
        _ensure_feach_shape(data)


def test_ensure_feach_shape_too_many_features_raises():
    data = _make_feach_response("x", num_extra_features=8)
    with pytest.raises(ValueError, match="5 and 8"):
        _ensure_feach_shape(data)


# --- DeepSeekClient ---


@pytest.fixture
def client():
    return DeepSeekClient(api_key="test-key", api_base_url="https://api.evolink.ai")


def test_client_init_requires_key():
    with patch.dict("os.environ", {"API_KEY": ""}, clear=False):
        with pytest.raises(ValueError, match="API_KEY"):
            DeepSeekClient(api_key="")
    DeepSeekClient(api_key="ok")


@pytest.mark.asyncio
async def test_refine_idea_empty_raises(client):
    with pytest.raises(ValueError, match="non-empty"):
        await client.refine_idea("")
    with pytest.raises(ValueError, match="non-empty"):
        await client.refine_idea("   ")


@pytest.mark.asyncio
async def test_refine_idea_returns_feach_structure(client):
    idea = "Астронавт на луне"
    feach = _make_feach_response("Астронавт на Луне в документальном стиле.", num_extra_features=5)
    content = json.dumps(feach, ensure_ascii=False)
    body = _chat_completion_body(content)

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.content_type = "application/json"
    mock_resp.text = AsyncMock(return_value=json.dumps(body))
    mock_resp.json = AsyncMock(return_value=body)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession.post", return_value=mock_resp):
        result = await client.refine_idea(idea)

    assert result["idea"] == feach["idea"]
    assert "style" in result["features"]
    assert len(result["features"]) == 6
    for key, feat in result["features"].items():
        assert "varname" in feat and "about" in feat and "options" in feat
        assert isinstance(feat["options"], dict)


@pytest.mark.asyncio
async def test_refine_idea_api_error_raises(client):
    mock_resp = MagicMock()
    mock_resp.status = 500
    mock_resp.text = AsyncMock(return_value="Server error")
    mock_resp.content_type = "text/plain"
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession.post", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="500"):
            await client.refine_idea("Астронавт на луне")


@pytest.mark.asyncio
async def test_refine_idea_invalid_json_in_response_raises(client):
    body = _chat_completion_body("not valid json {")
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.content_type = "application/json"
    mock_resp.text = AsyncMock(return_value=json.dumps(body))
    mock_resp.json = AsyncMock(return_value=body)
    mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_resp.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession.post", return_value=mock_resp):
        with pytest.raises((ValueError, json.JSONDecodeError)):
            await client.refine_idea("Test idea")
