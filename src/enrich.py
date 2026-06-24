"""
Слой 1 — обогащение событий (xG и т.п.).

Best-effort: для матчей (type=result) доливает поля из enricher-актора
(напр. Understat xG) в key_stats, сопоставляя по нормализованному имени команды.
Опционально (env ENRICH=true). Никогда не выдумывает: если совпадения нет — пропускает.
"""
import os
import re

from .config import load_sport
from .ingest import _get_path, _dataset_id
from .store import get_store


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _fetch_xg_map(enr):
    """Вернуть {normalized_team: {field: value}} из актора-обогатителя."""
    from apify_client import ApifyClient
    client = ApifyClient(os.environ["APIFY_TOKEN"])
    fm = enr.get("field_map", {})
    run = client.actor(enr["actor_id"]).call(run_input=enr.get("run_input", {}))
    table = {}
    for item in client.dataset(_dataset_id(run)).iterate_items():
        team = _get_path(item, fm.get("team", "team"))
        if not team:
            continue
        row = {}
        for field in enr.get("merges", []):
            val = _get_path(item, fm.get(field, field))
            if val is not None:
                row[field] = val
        table[_norm(team)] = row
    return table


def enrich(events):
    """Обогатить матчи. Возвращает обогащённые события (и обновляет стор)."""
    if os.environ.get("ENRICH", "false").lower() != "true":
        return events
    sport_cfg = load_sport()
    store = get_store()

    for enr in sport_cfg.get("enrichers", []):
        if not enr.get("enabled"):
            continue
        try:
            xg_map = _fetch_xg_map(enr)
        except Exception as e:
            print(f"[enrich] {enr['name']} failed: {e}")
            continue
        for ev in events:
            if ev.get("type") != "result":
                continue
            home, away = xg_map.get(_norm(ev.get("home"))), xg_map.get(_norm(ev.get("away")))
            for field in enr.get("merges", []):
                if field in ev["key_stats"]:
                    continue                                   # источник уже дал -> не трогаем
                hv = (home or {}).get(field)
                av = (away or {}).get(field)
                if hv is not None and av is not None:
                    ev["key_stats"][field] = f"{hv}-{av}"      # напр. xg: "2.7-0.9"
            store.upsert_event(ev)
    return events
