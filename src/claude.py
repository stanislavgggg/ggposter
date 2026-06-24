"""Слой 2: вызов Claude для генерации поста. Возвращает распарсенный JSON."""
import os
import json
import re

from .config import load_prompt

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic  # lazy
        _client = Anthropic()  # ключ из ANTHROPIC_API_KEY
    return _client


def _strip_fences(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def generate_post(event, geo, sport):
    """event/geo/sport — dict'ы. Возвращает dict по схеме prompts/post_generation.md."""
    system = load_prompt("post_generation")
    user_payload = {
        "event": event,
        "geo": geo,
        "sport": {
            "sport": sport.get("sport"),
            "sport_emoji": sport.get("sport_emoji"),
            "angle_hints": sport.get("angle_hints", []),
        },
    }
    resp = _get_client().messages.create(
        model=MODEL,
        max_tokens=1500,
        system=system,
        messages=[{
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False),
        }],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(_strip_fences(text)), MODEL
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude вернул невалидный JSON: {e}\n---\n{text[:500]}")
