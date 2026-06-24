"""
Очередь аппрува — ВСЕГДА-ВКЛЮЧЁННЫЙ процесс (Railway review).

Менеджер = редактор-аппрувер, не автор. В N гео руками не пишет.

Поток для Telegram-поста (двухшаговый, БЕЗ авторассылки):
  1. Бот шлёт шапку + каждый вариант (A/B) отдельным чистым сообщением с кнопками
     [✅ Опубликовать A] [❌ Отклонить].
  2. Менеджер жмёт ✅ -> бот сохраняет выбор и показывает КНОПКИ НАЗНАЧЕНИЙ
     (каналы/паблики гео + «Во все» + «Готово»). Менеджер сам выбирает, куда постить.
  3. Тап по назначению -> публикация туда (идемпотентно), уже опубликованные помечаются ✓.
  Правка: reply на превью своим текстом -> текст становится edited_text, дальше тот же выбор каналов.

Поток для email: один тап [✅ Отправить A/B] -> рассылка через ESP (аудитория из конфига).
"""
import os
import time
import html

from .store import get_store
from .config import load_geo, telegram_destinations
from .telegram import Telegram, inline_keyboard
from .deliver import deliver
from .publish import load_published_to

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
POLL_PENDING_EVERY = int(os.environ.get("POLL_PENDING_EVERY", "10"))  # сек


def _dest_keyboard(draft_id, variant, geo, done_slugs):
    """Кнопки выбора назначения. Уже опубликованные помечены ✓."""
    dests = telegram_destinations(geo)
    rows = []
    for idx, d in enumerate(dests):
        done = d["slug"] in done_slugs
        mark = "✅ " if done else "📢 "
        rows.append([(f"{mark}{d['label']}", f"pb:{variant}:{idx}:{draft_id}")])
    remaining = [d for d in dests if d["slug"] not in done_slugs]
    if len(dests) > 1 and len(remaining) > 1:
        rows.append([("📢 Во все каналы", f"pb:{variant}:all:{draft_id}")])
    rows.append([("✔️ Готово", f"dn:{draft_id}")])
    return inline_keyboard(rows)


