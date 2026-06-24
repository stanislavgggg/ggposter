"""
Воркер (Railway cron). Один проход петли производства, МУЛЬТИ-ГЕО:

  ingest (раз)  ->  для каждого GEO: generate (Слой 2)  ->  драфты в очередь аппрува.

События общие для всех гео (один матч), а тексты — свои на каждый гео
(транскреация + локальный угол + выученные хуки). Публикацию делает always-on bot.py.

GEO берутся из env GEOS="lt,lv,es" (через запятую) либо из PILOT_GEO (один).
"""
import os

from .ingest import ingest
from .generate import generate_for_events


def _geos():
    raw = os.environ.get("GEOS")
    if raw:
        return [g.strip() for g in raw.split(",") if g.strip()]
    return [os.environ.get("PILOT_GEO", "lt")]


def run_once():
    events = ingest()
    total = 0
    for geo in _geos():
        drafts = generate_for_events(events, geo_name=geo)
        total += len(drafts)
        print(f"[orchestrate] geo={geo} events={len(events)} new_drafts={len(drafts)}")
    print(f"[orchestrate] DONE events={len(events)} total_new_drafts={total}")
    return total


if __name__ == "__main__":
    run_once()
