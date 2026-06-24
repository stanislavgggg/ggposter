"""
Слой 3 — публикация Telegram-поста в ОДНО выбранное назначение (канал/паблик).

Без авторассылки: менеджер сам выбирает, куда уходит пост. Один драфт можно
опубликовать в несколько назначений — по одному вызову на каждое.

- Собирает финальный текст (ручная правка важнее выбранного варианта).
- Подставляет {{CTA_LINK}} (affiliate_url + per-post-per-channel subid) и {{PROMO_CODE}}.
- Идемпотентно по dest["id"]: повторная публикация в то же назначение — no-op.
- Копит историю публикаций в draft["published_to"] (+ published_links для writeback).
"""
import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from .store import get_store
from .telegram import Telegram
from .config import load_geo, telegram_destinations


def _final_text(draft):
    if draft.get("edited_text"):
        return draft["edited_text"]
    return draft["post_b"] if draft.get("chosen_variant") == "B" else draft["post_a"]


def _tracking_url(offer, subid):
    base = offer["affiliate_url"]
    param = offer.get("subid_param", "subid")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode({param: subid})}"


def _first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _msg_link(chat_id, msg_id):
    cid = str(chat_id)
    if cid.startswith("@"):
        return f"https://t.me/{cid.lstrip('@')}/{msg_id}"
    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/{msg_id}"
    return ""


def load_published_to(draft):
    """История публикаций драфта по назначениям -> list[dict]."""
    raw = draft.get("published_to")
    if not raw:
        return []
    if isinstance(raw, list):
        return list(raw)
    try:
        return json.loads(raw)
    except Exception:
        return []


def _resolve_dest(geo, draft, dest):
    """Превратить dest (dict | id | slug | None) в полноценный {id,label,slug}.
    dest=None и ровно одно назначение -> оно. Иначе (несколько) -> ошибка:
    выбор обязателен (никакой авторассылки)."""
    dests = telegram_destinations(geo)
    if isinstance(dest, dict) and dest.get("id"):
        return dest
    if dest is not None:
        token = str(dest).strip().casefold()
        for d in dests:
            if token in (str(d["id"]).casefold(), d["slug"].casefold(), d["label"].casefold()):
                return d
        raise RuntimeError(f"назначение '{dest}' не найдено в config/geo/{draft['geo']}.yaml")
    if len(dests) == 1:
        return dests[0]
    if not dests:
        raise RuntimeError(f"для гео {draft['geo']} не задано ни одного назначения Telegram")
    raise RuntimeError("несколько назначений — выбор обязателен (авторассылка отключена)")


def publish_to_destination(draft_id, dest=None, tg=None):
    """Опубликовать одобренный драфт в одно назначение. Возвращает запись публикации."""
    store = get_store()
    draft = store.get_draft(draft_id)
    if not draft:
        raise RuntimeError(f"draft {draft_id} not found")

    geo = load_geo(draft["geo"])
    dest = _resolve_dest(geo, draft, dest)

    published_to = load_published_to(draft)
    existing = next((p for p in published_to if str(p.get("dest_id")) == str(dest["id"])), None)
    if existing:
        return existing                      # уже постили сюда — идемпотентность

    offer = geo["offer"]
    subid = f"tg_{draft['geo']}_{dest['slug']}_{draft['draft_id']}"

    text = _final_text(draft)
    text = text.replace("{{CTA_LINK}}", _tracking_url(offer, subid))
    text = text.replace("{{PROMO_CODE}}", offer.get("promo_code", ""))

    tg = tg or Telegram()
    msg = tg.send_message(dest["id"], text, parse_mode=None)
    msg_id = msg.get("message_id")

    record = {"dest_id": str(dest["id"]), "label": dest["label"], "slug": dest["slug"],
              "subid": subid, "msg_id": msg_id, "link": _msg_link(dest["id"], msg_id),
              "at": datetime.now(timezone.utc).isoformat()}
    published_to.append(record)
    links = ", ".join(p["link"] for p in published_to if p.get("link"))

    store.update_draft(
        draft_id,
        status="published",
        subid=draft.get("subid") or subid,          # верхний subid = первый (для петли обучения)
        hook=draft.get("hook") or _first_line(text),
        published_text=text,
        published_at=record["at"],
        published_msg_id=msg_id,
        published_to=json.dumps(published_to, ensure_ascii=False),
        published_links=links,
    )
    return record


# Обратная совместимость: старое имя. dest обязателен при нескольких назначениях.
def publish_draft(draft_id, tg=None, dest=None):
    return publish_to_destination(draft_id, dest=dest, tg=tg)
