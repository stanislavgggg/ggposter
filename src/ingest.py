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


# ---- совместимость apify-client 1.x / 3.x ---------------------------------
def _actor_call(actor_client, run_input, timeout_secs):
    """Запуск актора с правильным аргументом таймаута для любой версии клиента.

    apify-client 3.x: timeout_secs → run_timeout=timedelta(seconds=...)
    apify-client 1.x: timeout_secs=int
    fallback:         запуск без таймаута + WARNING в лог
    """
    import inspect
    sig = inspect.signature(actor_client.call)
    if "run_timeout" in sig.parameters:
        # apify-client >= 3.0
        return actor_client.call(run_input=run_input,
                                 run_timeout=timedelta(seconds=timeout_secs))
    elif "timeout_secs" in sig.parameters:
        # apify-client 1.x
        return actor_client.call(run_input=run_input, timeout_secs=timeout_secs)
    else:
        print(f"[ingest] WARNING: ActorClient.call() не поддерживает таймаут "
              f"(неизвестная версия apify-client) — запускаем без него")
        return actor_client.call(run_input=run_input)


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
    """Фильтр по интересующим турнирам (подстрока, регистронезависимо).

    Если источник НЕ дал лигу (league пустой) — НЕ режем: иначе теряем всё
    (напр. Sofascore-актор отдаёт tournament=null). Лучше пропустить, чем потерять.
    """
    wanted = sport_cfg.get("leagues", [])
    if not wanted:
        return True
    if not league:
        return True
    league = league.lower()
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


def _dataset_id(run):
    """id датасета из результата .call(). apify-client 3.x -> объект Run
    (run.default_dataset_id); старые версии -> dict (run['defaultDatasetId'])."""
    if run is None:
        raise RuntimeError("Apify actor run is None (актор не запустился или таймаут)")
    if isinstance(run, dict):
        return run["defaultDatasetId"]
    return run.default_dataset_id


def _run_status(run):
    """Статус запуска (SUCCEEDED/TIMED-OUT/...) — оба формата результата .call()."""
    if run is None:
        return None
    if isinstance(run, dict):
        return run.get("status")
    return getattr(run, "status", None)


def _from_apify(sport_cfg, limit=None, store=None):
    """Диспетчер источников. Для каждого включённого источника выбирает режим:
      two_pass    — проход 1 (список без деталей) -> выбор свежайшего НОВОГО матча ЧМ ->
                    проход 2 (детали по ОДНОМУ матчу через eventUrls). Дёшево.
      single_pass — старое поведение: тянет детали по всем maxItems сразу.
    Переопределить можно env FETCH_MODE.
    """
    store = store or get_store()
    out, seen, seen_leagues = [], set(), set()
    any_league_match = False
    client = None  # создаём лениво, только если есть включённый apify-источник

    for src in sport_cfg.get("sources", []):
        if not src.get("enabled"):
            continue

        # --- HTTP-источник (надёжный JSON API, без Apify/скрапинга) ---
        if src.get("type") == "http":
            evs, matched = _http_source(src, sport_cfg, seen)
            out.extend(evs)
            any_league_match = any_league_match or matched
            if limit and len(out) >= limit:
                break
            continue

        # --- Apify-источники ---
        if client is None:
            from apify_client import ApifyClient  # lazy: нужен только для apify-источников
            client = ApifyClient(os.environ["APIFY_TOKEN"])
        mode = (os.environ.get("FETCH_MODE") or src.get("fetch_mode") or "single_pass").lower()
        if mode == "two_pass":
            evs, matched = _two_pass_source(src, sport_cfg, client, limit, store, seen, seen_leagues)
            out.extend(evs)
            any_league_match = any_league_match or matched
            if limit and len(out) >= limit:
                break
        else:
            evs, matched = _single_pass_source(src, sport_cfg, client, seen, seen_leagues)
            out.extend(evs)
            any_league_match = any_league_match or matched

    if not out and not any_league_match and seen_leagues:
        print(f"[ingest] 0 событий: ни одна лига не совпала с {sport_cfg.get('leagues')}. "
              f"Виденные лиги: {sorted(seen_leagues)} — подправь leagues в config/sport.")
    return out


