"""
Стор событий и очереди аппрува.

Два бэкенда:
  - local     : JSON-файл. Для прогона петли СЕГОДНЯ без GCP (one-machine testing).
  - bigquery  : твой прод-стор (MERGE-upsert, накопительная таблица).

Выбор: env STORE_BACKEND=local|bigquery (по умолчанию local).
Интерфейс одинаковый -> код генерации/публикации не зависит от бэкенда.
"""
import os
import json
import time
import threading
from datetime import datetime, timezone

BACKEND = os.environ.get("STORE_BACKEND", "local").lower()


def _now():
    return datetime.now(timezone.utc).isoformat()


# ----------------------------------------------------------------------
#  LOCAL JSON BACKEND  (testing only — есть гонки при многих процессах)
# ----------------------------------------------------------------------
class LocalJsonStore:
    def __init__(self, path=None):
        self.path = path or os.environ.get("LOCAL_STORE_PATH", "data/store.json")
        self._lock = threading.Lock()
        if not os.path.exists(self.path):
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._write({"events": {}, "drafts": {}})

    def _read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # --- events ---
    def upsert_event(self, event):
        with self._lock:
            d = self._read()
            event["ingested_at"] = _now()
            d["events"][event["match_id"]] = event
            self._write(d)

    def event_has_draft(self, match_id, geo, kind=None, audience=None):
        d = self._read()
        return any(
            dr["match_id"] == match_id and dr["geo"] == geo
            and (kind is None or dr.get("kind") == kind)
            and (audience is None or dr.get("audience") == audience)
            for dr in d["drafts"].values()
        )

    # --- drafts ---
    def save_draft(self, draft):
        with self._lock:
            d = self._read()
            draft.setdefault("created_at", _now())
            draft.setdefault("status", "pending")
            draft.setdefault("notified", False)
            d["drafts"][draft["draft_id"]] = draft
            self._write(d)

    def get_pending_unnotified(self):
        d = self._read()
        return [dr for dr in d["drafts"].values()
                if dr["status"] == "pending" and not dr.get("notified")]

    def get_draft(self, draft_id):
        return self._read()["drafts"].get(draft_id)

    def update_draft(self, draft_id, **fields):
        with self._lock:
            d = self._read()
            if draft_id in d["drafts"]:
                d["drafts"][draft_id].update(fields)
                self._write(d)

    def list_published(self, geo=None):
        d = self._read()
        return [dr for dr in d["drafts"].values()
                if dr.get("status") == "published"
                and dr.get("subid")
                and (geo is None or dr.get("geo") == geo)]

    def list_decided(self, geo=None, kind=None):
        d = self._read()
        return [dr for dr in d["drafts"].values()
                if dr.get("status") in ("approved", "published", "rejected")
                and (geo is None or dr.get("geo") == geo)
                and (kind is None or dr.get("kind") == kind)]


