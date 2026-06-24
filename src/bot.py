"""
Очередь аппрува — ВСЕГДА-ВКЛЮЧЁННЫЙ процесс (Railway worker / long-running).

Что делает в цикле:
  1. Забирает новые pending-драфты из стора -> шлёт админу превью с кнопками.
  2. Слушает callback'и кнопок:
        ✅ A / ✅ B  -> approve выбранный вариант -> публикация в канал
        ❌ Reject    -> reject
  3. Правка: ответь (reply) на сообщение-превью своим текстом ->
     он становится edited_text и сразу публикуется.

Менеджер = редактор-аппрувер, не автор. В три гео руками не пишет.
"""
import os
import time
import html

from .store import get_store
from .telegram import Telegram, inline_keyboard
from .deliver import deliver

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
POLL_PENDING_EVERY = int(os.environ.get("POLL_PENDING_EVERY", "10"))  # сек


def _preview(draft):
    flag = "✅" if draft.get("compliance_ok") else "⚠️"
    notes = "" if draft.get("compliance_ok") else f"\n⚠️ Compliance: {draft.get('compliance_notes')}"
    head = (f"🆕 <b>Черновик</b> {draft['draft_id']} · {draft.get('kind','telegram')}"
            f" · GEO={draft['geo']} · комплаенс {flag}{notes}\n"
            f"<i>{html.escape(draft.get('hook_rationale',''))}</i>\n")
    if draft.get("kind") == "email":
        return (head + f"audience: {draft.get('audience','warm')}\n\n"
                f"<b>Subject A:</b> {html.escape(draft.get('subject_a',''))}\n"
                f"<b>Subject B:</b> {html.escape(draft.get('subject_b',''))}\n"
                f"<b>Preview:</b> {html.escape(draft.get('preview_text',''))}\n\n"
                f"<b>Body:</b>\n{html.escape(draft.get('body_html',''))[:1500]}")
    return (head + f"\n<b>— Вариант A —</b>\n{html.escape(draft.get('post_a',''))}\n\n"
            f"<b>— Вариант B —</b>\n{html.escape(draft.get('post_b',''))}")


def _buttons(draft_id, kind="telegram"):
    a, b = ("✅ Отправить A", "✅ Отправить B") if kind == "email" else ("✅ Опубликовать A", "✅ Опубликовать B")
    return inline_keyboard([
        [(a, f"ap:A:{draft_id}"), (b, f"ap:B:{draft_id}")],
        [("❌ Отклонить", f"rj:{draft_id}")],
    ])


class ApprovalBot:
    def __init__(self):
        self.tg = Telegram()
        self.store = get_store()
        self.offset = None
        self.msg_to_draft = {}   # notify_msg_id -> draft_id (для правок реплаем)

    def push_pending(self):
        for draft in self.store.get_pending_unnotified():
            try:
                if draft.get("kind", "telegram") == "email":
                    self._push_email(draft)
                else:
                    self._push_telegram(draft)
                self.store.update_draft(draft["draft_id"], notified=True)
            except Exception as e:
                print("push error for", draft.get("draft_id"), ":", e)

    def _push_email(self, draft):
        msg = self.tg.send_message(
            ADMIN_CHAT_ID, _preview(draft),
            reply_markup=_buttons(draft["draft_id"], "email"),
        )
        self.msg_to_draft[msg["message_id"]] = draft["draft_id"]

    def _push_telegram(self, draft):
        """Шапка + каждый вариант ОТДЕЛЬНЫМ чистым сообщением (удобно копировать)."""
        did = draft["draft_id"]
        flag = "✅" if draft.get("compliance_ok") else "⚠️"
        notes = "" if draft.get("compliance_ok") else f"\n⚠️ {draft.get('compliance_notes', '')}"
        header = (f"🆕 Черновик {did} · GEO={draft['geo']} · комплаенс {flag}{notes}\n"
                  f"{draft.get('hook_rationale', '')}")
        self.tg.send_message(ADMIN_CHAT_ID, header, parse_mode=None)

        for variant in ("A", "B"):
            text = draft.get("post_a") if variant == "A" else draft.get("post_b")
            if not text:
                continue
            # raw текст (parse_mode=None) -> копируется чисто, без HTML-мусора
            msg = self.tg.send_message(
                ADMIN_CHAT_ID, text, parse_mode=None,
                reply_markup=inline_keyboard([
                    [(f"✅ Опубликовать {variant}", f"ap:{variant}:{did}")],
                    [("❌ Отклонить", f"rj:{did}")],
                ]),
            )
            self.msg_to_draft[msg["message_id"]] = did

    def handle_callback(self, cq):
        data = cq["data"]
        chat_id = cq["message"]["chat"]["id"]
        msg_id = cq["message"]["message_id"]

        if data.startswith("ap:"):
            _, variant, draft_id = data.split(":", 2)
            self.store.update_draft(draft_id, status="approved",
                                    chosen_variant=variant)
            out = deliver(draft_id, tg=self.tg)
            self.tg.answer_callback(cq["id"], f"Отправлено ({out})")
            self.tg.edit_reply_markup(chat_id, msg_id, reply_markup=None)
            self.tg.send_message(chat_id, f"✅ {draft_id}: отправлен вариант {variant}")
        elif data.startswith("rj:"):
            draft_id = data.split(":", 1)[1]
            self.store.update_draft(draft_id, status="rejected")
            self.tg.answer_callback(cq["id"], "Отклонено")
            self.tg.edit_reply_markup(chat_id, msg_id, reply_markup=None)
            self.tg.send_message(chat_id, f"❌ {draft_id}: отклонён")

    def handle_message(self, msg):
        """Правка реплаем на превью -> edited_text + публикация."""
        reply = msg.get("reply_to_message")
        if not reply:
            return
        draft_id = self.msg_to_draft.get(reply["message_id"])
        if not draft_id:
            return
        self.store.update_draft(draft_id, status="approved",
                                edited_text=msg["text"])
        out = deliver(draft_id, tg=self.tg)
        self.tg.send_message(msg["chat"]["id"],
                             f"✏️ {draft_id}: отправлен твой отредактированный текст ({out})")

    def loop(self):
        print("ApprovalBot запущен. Жду драфты и нажатия...")
        last_push = 0
        while True:
            now = time.time()
            if now - last_push >= POLL_PENDING_EVERY:
                try:
                    self.push_pending()
                except Exception as e:
                    print("push_pending error:", e)
                last_push = now

            try:
                updates = self.tg.get_updates(offset=self.offset, timeout=10)
            except Exception as e:
                print("get_updates error:", e)
                time.sleep(3)
                continue

            for upd in updates:
                self.offset = upd["update_id"] + 1
                try:
                    if "callback_query" in upd:
                        self.handle_callback(upd["callback_query"])
                    elif "message" in upd and "text" in upd["message"]:
                        self.handle_message(upd["message"])
                except Exception as e:
                    print("handle update error:", e)


if __name__ == "__main__":
    ApprovalBot().loop()