def _single_pass_source(src, sport_cfg, client, seen, seen_leagues):
    req = sport_cfg["trigger"]["required_fields"]
    parser = PARSERS.get(src.get("parser"))
    timeout = int(src.get("timeout_secs") or os.environ.get("APIFY_TIMEOUT_SECS", "600"))
    run_input_cfg = src.get("run_input", {})
    dates = _target_dates(sport_cfg) if "${MATCH_DATE}" in json.dumps(run_input_cfg) else [None]
    out, matched_any = [], False
    for d in dates:
        run_input = _fill_input(run_input_cfg, d) if d else run_input_cfg
        run = _actor_call(client.actor(src["actor_id"]), run_input=run_input, timeout_secs=timeout)
        status = _run_status(run)
        if status and str(status).upper() != "SUCCEEDED":
            print(f"[ingest] WARN {src['actor_id']} date={d} status={status} "
                  f"(подними timeout_secs / снизь maxItems)")
        for item in client.dataset(_dataset_id(run)).iterate_items():
            if parser:
                ev = parser(item, sport_cfg)
                if ev is None:
                    continue
            else:
                ev = _normalize(item, src, sport_cfg)
                if not _is_finished(item, src, ev["status"]):
                    continue
                ev["type"] = "result"
            if ev["match_id"] in seen:
                continue
            if not _league_matches(ev.get("league"), sport_cfg):
                if ev.get("league"):
                    seen_leagues.add(ev["league"])
                continue
            matched_any = True
            if any(ev.get(rf) is None for rf in req):
                continue
            seen.add(ev["match_id"])
            out.append(ev)
    return out, matched_any


# ---- двухпроходный режим (дёшево: детали только по нужному матчу) ----------
def _event_url(row, fm):
    """URL матча для прохода 2 (eventUrls). Берём из field_map.event_url, иначе из
    известных полей, иначе строим из slug+id (best-effort)."""
    for key in (fm.get("event_url"), "url", "eventUrl", "matchUrl", "sourceUrl"):
        if not key:
            continue
        v = _get_path(row, key)
        if v and "sofascore.com" in str(v) and "/scheduled" not in str(v):
            return str(v)
    slug, rid = row.get("slug"), row.get("id")
    if slug and rid:
        return f"https://www.sofascore.com/{slug}/{rid}"
    return None


def _list_candidate(row, fm, sport_cfg):
    """Из строки прохода 1 (без деталей) собрать кандидата для отбора."""
    if row.get("rowType") not in (None, "event"):
        return None
    home = _get_path(row, fm.get("home", "homeTeam.name"))
    away = _get_path(row, fm.get("away", "awayTeam.name"))
    if not home or not away:
        return None
    league = _get_path(row, fm.get("league", "tournament.name"))
    if not league:
        t = row.get("tournament")
        league = t.get("name") if isinstance(t, dict) else (t if isinstance(t, str) else None)
    return {
        "match_id": str(_get_path(row, fm.get("match_id", "id"))),
        "home": home, "away": away, "league": league,
        "finished_at": _to_iso(_get_path(row, fm.get("finished_at", "startTimestamp"))),
        "url": _event_url(row, fm),
    }


