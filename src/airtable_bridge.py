"""
Airtable как поверхность ревью (альтернатива bot.py).

BigQuery/local остаётся ЕДИНЫМ источником правды. Airtable — окно ревью:
  push    : новые pending-драфты -> создаём записи в Airtable (Status=Pending).
  poll    : записи со Status=Approved/Rejected и Processed != true ->
              Approved -> прокидываем правки в стор -> публикуем -> пишем ссылку обратно.
              Rejected -> помечаем в сторе.

Менеджер работает в гриде: видит всё разом, правит в ячейке, дропдаун статуса.
chat_id не нужен — токен бота используется только для постинга в канал.

Поля таблицы Airtable (создай в базе, см. README):
  Draft ID (text) · Match (text) · GEO (text) · Compliance (text) ·
  Post A (long text) · Post B (long text) · Hook rationale (text) ·
  Variant (single select: A/B) · Edited text (long text) ·
  Status (single select: Pending/Approved/Rejected) · Processed (checkbox) ·
  Published link (url) · Channel (text)
"""
import time

from .store import get_store
from .airtable import Airtable
from .publish import publish_draft


def _preview_fields(draft):
    flag = "OK ✅" if draft.get("compliance_ok") else f"⚠️ {draft.get('compliance_notes','')}"
    return {
        "Draft ID": draft["draft_id"],
        "Match": draft["match_id"],
        "GEO": draft["geo"],
        "Channel": draft.get("channel_id", ""),
        "Compliance": flag,
        "Post A": draft.get("post_a", ""),
        "Post B": draft.get("post_b", ""),
        "Hook rationale": draft.get("hook_rationale", ""),
        "Status": "Pending",
        "Processed": False,
    }


class AirtableBridge:
    def __init__(self, poll_every=10, at=None):
        self.store = get_store()
        self.at = at or Airtable()
        self.poll_every = poll_every

    def push_pending(self):
        for draft in self.store.get_pending_unnotified():
            rec_id = self.at.create(_preview_fields(draft))
            self.store.update_draft(draft["draft_id"], notified=True,
                                    airtable_record_id=rec_id)

    def process_decisions(self):
        formula = "AND(OR({Status}='Approved',{Status}='Rejected'),NOT({Processed}))"
        for rec in self.at.list(formula=formula):
            f = rec.get("fields", {})
            draft_id = f.get("Draft ID")
            if not draft_id:
                continue
            status = f.get("Status")

            if status == "Approved":
                # прокинуть выбор/правку менеджера в стор
                updates = {}
                if f.get("Variant"):
                    updates["chosen_variant"] = f["Variant"]
                if f.get("Edited text"):
                    updates["edited_text"] = f["Edited text"]
                if updates:
                    self.store.update_draft(draft_id, **updates)
                publish_draft(draft_id)                       # постит в канал
                pub = self.store.get_draft(draft_id)
                self.at.update(rec["id"], {
                    "Processed": True,
                    "Published link": f"https://t.me/{str(pub.get('channel_id','')).lstrip('@')}",
                })
            elif status == "Rejected":
                self.store.update_draft(draft_id, status="rejected")
                self.at.update(rec["id"], {"Processed": True})

    def loop(self):
        print("AirtableBridge запущен. Зеркалю драфты и слушаю статусы...")
        while True:
            try:
                self.push_pending()
                self.process_decisions()
            except Exception as e:
                print("bridge error:", e)
            time.sleep(self.poll_every)


if __name__ == "__main__":
    import os
    AirtableBridge(poll_every=int(os.environ.get("POLL_PENDING_EVERY", "10"))).loop()
