"""
Источник перформанса постов по subid.

Каждый опубликованный пост несёт уникальный subid (tg_<geo>_<draft_id>) в трекинг-ссылке.
Отсюда тянем clicks / FTD per subid -> это сигнал для петли обратной связи.

Бэкенды (env METRICS_BACKEND):
  - local    : data/metrics.json  {"<subid>": {"clicks": N, "ftd": M}}  (для теста)
  - bigquery : твой стор Voonix/FTD. Таблица и колонки настраиваются через env:
        METRICS_TABLE      напр. x-fabric-494718-d1.datasetmailchimp.VoonixChannelDaily
        METRICS_SUBID_COL  колонка с subid (по умолчанию subid)
        METRICS_CLICKS_COL по умолчанию clicks
        METRICS_FTD_COL    по умолчанию ftd
"""
import os
import json

from .config import ROOT

BACKEND = os.environ.get("METRICS_BACKEND", "local").lower()


class LocalMetrics:
    def __init__(self, path=None):
        self.path = path or os.environ.get("METRICS_PATH", os.path.join(ROOT, "data", "metrics.json"))

    def get(self, subids):
        if not os.path.exists(self.path):
            return {}
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {s: data.get(s, {"clicks": 0, "ftd": 0}) for s in subids}


class BigQueryMetrics:
    def __init__(self):
        from google.cloud import bigquery
        self.bq = bigquery.Client()
        self.table = os.environ["METRICS_TABLE"]
        self.subid_col = os.environ.get("METRICS_SUBID_COL", "subid")
        self.clicks_col = os.environ.get("METRICS_CLICKS_COL", "clicks")
        self.ftd_col = os.environ.get("METRICS_FTD_COL", "ftd")

    def get(self, subids):
        if not subids:
            return {}
        from google.cloud import bigquery
        sql = f"""
        SELECT {self.subid_col} AS subid,
               SUM({self.clicks_col}) AS clicks,
               SUM({self.ftd_col})    AS ftd
        FROM `{self.table}`
        WHERE {self.subid_col} IN UNNEST(@subids)
        GROUP BY subid
        """
        job = self.bq.query(sql, job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ArrayQueryParameter("subids", "STRING", subids)]))
        out = {s: {"clicks": 0, "ftd": 0} for s in subids}
        for r in job.result():
            out[r["subid"]] = {"clicks": int(r["clicks"] or 0), "ftd": int(r["ftd"] or 0)}
        return out


def get_metrics_source():
    return BigQueryMetrics() if BACKEND == "bigquery" else LocalMetrics()