def _two_pass_source(src, sport_cfg, client, limit, store, seen, seen_leagues):
    fm = src.get("field_map", {})
    req = sport_cfg["trigger"]["required_fields"]
    timeout = int(src.get("timeout_secs") or os.environ.get("APIFY_TIMEOUT_SECS", "600"))
    cap = limit if limit else int(os.environ.get("EVENTS_PER_RUN", "1") or 1)
    max_lookups = int(src.get("max_detail_lookups", 5))

    # ---- ПРОХОД 1: список без деталей (дёшево) ----
    list_input = src.get("list_input", {})
    dates = _target_dates(sport_cfg) if "${MATCH_DATE}" in json.dumps(list_input) else [None]
    candidates, cids, matched_any = [], set(), False
    for d in dates:
        ri = _fill_input(list_input, d) if d else list_input
        run = _actor_call(client.actor(src["actor_id"]), run_input=ri, timeout_secs=timeout)
        st = _run_status(run)
        if st and str(st).upper() != "SUCCEEDED":
            print(f"[ingest] WARN pass1 {src['actor_id']} date={d} status={st}")
        for row in client.dataset(_dataset_id(run)).iterate_items():
            c = _list_candidate(row, fm, sport_cfg)
            if not c:
                continue
            if not _league_matches(c["league"], sport_cfg):
                if c["league"]:
                    seen_leagues.add(c["league"])
                continue
            matched_any = True
            if c["match_id"] in cids or c["match_id"] in seen:
                continue
            if store.event_has_any_draft(c["match_id"]):     # «об этом уже писали»
                continue
            cids.add(c["match_id"])
            candidates.append(c)

    candidates.sort(key=lambda c: c.get("finished_at") or "", reverse=True)  # свежие первыми
    print(f"[ingest] pass1: новых кандидатов ЧМ={len(candidates)} нужно={cap}")

    # ---- ПРОХОД 2: детали только по нужным матчам, пока не наберём cap финалов ----
    detail_input = src.get("detail_input", {})
    out, attempts = [], 0
    for c in candidates:
        if len(out) >= cap or attempts >= max_lookups:
            break
        if not c.get("url"):
            print(f"[ingest] pass2: нет URL матча {c['match_id']} (проверь field_map.event_url)")
            continue
        attempts += 1
        di = dict(detail_input); di["startUrls"] = [c["url"]]
        run = _actor_call(client.actor(src["actor_id"]), run_input=di, timeout_secs=timeout)
        ev = None
        for row in client.dataset(_dataset_id(run)).iterate_items():
            ev = _parse_sofascore_scheduled(row, sport_cfg, require_rowtype=False)
            if ev:
                break
        if not ev:
            print(f"[ingest] pass2: {c['url']} -> не финал/нет данных, пропускаю")
            continue
        if any(ev.get(rf) is None for rf in req):
            print(f"[ingest] pass2: {c['url']} -> нет обязательных полей {req}, пропускаю")
            continue
        seen.add(ev["match_id"])
        out.append(ev)
        print(f"[ingest] pass2: ВЗЯТ {ev['home']} {ev['score_home']}:{ev['score_away']} {ev['away']}")

    if not out and (candidates or attempts):
        print(f"[ingest] pass2: 0 финалов из {attempts} проверок. Если кандидаты были — "
              f"сверь field_map.event_url и работу режима eventUrls у актора.")
    return out, matched_any


# ---- актор-специфичные парсеры -------------------------------------------
def _parse_sofascore_scheduled(raw, sport_cfg, require_rowtype=True):
    """
    Адаптер под maximedupre/sofascore-live-events-scraper.
    Финал берём из incidents (FT) — они приходят только при includeMatchDetails=true.
    require_rowtype: в проходе 2 (eventUrls) строка — это наш матч, rowType можно не требовать.
    Возвращает нормализованное событие или None (если матч ещё не сыгран / нет данных).
    """
    if require_rowtype and raw.get("rowType") != "event":
        return None
    home = _get_path(raw, "homeTeam.name")
    away = _get_path(raw, "awayTeam.name")
    if not home or not away:
        return None

    incidents = raw.get("incidents") or []
    ft = next((i for i in incidents
               if i.get("text") == "FT" and i.get("incidentType") == "period"), None)
    if not ft:
        return None                                   # нет FT -> матч не сыгран, пропускаем

    # статы из инцидентов (только то, что реально есть)
    key_stats = {}
    reds = sum(1 for i in incidents
               if i.get("incidentType") == "card" and str(i.get("incidentClass")).lower() in ("red", "redcard"))
    if reds:
        key_stats["red_cards"] = reds
    scorers = [i.get("playerName") for i in incidents
               if i.get("incidentType") == "goal" and i.get("playerName")]
    if scorers:
        key_stats["scorers"] = scorers

    tournament = raw.get("tournament")
    league = tournament.get("name") if isinstance(tournament, dict) else (tournament or None)

    return {
        "match_id": str(raw.get("id")),
        "type": "result",
        "sport": sport_cfg["sport"],
        "league": league,                             # может быть None — это ок
        "status": "finished",
        "home": home,
        "away": away,
        "score_home": _to_int(ft.get("homeScore")),
        "score_away": _to_int(ft.get("awayScore")),
        "key_stats": key_stats,
        "raw": raw,
        "finished_at": _to_iso(raw.get("startTimestamp") or raw.get("startDate") or raw.get("scrapedAt")),
    }


PARSERS = {"sofascore_scheduled": _parse_sofascore_scheduled}


