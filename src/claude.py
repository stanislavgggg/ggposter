"""Слой 2: вызовы Claude для постов и email через tool-use (надёжный структурированный вывод)."""
import os
import json

from .config import load_prompt

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
_client = None


def _get_client():
    global _client
    if _client is None:
        from anthropic import Anthropic  # lazy
        _client = Anthropic()  # ключ из ANTHROPIC_API_KEY
    return _client


# --- схемы структурированного вывода (гарантируют валидный объект, без парсинга строк) ---
_COMPLIANCE = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "notes": {"type": "string"}},
    "required": ["ok"],
}
POST_SCHEMA = {
    "type": "object",
    "properties": {
        "post_a": {"type": "string"},
        "post_b": {"type": "string"},
        "hook_rationale": {"type": "string"},
        "compliance": _COMPLIANCE,
    },
    "required": ["post_a", "post_b", "compliance"],
}
EMAIL_SCHEMA = {
    "type": "object",
    "properties": {
        "subject_a": {"type": "string"},
        "subject_b": {"type": "string"},
        "preview_text": {"type": "string"},
        "body_html": {"type": "string"},
        "hook_rationale": {"type": "string"},
        "compliance": _COMPLIANCE,
    },
    "required": ["subject_a", "subject_b", "body_html", "compliance"],
}


def _call(system, payload, max_tokens, schema, tool_name):
    """Форсируем tool-use -> модель возвращает готовый dict, JSON-строку не парсим."""
    resp = _get_client().messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        tools=[{"name": tool_name, "description": "Return the structured result.",
                "input_schema": schema}],
        tool_choice={"type": "tool", "name": tool_name},
    )
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
            return dict(block.input), MODEL
    raise RuntimeError(f"Claude не вернул tool_use '{tool_name}': {resp.content!r}"[:500])


def generate_post(event, geo, sport):
    """Telegram-пост. Без offer (бонус/промокод) в промпте — пост без бонуса."""
    geo_post = {k: v for k, v in geo.items() if k != "offer"}
    payload = {"event": event, "geo": geo_post, "sport": {
        "sport": sport.get("sport"), "sport_emoji": sport.get("sport_emoji"),
        "angle_hints": sport.get("angle_hints", [])}}
    return _call(load_prompt("post_generation"), payload, 1500, POST_SCHEMA, "emit_post")


def generate_email(event, geo, sport, audience="warm", funnel="prelander"):
    """Конверсионный email БЕЗ бонуса. offer (бонус/промокод) в промпт не передаём."""
    geo_email = {k: v for k, v in geo.items() if k != "offer"}
    payload = {"event": event, "geo": geo_email, "audience": audience, "funnel": funnel,
               "sport": {"sport": sport.get("sport"), "sport_emoji": sport.get("sport_emoji")}}
    return _call(load_prompt("email_generation"), payload, 2000, EMAIL_SCHEMA, "emit_email")
