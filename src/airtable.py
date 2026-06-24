"""Тонкий клиент Airtable REST API (create / list / update записей)."""
import os
import requests

BASE = "https://api.airtable.com/v0/{base}/{table}"


class Airtable:
    def __init__(self, token=None, base_id=None, table=None):
        self.token = token or os.environ["AIRTABLE_TOKEN"]
        self.base_id = base_id or os.environ["AIRTABLE_BASE_ID"]
        self.table = table or os.environ.get("AIRTABLE_TABLE", "Drafts")
        self.headers = {"Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json"}

    def _url(self, rec_id=None):
        url = BASE.format(base=self.base_id, table=self.table)
        return f"{url}/{rec_id}" if rec_id else url

    def create(self, fields):
        r = requests.post(self._url(), headers=self.headers,
                          json={"fields": fields, "typecast": True}, timeout=30)
        r.raise_for_status()
        return r.json()["id"]

    def update(self, rec_id, fields):
        r = requests.patch(self._url(rec_id), headers=self.headers,
                           json={"fields": fields, "typecast": True}, timeout=30)
        r.raise_for_status()
        return r.json()

    def list(self, formula=None, page_size=100):
        params = {"pageSize": page_size}
        if formula:
            params["filterByFormula"] = formula
        out, offset = [], None
        while True:
            if offset:
                params["offset"] = offset
            r = requests.get(self._url(), headers=self.headers, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            out.extend(data.get("records", []))
            offset = data.get("offset")
            if not offset:
                return out