# ---- HTTP-источники (надёжный JSON, без Apify) ----------------------------
def _http_source(src, sport_cfg, seen):
    """Тянет JSON по URL и парсит сыгранные матчи. Возвращает (события, был_ли_матч_лиги)."""
    import requests
    parser = HTTP_PARSERS.get(src.get("parser"))
    if not parser:
        print(f"[ingest] http '{src.get('name')}' без parser — пропуск")
        return [], False
    try:
        r = requests.get(src["url"], timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[ingest] http {src.get('url')} error: {e}")
        return [], False

    out, matched = [], False
    for ev in parser(data, sport_cfg):
        if ev["match_id"] in seen:
            continue
        if not _league_matches(ev.get("league"), sport_cfg):
            continue
        matched = True
        seen.add(ev["match_id"])
        out.append(ev)
    print(f"[ingest] http '{src.get('name')}': сыгранных матчей={len(out)}")
    return out, matched


def _parse_openfootball_wc(data, sport_cfg):
    """
    openfootball/worldcup.json — публичный JSON ЧМ-2026 (без ключа/лимитов).
    Сыгранный матч = есть score.ft (массив из 2). У источника нет id -> делаем
    стабильный match_id из даты+команд (идемпотентность). Голеадоры -> key_stats.scorers.
    """
    import hashlib
    league = data.get("name") or "World Cup 2026"
    out = []
    for m in data.get("matches", []):
        sc = m.get("score") or {}
        ft = sc.get("ft")
        if not (isinstance(ft, list) and len(ft) == 2):
            continue                                   # ещё не сыгран
        home, away = m.get("team1"), m.get("team2")
        if not home or not away:
            continue
        mid = "wc_" + hashlib.sha1(f"{m.get('date')}|{home}|{away}".encode()).hexdigest()[:12]
        scorers = [g.get("name") for g in (m.get("goals1") or []) + (m.get("goals2") or [])
                   if isinstance(g, dict) and g.get("name")]
        key_stats = {}
        if scorers:
            key_stats["scorers"] = scorers
        ht = sc.get("ht")
        if isinstance(ht, list) and len(ht) == 2:
            key_stats["halftime"] = f"{ht[0]}-{ht[1]}"
        out.append({
            "match_id": mid,
            "type": "result",
            "sport": sport_cfg["sport"],
            "league": league,
            "status": "finished",
            "home": home,
            "away": away,
            "score_home": _to_int(ft[0]),
            "score_away": _to_int(ft[1]),
            "key_stats": key_stats,
            "raw": m,
            "finished_at": _to_iso(m.get("date")),
        })
    return out


HTTP_PARSERS = {"openfootball_wc": _parse_openfootball_wc}


def ingest(limit=None):
    """Забрать завершённые матчи и upsert в стор. Возвращает список событий.
    limit — для two_pass: сколько новых матчей материализовать (детали тянем только по ним)."""
    sport_cfg = load_sport()
    source = os.environ.get("SOURCE", "sample")
    events = _from_sample(sport_cfg) if source == "sample" else _from_apify(sport_cfg, limit=limit)

    store = get_store()
    for ev in events:
        store.upsert_event(ev)
    return events


# ---- НОВОСТИ (инфоповоды, Слой 1) -----------------------------------------
import hashlib
import re
import html as _html
from email.utils import parsedate_to_datetime
import xml.etree.ElementTree as ET
from .config import load_news


def _news_id(url):
    return "news_" + hashlib.sha1(str(url).encode()).hexdigest()[:12]


def _news_relevant(ev, news_cfg):
    kws = news_cfg.get("keywords", [])
    if not kws:
        return True
    text = f"{ev.get('title','')} {ev.get('summary','')}".lower()
    return any(k.lower() in text for k in kws)


def _news_normalize(raw, fm):
    """Нормализация по field_map (для apify/sample-источников GDELT-формата)."""
    def f(name):
        path = fm.get(name)
        return _get_path(raw, path) if path else raw.get(name)
    url = f("url") or f("id") or ""
    return {
        "match_id": _news_id(url), "type": "news",
        "sport": os.environ.get("PILOT_SPORT", "football"),
        "league": f("league"), "status": "news",
        "title": f("title"), "summary": f("summary"),
        "url": url, "source": f("source"),
        "key_stats": {}, "raw": raw, "finished_at": _to_iso(f("finished_at")),
    }


# --- RSS (надёжно, без токена, с описаниями) ---
def _strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", _html.unescape(s)).strip()


def _parse_rss(xml_text):
    """RSS 2.0 и Atom -> [{title, link, summary, pubDate, source}]."""
    out = []
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is not None:                                  # RSS 2.0
        domain = _strip_html(channel.findtext("title") or "")
        for it in channel.findall("item"):
            out.append({
                "title": _strip_html(it.findtext("title") or ""),
                "link": (it.findtext("link") or "").strip(),
                "summary": _strip_html(it.findtext("description") or "")[:500],
                "pubDate": it.findtext("pubDate"),
                "source": domain,
            })
    else:                                                    # Atom
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for e in root.findall("a:entry", ns):
            link_el = e.find("a:link", ns)
            out.append({
                "title": _strip_html(e.findtext("a:title", default="", namespaces=ns)),
                "link": (link_el.get("href") if link_el is not None else "") or "",
                "summary": _strip_html(e.findtext("a:summary", default="", namespaces=ns)
                                       or e.findtext("a:content", default="", namespaces=ns))[:500],
                "pubDate": e.findtext("a:updated", default="", namespaces=ns),
                "source": "",
            })
    return out


def _rss_event(it):
    fin = None
    if it.get("pubDate"):
        try:
            fin = parsedate_to_datetime(it["pubDate"]).astimezone(timezone.utc).isoformat()
        except Exception:
            fin = None
    return {
        "match_id": _news_id(it["link"]), "type": "news",
        "sport": os.environ.get("PILOT_SPORT", "football"),
        "league": None, "status": "news",
        "title": it.get("title"), "summary": it.get("summary"),
        "url": it.get("link"), "source": it.get("source"),
        "key_stats": {}, "raw": it, "finished_at": fin,
    }


def _news_from_rss(news_cfg):
    import requests
    src = news_cfg.get("source", {})
    feeds = src.get("feeds", [])
    maxf = int(src.get("max_per_feed", 15))
    out, seen = [], set()
    for url in feeds:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "ggposter/1.0"})
            r.raise_for_status()
            items = _parse_rss(r.text)[:maxf]
        except Exception as e:
            print(f"[news] rss {url} error: {e}")
            continue
        for it in items:
            if not it.get("title") or not it.get("link"):
                continue
            ev = _rss_event(it)
            if ev["match_id"] in seen or not _news_relevant(ev, news_cfg):
                continue
            seen.add(ev["match_id"])
            out.append(ev)
    return out


