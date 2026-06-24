"""
Слой 2 — генерация черновиков.

Для каждого завершённого события без драфта на данном GEO:
  событие + geo-конфиг (+ выученные хуки из петли обратной связи) -> Claude
  -> черновик (post_a/post_b + compliance) -> очередь аппрува (status=pending).

Идемпотентность: один матч = один драфт на гео (store.event_has_draft).
"""
import uuid

from .config import load_geo, load_sport
from .store import get_store
from .claude import generate_post
from . import feedback


def generate_for_events(events, geo_name=None):
    geo = load_geo(geo_name)
    sport = load_sport()
    store = get_store()
    created = []

    # петля обучения: что историч. гонит FTD в этом гео -> в промпт
    geo["learned_winners"] = feedback.get_winners_block(geo["geo"])

    for ev in events:
        if store.event_has_draft(ev["match_id"], geo["geo"]):
            continue

        result, model = generate_post(ev, geo, sport)
        comp = result.get("compliance", {})
        draft = {
            "draft_id": uuid.uuid4().hex[:12],
            "match_id": ev["match_id"],
            "geo": geo["geo"],
            "channel_id": geo["telegram"]["public_channel_id"],
            "status": "pending",
            "post_a": result.get("post_a", ""),
            "post_b": result.get("post_b", ""),
            "hook_rationale": result.get("hook_rationale", ""),
            "compliance_ok": bool(comp.get("ok")),
            "compliance_notes": comp.get("notes", ""),
            "claude_model": model,
            "notified": False,
        }
        store.save_draft(draft)
        created.append(draft)

    return created


if __name__ == "__main__":
    from .ingest import ingest
    evs = ingest()
    drafts = generate_for_events(evs)
    print(f"generated {len(drafts)} draft(s)")
    for d in drafts:
        print(f"\n--- draft {d['draft_id']} (compliance_ok={d['compliance_ok']}) ---")
        print(d["post_a"])
