"""
Слой 2 — генерация черновиков для всех выходов конвейера.

Для каждого события, для каждого выхода (geo.outputs: telegram | email):
  событие + geo (+ выученные хуки/сабжекты этого выхода) -> Claude
  -> черновик (kind=telegram|email) -> очередь аппрува (status=pending).

Идемпотентность: один матч = один драфт на (гео, kind, audience).
"""
import uuid

from .config import load_geo, load_sport, telegram_destinations
from .store import get_store
from .claude import generate_post, generate_email
from . import feedback


def _base(geo, ev, kind):
    dests = telegram_destinations(geo)
    return {
        "draft_id": uuid.uuid4().hex[:12],
        "match_id": ev["match_id"],
        "geo": geo["geo"],
        "kind": kind,
        "channel_id": dests[0]["id"] if dests else "",   # основное назначение (для отображения)
        "status": "pending",
        "notified": False,
    }


def _telegram_draft(geo, sport, ev, store):
    if store.event_has_draft(ev["match_id"], geo["geo"], kind="telegram"):
        return None
    geo = dict(geo); geo["learned_winners"] = feedback.get_winners_block(geo["geo"], "telegram")
    res, model = generate_post(ev, geo, sport)
    comp = res.get("compliance", {})
    d = _base(geo, ev, "telegram")
    d.update(post_a=res.get("post_a", ""), post_b=res.get("post_b", ""),
             hook_rationale=res.get("hook_rationale", ""),
             compliance_ok=bool(comp.get("ok")), compliance_notes=comp.get("notes", ""),
             claude_model=model)
    return d


def _email_drafts(geo, sport, ev, store):
    out = []
    email_cfg = geo.get("email", {})
    for audience in email_cfg.get("audiences", ["warm"]):
        if store.event_has_draft(ev["match_id"], geo["geo"], kind="email", audience=audience):
            continue
        g = dict(geo); g["learned_winners"] = feedback.get_winners_block(geo["geo"], "email")
        res, model = generate_email(ev, g, sport, audience=audience)
        comp = res.get("compliance", {})
        d = _base(geo, ev, "email")
        d.update(audience=audience, subject_a=res.get("subject_a", ""),
                 subject_b=res.get("subject_b", ""), preview_text=res.get("preview_text", ""),
                 body_html=res.get("body_html", ""), hook_rationale=res.get("hook_rationale", ""),
                 compliance_ok=bool(comp.get("ok")), compliance_notes=comp.get("notes", ""),
                 claude_model=model)
        out.append(d)
    return out


def generate_for_events(events, geo_name=None):
    geo = load_geo(geo_name)
    sport = load_sport()
    store = get_store()
    outputs = geo.get("outputs", ["telegram"])
    created = []

    for ev in events:
        drafts = []
        if "telegram" in outputs:
            d = _telegram_draft(geo, sport, ev, store)
            if d:
                drafts.append(d)
        if "email" in outputs:
            drafts.extend(_email_drafts(geo, sport, ev, store))
        for d in drafts:
            store.save_draft(d)
            created.append(d)
    return created


if __name__ == "__main__":
    from .ingest import ingest
    evs = ingest()
    drafts = generate_for_events(evs)
    print(f"generated {len(drafts)} draft(s)")
