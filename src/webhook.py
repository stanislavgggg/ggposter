"""
Событийный триггер (Слой 1). Альтернатива крону: вешаешь в Apify на актор
вебхук 'Actor finished' -> POST на этот эндпоинт -> сразу прогоняем петлю.
Так система реагирует на инфоповод за минуты, а не ждёт следующего тика крона.

Запуск: python -m src.webhook   (PORT, WEBHOOK_SECRET из env)
Apify webhook URL: https://<твой-railway-домен>/run?secret=<WEBHOOK_SECRET>
"""
import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from .orchestrate import run_once

SECRET = os.environ.get("WEBHOOK_SECRET", "")
PORT = int(os.environ.get("PORT", "8080"))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/run":
            return self._send(404, {"error": "not found"})
        if SECRET and parse_qs(u.query).get("secret", [""])[0] != SECRET:
            return self._send(401, {"error": "bad secret"})
        try:
            n = run_once()
            self._send(200, {"ok": True, "new_drafts": n})
        except Exception as e:
            self._send(500, {"error": str(e)})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print(f"webhook listening on :{PORT}  (POST /run, GET /health)")
    HTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
