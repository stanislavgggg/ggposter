"""Слой 2: вызовы Claude для постов и email. Возвращают распарсенный JSON."""
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


def _call(system, payload, max_tokens):
    resp = _get_client().messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        return json.loads(_strip_fences(text)), MODEL
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Claude вернул невалидный JSON: {e}\n---\n{text[:500]}")


def generate_post(event, geo, sport):
    """Telegram-пост. Схема -> prompts/post_generation.md."""
    payload = {"event": event, "geo": geo, "sport": {
        "sport": sport.get("sport"), "sport_emoji": sport.get("sport_emoji"),
        "angle_hints": sport.get("angle_hints", [])}}
    return _call(load_prompt("post_generation"), payload, max_tokens=1500)


def generate_email(event, geo, sport, audience="warm", funnel="prelander"):
    """Конверсионный email. Схема -> prompts/email_generation.md."""
    payload = {"event": event, "geo": geo, "audience": audience, "funnel": funnel,
               "sport": {"sport": sport.get("sport"), "sport_emoji": sport.get("sport_emoji")}}
    return _call(load_prompt("email_generation"), payload, max_tokens=2000)
