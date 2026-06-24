"""
Слой 3 — единая доставка. Маршрутизирует черновик по kind:
  telegram -> публикация в канал (publish.py)
  email    -> отправка через ESP (esp.py)

В обоих случаях подставляет {{CTA_LINK}} (+ per-post subid) и {{PROMO_CODE}},
помечает published, сохраняет subid + hook (для петли обучения).
"""
from datetime import datetime, timezone
from urllib.parse import urlencode

from .store import get_store
from .config import load_geo
from .publish import publish_draft        # telegram
from .esp import get_esp


def _tracking_url(offer, subid):
    base = offer["affiliate_url"]
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode({offer.get('subid_param', 'subid'): subid})}"


def send_email_draft(draft_id):
    store = get_store()
    draft = store.get_draft(draft_id)
    if not draft:
        raise RuntimeError(f"draft {draft_id} not found")
    if draft["status"] == "published":
        return draft.get("subid")

    geo = load_geo(draft["geo"])
    offer = geo["offer"]
    email_cfg = geo.get("email", {})
    subid = f"em_{draft['geo']}_{draft['draft_id']}"

    subject = draft.get("edited_subject") or (
        draft.get("subject_b") if draft.get("chosen_variant") == "B" else draft.get("subject_a"))
    html = draft.get("edited_text") or draft.get("body_html", "")
    html = html.replace("{{CTA_LINK}}", _tracking_url(offer, subid))
    html = html.replace("{{PROMO_CODE}}", offer.get("promo_code", ""))

    audience = draft.get("audience", "warm")
    audience_cfg = email_cfg.get(audience, email_cfg.get("warm", {}))

    res = get_esp().send(subject=subject, preview_text=draft.get("preview_text", ""),
                         html=html, audience_cfg=audience_cfg, subid=subid)

    store.update_draft(draft_id, status="published", subid=subid, hook=subject,
                       published_text=html,
                       published_at=datetime.now(timezone.utc).isoformat(),
                       esp_campaign_id=res.get("id"))
    return subid


def deliver(draft_id, tg=None):
    """Доставить черновик по его kind."""
    draft = get_store().get_draft(draft_id)
    if draft and draft.get("kind") == "email":
        return send_email_draft(draft_id)
    return publish_draft(draft_id, tg=tg)
