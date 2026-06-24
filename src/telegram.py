"""Тонкий клиент Telegram Bot API (raw requests, без тяжёлых зависимостей)."""
import os
import requests

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token=None):
        self.token = token or os.environ["TELEGRAM_BOT_TOKEN"]

    def _call(self, method, **params):
        url = API.format(token=self.token, method=method)
        params = {k: v for k, v in params.items() if v is not None}
        r = requests.post(url, json=params, timeout=30)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data}")
        return data["result"]

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML",
                     disable_web_page_preview=True):
        return self._call(
            "sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
            reply_markup=reply_markup,
        )

    def edit_reply_markup(self, chat_id, message_id, reply_markup=None):
        return self._call("editMessageReplyMarkup", chat_id=chat_id,
                          message_id=message_id, reply_markup=reply_markup)

    def answer_callback(self, callback_query_id, text=None):
        return self._call("answerCallbackQuery",
                          callback_query_id=callback_query_id, text=text or "")

    def get_updates(self, offset=None, timeout=25):
        return self._call("getUpdates", offset=offset, timeout=timeout,
                          allowed_updates=["callback_query", "message"])


def inline_keyboard(rows):
    """rows: list[list[(text, callback_data)]] -> reply_markup dict."""
    return {"inline_keyboard": [
        [{"text": t, "callback_data": cb} for (t, cb) in row] for row in rows
    ]}