# ----------------------------------------------------------------------
#  BIGQUERY BACKEND  (production)
# ----------------------------------------------------------------------
class BigQueryStore:
    def __init__(self, dataset=None):
        from google.cloud import bigquery  # lazy import
        self.bq = bigquery.Client()
        self.dataset = dataset or os.environ["BQ_DATASET"]  # напр. x-fabric-494718-d1.autopost
        self.events = f"`{self.dataset}.match_events`"
        self.drafts = f"`{self.dataset}.post_drafts`"

    def _q(self, sql, params=None):
        from google.cloud import bigquery
        job_config = bigquery.QueryJobConfig(query_parameters=params or [])
        return list(self.bq.query(sql, job_config=job_config).result())

    def _p(self, name, type_, value):
        from google.cloud import bigquery
        return bigquery.ScalarQueryParameter(name, type_, value)

    def upsert_event(self, event):
        sql = f"""
        MERGE {self.events} T
        USING (SELECT @match_id AS match_id) S
        ON T.match_id = S.match_id
        WHEN MATCHED THEN UPDATE SET
          status=@status, score_home=@sh, score_away=@sa,
          key_stats=PARSE_JSON(@key_stats), raw=PARSE_JSON(@raw),
          finished_at=@finished_at, ingested_at=CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN INSERT
          (match_id,sport,league,status,home,away,score_home,score_away,key_stats,raw,finished_at,ingested_at)
        VALUES
          (@match_id,@sport,@league,@status,@home,@away,@sh,@sa,
           PARSE_JSON(@key_stats),PARSE_JSON(@raw),@finished_at,CURRENT_TIMESTAMP())
        """
        self._q(sql, [
            self._p("match_id", "STRING", event["match_id"]),
            self._p("sport", "STRING", event.get("sport")),
            self._p("league", "STRING", event.get("league")),
            self._p("status", "STRING", event.get("status")),
            self._p("home", "STRING", event.get("home")),
            self._p("away", "STRING", event.get("away")),
            self._p("sh", "INT64", event.get("score_home")),
            self._p("sa", "INT64", event.get("score_away")),
            self._p("key_stats", "STRING", json.dumps(event.get("key_stats", {}), ensure_ascii=False)),
            self._p("raw", "STRING", json.dumps(event.get("raw", {}), ensure_ascii=False)),
            self._p("finished_at", "TIMESTAMP", event.get("finished_at")),
        ])

    def event_has_draft(self, match_id, geo, kind=None, audience=None):
        sql = f"SELECT 1 FROM {self.drafts} WHERE match_id=@m AND geo=@g"
        params = [self._p("m", "STRING", match_id), self._p("g", "STRING", geo)]
        if kind is not None:
            sql += " AND kind=@k"
            params.append(self._p("k", "STRING", kind))
        if audience is not None:
            sql += " AND audience=@a"
            params.append(self._p("a", "STRING", audience))
        return len(self._q(sql + " LIMIT 1", params)) > 0

    # колонки таблицы post_drafts, которые можно вставлять напрямую
    _DRAFT_COLS = {
        "draft_id", "match_id", "geo", "kind", "channel_id", "status",
        "post_a", "post_b", "chosen_variant", "edited_text", "hook_rationale",
        "compliance_ok", "compliance_notes", "claude_model", "notified",
        "subid", "hook", "published_text", "airtable_record_id",
        "audience", "subject_a", "subject_b", "preview_text", "body_html",
        "edited_subject", "esp_campaign_id", "published_to", "published_links",
    }

    def save_draft(self, draft):
        cols = [c for c in draft if c in self._DRAFT_COLS]
        type_map = {bool: "BOOL", int: "INT64"}
        params, names, values = [], [], []
        for c in cols:
            names.append(c)
            values.append(f"@{c}")
            params.append(self._p(c, type_map.get(type(draft[c]), "STRING"), draft[c]))
        names.append("created_at"); values.append("CURRENT_TIMESTAMP()")
        sql = f"INSERT INTO {self.drafts} ({', '.join(names)}) VALUES ({', '.join(values)})"
        self._q(sql, params)

    def get_pending_unnotified(self):
        rows = self._q(
            f"SELECT * FROM {self.drafts} WHERE status='pending' AND notified=FALSE")
        return [dict(r) for r in rows]

    def get_draft(self, draft_id):
        rows = self._q(f"SELECT * FROM {self.drafts} WHERE draft_id=@id",
                       [self._p("id", "STRING", draft_id)])
        return dict(rows[0]) if rows else None

    def update_draft(self, draft_id, **fields):
        if not fields:
            return
        sets = ", ".join(f"{k}=@{k}" for k in fields)
        params = [self._p("id", "STRING", draft_id)]
        type_map = {bool: "BOOL", int: "INT64"}
        for k, v in fields.items():
            params.append(self._p(k, type_map.get(type(v), "STRING"), v))
        self._q(f"UPDATE {self.drafts} SET {sets} WHERE draft_id=@id", params)

    def list_published(self, geo=None):
        sql = (f"SELECT * FROM {self.drafts} "
               f"WHERE status='published' AND subid IS NOT NULL")
        params = []
        if geo:
            sql += " AND geo=@g"
            params.append(self._p("g", "STRING", geo))
        return [dict(r) for r in self._q(sql, params)]

    def list_decided(self, geo=None, kind=None):
        sql = f"SELECT * FROM {self.drafts} WHERE status IN ('approved','published','rejected')"
        params = []
        if geo:
            sql += " AND geo=@g"; params.append(self._p("g", "STRING", geo))
        if kind:
            sql += " AND kind=@k"; params.append(self._p("k", "STRING", kind))
        return [dict(r) for r in self._q(sql, params)]


def get_store():
    return BigQueryStore() if BACKEND == "bigquery" else LocalJsonStore()
