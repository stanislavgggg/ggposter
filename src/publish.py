"""
Слой 3 — публикация.

- Берёт одобренный драфт, собирает финальный текст (ручная правка важнее выбранного варианта).
- Подставляет плейсхолдеры {{CTA_LINK}} и {{PROMO_CODE}}:
    {{CTA_LINK}}  -> affiliate_url + уникальный subid (tg_<geo>_<draft_id>) для per-post атрибуции.
    {{PROMO_CODE}} -> промокод оффера.
- Постит в публичный канал GEO, помечает published, сохраняет subid + hook (для петли обучения).
"""
from datetime import datetime, timezone
from urllib.parse import urlencode

from .store import get_store
from .telegram import Telegram
from .config import load_geo


def _final_text(draft):
    if draft.get("edited_text"):
        return draft["edited_text"]
    return draft["post_b"] if draft.get("chosen_variant") == "B" else draft["post_a"]


def _build_subid(draft):
    return f"tg_{draft['geo']}_{draft['draft_id']}"


def _tracking_url(offer, subid):
    base = offer["affiliate_url"]
    param = offer.get("subid_param", "subid")
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode({param: subid})}"


def _first_line(text):
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def publish_draft(draft_id, tg=None):
    store = get_store()
    draft = store.get_draft(draft_id)
    if not draft:
        raise RuntimeError(f"draft {draft_id} not found")
    if draft["status"] == "published":
        return draft.get("published_msg_id")

    geo = load_geo(draft["geo"])
    offer = geo["offer"]
    subid = _build_subid(draft)

    text = _final_text(draft)
    text = text.replace("{{CTA_LINK}}", _tracking_url(offer, subid))
    text = text.replace("{{PROMO_CODE}}", offer.get("promo_code", ""))

    tg = tg or Telegram()
    msg = tg.send_message(draft["channel_id"], text, parse_mode=None)

    store.update_draft(
        draft_id,
        status="published",
        subid=subid,
        hook=_first_line(text),
        published_text=text,
        published_at=datetime.now(timezone.utc).isoformat(),
        published_msg_id=msg["message_id"],
    )
    return msg["message_id"]
