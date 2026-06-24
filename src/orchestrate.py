"""
Воркер (Railway cron). Один проход петли производства, МУЛЬТИ-ГЕО + МУЛЬТИ-ВЫХОД:

  ingest матчей + ingest_news (инфоповоды) + enrich (xG)
    -> для каждого GEO: generate (telegram и/или email по geo.outputs)
    -> драфты в очередь аппрува.

События общие, тексты — свои на гео/выход. Доставку делает always-on review-процесс.
GEO из env GEOS="lt,lv,es" либо PILOT_GEO.
"""
import os

from .ingest import ingest, ingest_news
from .enrich import enrich
from .generate import generate_for_events


def _geos():
    raw = os.environ.get("GEOS")
    if raw:
        return [g.strip() for g in raw.split(",") if g.strip()]
    return [os.environ.get("PILOT_GEO", "lt")]


def run_once():
    matches = ingest()
    news = ingest_news()
    enrich(matches)                       # опционально (ENRICH=true)
    events = matches + news
    total = 0
    for geo in _geos():
        drafts = generate_for_events(events, geo_name=geo)
        total += len(drafts)
        kinds = {}
        for d in drafts:
            kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
        print(f"[orchestrate] geo={geo} events={len(events)} drafts={len(drafts)} {kinds}")
    print(f"[orchestrate] DONE matches={len(matches)} news={len(news)} total_drafts={total}")
    return total


if __name__ == "__main__":
    run_once()
