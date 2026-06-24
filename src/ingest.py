"""
Слой 1 — забор данных (актор-агностичный).

SOURCE:
  - sample : фикстура data/sample_match.json (петля без Apify).
  - apify  : дёргает РЕАЛЬНЫЕ акторы из sport-конфига. Каждый источник описывает
             run_input + field_map + stat_map -> смена актора = правка YAML, не кода.

Нормализованное событие:
  match_id, sport, league, status, home, away, score_home, score_away,
  key_stats (dict, только реально пришедшие поля), raw (dict), finished_at (iso)
"""
import os
import json
from datetime import datetime, timezone, timedelta

from .config import load_sport, ROOT
from .store import get_store


# ---- утилиты --------------------------------------------------------------
def _get_path(obj, path):
    """Достать значение по dot-path 'a.b.c' из вложенного dict. None если нет."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _to_iso(value):
    """unix (sec) | iso-строка -> iso. None пропускаем."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return str(value)


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_finished(raw, src, status):
    fv = [s.lower() for s in src.get("finished_values", [])]
    if isinstance(status, bool):              # напр. OpenLigaDB matchIsFinished
        return status
    return str(status).lower() in fv if status is not None else False


def _league_matches(league, sport_cfg):
    """Фильтр по интересующим турнирам (подстрока, регистронезависимо)."""
    wanted = sport_cfg.get("leagues", [])
    if not wanted:
        return True
    league = (league or "").lower()
    return any(w.lower() in league for w in wanted)


# ---- нормализация ---------------------------------------------------------
def _normalize(raw, src, sport_cfg):
    fm = src.get("field_map", {})
    sm = src.get("stat_map", {})
    sport_name = sport_cfg["sport"]

    def f(name):
        path = fm.get(name)
        return _get_path(raw, path) if path else raw.get(name)

    key_stats = {}
    for stat, path in sm.items():
        val = _get_path(raw, path)
        if val is not None:
            key_stats[stat] = val

    return {
        "match_id": str(f("match_id")),
        "sport": sport_name,
        "league": f("league"),
        "status": str(f("status")),
        "home": f("home"),
        "away": f("away"),
        "score_home": _to_int(f("score_home")),
        "score_away": _to_int(f("score_away")),
        "key_stats": key_stats,
        "raw": raw,
        "finished_at": _to_iso(f("finished_at")),
    }


# ---- источники ------------------------------------------------------------
def _target_dates(sport_cfg):
    days = sport_cfg.get("trigger", {}).get("lookback_days", 1)
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=d)).isoformat() for d in range(days + 1)]


def _fill_input(run_input, match_date):
    """Подставить ${MATCH_DATE} в run_input."""
    out = {}
    for k, v in run_input.items():
        if isinstance(v, str):
            out[k] = v.replace("${MATCH_DATE}", match_date)
        else:
            out[k] = v
    return out


def _from_sample(sport_cfg):
    with open(os.path.join(ROOT, "data", "sample_match.json"), "r", encoding="utf-8") as f:
        raw = json.load(f)
    # фикстура уже в нормализованных именах -> тривиальный source
    src = {"field_map": {}, "stat_map": {s: s for s in sport_cfg.get("key_stats", [])},
           "finished_values": ["finished"]}
    ev = _normalize(raw, src, sport_cfg)
    ev["type"] = "result"
    return [ev]


def _from_apify(sport_cfg):
    from apify_client import ApifyClient  # lazy
    client = ApifyClient(os.environ["APIFY_TOKEN"])
    req = sport_cfg["trigger"]["required_fields"]
    out, seen = [], set()

    for src in sport_cfg.get("sources", []):
        if not src.get("enabled"):
            continue
        dates = _target_dates(sport_cfg) if "${MATCH_DATE}" in json.dumps(src.get("run_input", {})) else [None]
        for d in dates:
            run_input = _fill_input(src.get("run_input", {}), d) if d else src.get("run_input", {})
            run = client.actor(src["actor_id"]).call(run_input=run_input)
            for item in client.dataset(run["defaultDatasetId"]).iterate_items():
                ev = _normalize(item, src, sport_cfg)
                if ev["match_id"] in seen:
                    continue
                if not _is_finished(item, src, ev["status"]):
                    continue
                if not _league_matches(ev["league"], sport_cfg):
                    continue
                if any(ev.get(rf) is None for rf in req):
                    continue
                seen.add(ev["match_id"])
                ev["type"] = "result"
                out.append(ev)
    return out


def ingest():
    """Забрать завершённые матчи и upsert в стор. Возвращает список событий."""
    sport_cfg = load_sport()
    source = os.environ.get("SOURCE", "sample")
    events = _from_sample(sport_cfg) if source == "sample" else _from_apify(sport_cfg)

    store = get_store()
    for ev in events:
        store.upsert_event(ev)
    return events


# ---- НОВОСТИ (инфоповоды, Слой 1) -----------------------------------------
import hashlib
from .config import load_news


def _news_normalize(raw, news_cfg):
    fm = news_cfg["source"].get("field_map", {})

    def f(name):
        path = fm.get(name)
        return _get_path(raw, path) if path else raw.get(name)

    url = f("url") or f("id") or ""
    nid = "news_" + hashlib.sha1(str(url).encode()).hexdigest()[:12]
    return {
        "match_id": nid,
        "type": "news",
        "sport": os.environ.get("PILOT_SPORT", "football"),
        "league": f("league"),
        "status": "news",
        "title": f("title"),
        "summary": f("summary"),
        "url": url,
        "source": f("source"),
        "key_stats": {},
        "raw": raw,
        "finished_at": _to_iso(f("finished_at")),
    }


def _news_relevant(ev, news_cfg):
    kws = news_cfg.get("keywords", [])
    if not kws:
        return True
    text = f"{ev.get('title','')} {ev.get('summary','')}".lower()
    return any(k.lower() in text for k in kws)


def _news_from_sample(news_cfg):
    with open(os.path.join(ROOT, "data", "sample_news.json"), "r", encoding="utf-8") as f:
        return [_news_normalize(json.load(f), news_cfg)]


def _news_from_apify(news_cfg):
    from apify_client import ApifyClient
    client = ApifyClient(os.environ["APIFY_TOKEN"])
    src = news_cfg["source"]
    run = client.actor(src["actor_id"]).call(run_input=src.get("run_input", {}))
    out, seen = [], set()
    for item in client.dataset(run["defaultDatasetId"]).iterate_items():
        ev = _news_normalize(item, news_cfg)
        if ev["match_id"] in seen or not _news_relevant(ev, news_cfg):
            continue
        seen.add(ev["match_id"])
        out.append(ev)
    return out


def ingest_news():
    """Забрать инфоповоды. Возвращает список news-событий (или [] если выключено)."""
    news_cfg = load_news()
    if not news_cfg.get("enabled"):
        return []
    source = os.environ.get("SOURCE", "sample")
    events = _news_from_sample(news_cfg) if source == "sample" else _news_from_apify(news_cfg)
    store = get_store()
    for ev in events:
        store.upsert_event(ev)
    return events


if __name__ == "__main__":
    evs = ingest()
    print(f"ingested {len(evs)} event(s): {[e['match_id'] for e in evs]}")