def _news_from_sample(news_cfg):
    fm = news_cfg.get("apify", {}).get("field_map", {})
    with open(os.path.join(ROOT, "data", "sample_news.json"), "r", encoding="utf-8") as f:
        return [_news_normalize(json.load(f), fm)]


def _news_from_apify(news_cfg):
    from apify_client import ApifyClient
    ap = news_cfg.get("apify", {})
    client = ApifyClient(os.environ["APIFY_TOKEN"])
    run = client.actor(ap["actor_id"]).call(run_input=ap.get("run_input", {}))
    out, seen = [], set()
    for item in client.dataset(_dataset_id(run)).iterate_items():
        ev = _news_normalize(item, ap.get("field_map", {}))
        if ev["match_id"] in seen or not _news_relevant(ev, news_cfg):
            continue
        seen.add(ev["match_id"])
        out.append(ev)
    return out


def ingest_news():
    """Забрать инфоповоды. RSS по умолчанию (без токена). SOURCE=sample -> фикстура."""
    news_cfg = load_news()
    if not news_cfg.get("enabled"):
        return []
    stype = (news_cfg.get("source", {}) or {}).get("type", "rss")
    source_env = os.environ.get("SOURCE", "sample")
    if source_env == "sample":
        events = _news_from_sample(news_cfg)
    elif stype == "apify":
        events = _news_from_apify(news_cfg)
    else:
        events = _news_from_rss(news_cfg)
    store = get_store()
    for ev in events:
        store.upsert_event(ev)
    print(f"[news] источник={'sample' if source_env=='sample' else stype} новостей={len(events)}")
    return events


if __name__ == "__main__":
    evs = ingest()
    print(f"ingested {len(evs)} event(s): {[e['match_id'] for e in evs]}")
