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
  Published link (url/text) · Channel (text, ПОДСКАЗКА: доступные назначения) ·
  Channels (text, ВЫБОР менеджера: куда постить — метки через запятую, либо «все»)

Без авторассылки: для telegram пост уходит ТОЛЬКО в назначения, перечисленные в
поле Channels. Пусто -> не публикуем (строка ждёт, пока менеджер заполнит Channels).
"""
import time

from .store import get_store
from .config import load_geo, telegram_destinations
from .airtable import Airtable
from .deliver import deliver
from .publish import load_published_to


def _dest_hint(geo_name):
    """Список доступных назначений гео -> строка-подсказка для поля Channel."""
    try:
        return "; ".join(d["label"] for d in telegram_destinations(load_geo(geo_name)))
    except Exception:
        return ""


def _resolve_dests(geo, raw):
    """Строка из поля Channels менеджера -> список назначений. 'all'/'все' = все.
    Токены матчатся по label / id / slug (без регистра). Неизвестные игнорируются."""
    dests = telegram_destinations(geo)
    if not raw:
        return []
    tokens = [t.strip() for t in str(raw).replace("\n", ",").replace(";", ",").split(",") if t.strip()]
    if any(t.casefold() in ("all", "все", "*") for t in tokens):
        return dests
    chosen, seen = [], set()
    for t in tokens:
        tc = t.casefold()
        for d in dests:
            if d["slug"] in seen:
                continue
            if tc in (str(d["id"]).casefold(), d["slug"].casefold(), d["label"].casefold()):
                chosen.append(d); seen.add(d["slug"])
    return chosen


def _preview_fields(draft):
    flag = "OK ✅" if draft.get("compliance_ok") else f"⚠️ {draft.get('compliance_notes','')}"
    f = {
        "Draft ID": draft["draft_id"],
        "Kind": draft.get("kind", "telegram"),
        "Match": draft["match_id"],
        "GEO": draft["geo"],
        "Channel": _dest_hint(draft["geo"]) or draft.get("channel_id", ""),
        "Compliance": flag,
        "Hook rationale": draft.get("hook_rationale", ""),
        "Status": "Pending",
        "Processed": False,
    }
    if draft.get("kind") == "email":
        f.update({"Subject A": draft.get("subject_a", ""), "Subject B": draft.get("subject_b", ""),
                  "Preview text": draft.get("preview_text", ""), "Audience": draft.get("audience", "warm"),
                  "Post A": draft.get("body_html", "")})       # тело в Post A для ревью
    else:
        f.update({"Post A": draft.get("post_a", ""), "Post B": draft.get("post_b", "")})
    return f


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
                draft = self.store.get_draft(draft_id)
                if not draft:
                    self.at.update(rec["id"], {"Processed": True,
                                              "Published link": "⚠ draft не найден"})
                    continue

                updates = {}
                if f.get("Variant"):
                    updates["chosen_variant"] = f["Variant"]
                if f.get("Edited text"):
                    updates["edited_text"] = f["Edited text"]
                if updates:
                    self.store.update_draft(draft_id, **updates)

                if draft.get("kind") == "email":
                    deliver(draft_id)                          # рассылка через ESP
                    self.at.update(rec["id"], {"Processed": True, "Published link": "email отправлен"})
                    continue

                # telegram: публикуем ТОЛЬКО в выбранные каналы (поле Channels). Без авторассылки.
                geo = load_geo(draft["geo"])
                dests = _resolve_dests(geo, f.get("Channels"))
                if not dests:
                    # каналы не выбраны -> не публикуем и НЕ помечаем Processed (менеджер дозаполнит).
                    # Подсказку пишем в грид (а не только в лог), без повторной записи.
                    hint = f"⚠ укажи Channels: {_dest_hint(draft['geo'])}"
                    if f.get("Published link") != hint:
                        self.at.update(rec["id"], {"Published link": hint})
                        print(f"[airtable] {draft_id}: {hint}")
                    continue
                for d in dests:
                    deliver(draft_id, dest=d)
                pub = self.store.get_draft(draft_id)
                links = pub.get("published_links") or ", ".join(
                    p.get("link", "") for p in load_published_to(pub))
                self.at.update(rec["id"], {"Processed": True, "Published link": links})
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
