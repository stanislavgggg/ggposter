"""
Слой 3 для email — отправка через ESP.

Бэкенды (env ESP_BACKEND):
  - local     : пишет письмо в data/sent_emails/<subid>.html (тест без ESP).
  - mailchimp : твой ESP. Создаёт regular-кампанию на list/segment, ставит контент, шлёт.

audience_cfg берётся из geo.email (list_id, сегменты warm/cold, from_name, reply_to).
"""
import os
import json
import requests

from .config import ROOT

BACKEND = os.environ.get("ESP_BACKEND", "local").lower()


class LocalESP:
    def __init__(self):
        self.dir = os.path.join(ROOT, "data", "sent_emails")
        os.makedirs(self.dir, exist_ok=True)

    def send(self, *, subject, preview_text, html, audience_cfg, subid):
        path = os.path.join(self.dir, f"{subid}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"<!-- subject: {subject} | preview: {preview_text} | "
                    f"list: {audience_cfg.get('list_id')} -->\n{html}")
        return {"id": f"local-{subid}", "status": "sent", "path": path}


class MailchimpESP:
    def __init__(self):
        self.key = os.environ["MAILCHIMP_API_KEY"]
        self.dc = self.key.split("-")[-1]            # data center из суффикса ключа
        self.base = f"https://{self.dc}.api.mailchimp.com/3.0"
        self.auth = ("anystring", self.key)
        self.from_name = os.environ.get("MAILCHIMP_FROM_NAME", "Team")
        self.reply_to = os.environ.get("MAILCHIMP_REPLY_TO", "")

    def _post(self, path, body):
        r = requests.post(self.base + path, auth=self.auth, json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def _put(self, path, body):
        r = requests.put(self.base + path, auth=self.auth, json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def send(self, *, subject, preview_text, html, audience_cfg, subid):
        recipients = {"list_id": audience_cfg["list_id"]}
        if audience_cfg.get("segment_id"):
            recipients["segment_opts"] = {"saved_segment_id": int(audience_cfg["segment_id"])}
        campaign = self._post("/campaigns", {
            "type": "regular",
            "recipients": recipients,
            "settings": {
                "subject_line": subject,
                "preview_text": preview_text,
                "title": f"auto-{subid}",
                "from_name": audience_cfg.get("from_name", self.from_name),
                "reply_to": audience_cfg.get("reply_to", self.reply_to),
            },
        })
        cid = campaign["id"]
        self._put(f"/campaigns/{cid}/content", {"html": html})
        self._post(f"/campaigns/{cid}/actions/send", {})
        return {"id": cid, "status": "sent"}


def get_esp():
    return MailchimpESP() if BACKEND == "mailchimp" else LocalESP()