class ApprovalBot:
    def __init__(self):
        self.tg = Telegram()
        self.store = get_store()
        self.offset = None
        self.msg_to_draft = {}     # notify_msg_id -> draft_id (для правок реплаем)
        self.variant_msgs = {}     # draft_id -> {"A": msg_id, "B": msg_id} (чтобы гасить соседа)

    # ---- отправка превью ----------------------------------------------------
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
        flag = "✅" if draft.get("compliance_ok") else "⚠️"
        notes = "" if draft.get("compliance_ok") else f"\n⚠️ {draft.get('compliance_notes','')}"
        body = (f"🆕 <b>Письмо</b> {draft['draft_id']} · GEO={draft['geo']} · комплаенс {flag}{notes}\n"
                f"<i>{html.escape(draft.get('hook_rationale',''))}</i>\n"
                f"audience: {draft.get('audience','warm')}\n\n"
                f"<b>Subject A:</b> {html.escape(draft.get('subject_a',''))}\n"
                f"<b>Subject B:</b> {html.escape(draft.get('subject_b',''))}\n"
                f"<b>Preview:</b> {html.escape(draft.get('preview_text',''))}\n\n"
                f"<b>Body:</b>\n{html.escape(draft.get('body_html',''))[:1500]}")
        kb = inline_keyboard([
            [("✅ Отправить A", f"ap:A:{draft['draft_id']}"),
             ("✅ Отправить B", f"ap:B:{draft['draft_id']}")],
            [("❌ Отклонить", f"rj:{draft['draft_id']}")],
        ])
        msg = self.tg.send_message(ADMIN_CHAT_ID, body, reply_markup=kb)
        self.msg_to_draft[msg["message_id"]] = draft["draft_id"]

    def _push_telegram(self, draft):
        """Шапка + каждый вариант отдельным чистым сообщением (удобно копировать)."""
        did = draft["draft_id"]
        flag = "✅" if draft.get("compliance_ok") else "⚠️"
        notes = "" if draft.get("compliance_ok") else f"\n⚠️ {draft.get('compliance_notes', '')}"
        header = (f"🆕 Черновик {did} · GEO={draft['geo']} · комплаенс {flag}{notes}\n"
                  f"{draft.get('hook_rationale', '')}")
        self.tg.send_message(ADMIN_CHAT_ID, header, parse_mode=None)

        self.variant_msgs[did] = {}
        for variant in ("A", "B"):
            text = draft.get("post_a") if variant == "A" else draft.get("post_b")
            if not text:
                continue
            msg = self.tg.send_message(
                ADMIN_CHAT_ID, text, parse_mode=None,
                reply_markup=inline_keyboard([
                    [(f"✅ Опубликовать {variant}", f"ap:{variant}:{did}")],
                    [("❌ Отклонить", f"rj:{did}")],
                ]),
            )
            self.msg_to_draft[msg["message_id"]] = did
            self.variant_msgs[did][variant] = msg["message_id"]

    # ---- callbacks ----------------------------------------------------------
    def handle_callback(self, cq):
        data = cq["data"]
        chat_id = cq["message"]["chat"]["id"]
        msg_id = cq["message"]["message_id"]

        if data.startswith("ap:"):
            _, variant, draft_id = data.split(":", 2)
            self._on_approve(cq, chat_id, msg_id, draft_id, variant)
        elif data.startswith("pb:"):
            _, variant, idx, draft_id = data.split(":", 3)
            self._on_publish(cq, chat_id, msg_id, draft_id, variant, idx)
        elif data.startswith("dn:"):
            draft_id = data.split(":", 1)[1]
            self.tg.edit_reply_markup(chat_id, msg_id, reply_markup=None)
            self.tg.answer_callback(cq["id"], "Готово")
        elif data.startswith("rj:"):
            draft_id = data.split(":", 1)[1]
            self.store.update_draft(draft_id, status="rejected")
            self.tg.answer_callback(cq["id"], "Отклонено")
            self.tg.edit_reply_markup(chat_id, msg_id, reply_markup=None)
            self.tg.send_message(chat_id, f"❌ {draft_id}: отклонён")

    def _on_approve(self, cq, chat_id, msg_id, draft_id, variant):
        draft = self.store.get_draft(draft_id)
        if not draft:
            self.tg.answer_callback(cq["id"], "Драфт не найден")
            return

        # email: подтверждение = сразу рассылка (назначение = аудитория из конфига)
        if draft.get("kind") == "email":
            self.store.update_draft(draft_id, status="approved", chosen_variant=variant)
            out = deliver(draft_id, tg=self.tg)
            self.tg.answer_callback(cq["id"], f"Отправлено ({out})")
            self.tg.edit_reply_markup(chat_id, msg_id, reply_markup=None)
            self.tg.send_message(chat_id, f"✅ {draft_id}: письмо отправлено (вариант {variant})")
            return

        # telegram: фиксируем вариант и показываем выбор назначения (без авторассылки)
        self.store.update_draft(draft_id, status="approved", chosen_variant=variant)
        self._disable_sibling(chat_id, draft_id, keep_variant=variant)
        geo = load_geo(draft["geo"])
        done = {p["slug"] for p in load_published_to(draft)}
        self.tg.edit_reply_markup(chat_id, msg_id,
                                  reply_markup=_dest_keyboard(draft_id, variant, geo, done))
        self.tg.answer_callback(cq["id"], f"Вариант {variant}. Выбери канал/паблик 👇")

    def _on_publish(self, cq, chat_id, msg_id, draft_id, variant, idx):
        draft = self.store.get_draft(draft_id)
        geo = load_geo(draft["geo"])
        dests = telegram_destinations(geo)

        try:
            if idx == "all":
                done_before = {p["slug"] for p in load_published_to(draft)}
                targets = [d for d in dests if d["slug"] not in done_before]
                for d in targets:
                    deliver(draft_id, tg=self.tg, dest=d)
                self.tg.answer_callback(cq["id"], f"Опубликовано во все ({len(targets)})")
                self.tg.edit_reply_markup(chat_id, msg_id, reply_markup=None)
            else:
                dest = dests[int(idx)]
                deliver(draft_id, tg=self.tg, dest=dest)
                self.tg.answer_callback(cq["id"], f"Опубликовано: {dest['label']}")
                fresh = self.store.get_draft(draft_id)
                done = {p["slug"] for p in load_published_to(fresh)}
                if len(done) >= len(dests):
                    self.tg.edit_reply_markup(chat_id, msg_id, reply_markup=None)
                else:
                    self.tg.edit_reply_markup(
                        chat_id, msg_id,
                        reply_markup=_dest_keyboard(draft_id, variant, geo, done))
        except Exception as e:
            self.tg.answer_callback(cq["id"], f"Ошибка: {e}"[:190])
            print("publish error", draft_id, idx, ":", e)

    def _disable_sibling(self, chat_id, draft_id, keep_variant):
        other = "B" if keep_variant == "A" else "A"
        mid = self.variant_msgs.get(draft_id, {}).get(other)
        if mid:
            try:
                self.tg.edit_reply_markup(chat_id, mid, reply_markup=None)
            except Exception:
                pass

    # ---- правка реплаем -----------------------------------------------------
    def handle_message(self, msg):
        reply = msg.get("reply_to_message")
        if not reply:
            return
        draft_id = self.msg_to_draft.get(reply["message_id"])
        if not draft_id:
            return
        draft = self.store.get_draft(draft_id)
        if not draft:
            return

        if draft.get("kind") == "email":
            self.store.update_draft(draft_id, status="approved", edited_text=msg["text"])
            out = deliver(draft_id, tg=self.tg)
            self.tg.send_message(msg["chat"]["id"],
                                 f"✏️ {draft_id}: отправлен твой отредактированный текст ({out})")
            return

        # telegram: сохранить правку и предложить выбор назначения
        self.store.update_draft(draft_id, status="approved", edited_text=msg["text"])
        geo = load_geo(draft["geo"])
        done = {p["slug"] for p in load_published_to(draft)}
        self.tg.send_message(
            msg["chat"]["id"],
            f"✏️ {draft_id}: правка принята. Куда опубликовать?",
            reply_markup=_dest_keyboard(draft_id, "E", geo, done))

    # ---- цикл ---------------------------------------------------------------
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
