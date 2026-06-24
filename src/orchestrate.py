"""
Воркер (Railway cron). Один проход петли производства, МУЛЬТИ-ГЕО + МУЛЬТИ-ВЫХОД:

  ingest матчей + ingest_news (инфоповоды) + enrich (xG)
    -> для каждого GEO: generate (telegram и/или email по geo.outputs)
    -> драфты в очередь аппрува.

События общие, тексты — свои на гео/выход. Доставку делает always-on review-процесс.
GEO из env GEOS="lt,lv,es" либо PILOT_GEO.
"""
import os

from . import _bootstrap  # noqa: F401  — env→файл GCP-креды; ДОЛЖЕН идти первым
from .ingest import ingest, ingest_news
from .enrich import enrich
from .generate import generate_for_events
from .store import get_store


def _geos():
    raw = os.environ.get("GEOS")
    if raw:
        return [g.strip() for g in raw.split(",") if g.strip()]
    return [os.environ.get("PILOT_GEO", "lt")]


def _events_per_run():
    """Сколько НОВЫХ событий обрабатывать за прогон. По умолчанию 1:
    «схватить первый матч, о котором ещё не писали». 0 = без лимита."""
    return int(os.environ.get("EVENTS_PER_RUN", "1"))


def _select_new(events, store, limit):
    """Свежие-первыми, только те, по которым ещё НЕТ ни одного драфта (ни в одном гео),
    обрезаем до limit. limit=0 -> все новые."""
    fresh = sorted(events, key=lambda e: e.get("finished_at") or "", reverse=True)
    out = []
    for ev in fresh:
        if store.event_has_any_draft(ev["match_id"]):
            continue
        out.append(ev)
        if limit and len(out) >= limit:
            break
    return out


def run_once():
    store = get_store()
    limit = _events_per_run()
    matches = ingest(limit=limit)         # two_pass материализует только нужные новые матчи
    news = ingest_news()
    enrich(matches)                       # опционально (ENRICH=true)

    all_events = matches + news
    events = _select_new(all_events, store, limit)
    print(f"[orchestrate] получено={len(all_events)} новых_к_обработке={len(events)} "
          f"(EVENTS_PER_RUN={limit or '∞'})")

    total = 0
    for geo in _geos():
        drafts = generate_for_events(events, geo_name=geo)
        total += len(drafts)
        kinds = {}
        for d in drafts:
            kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
        print(f"[orchestrate] geo={geo} events={len(events)} drafts={len(drafts)} {kinds}")
    print(f"[orchestrate] DONE matches={len(matches)} news={len(news)} "
          f"processed={len(events)} total_drafts={total}")
    return total


if __name__ == "__main__":
    run_once()
