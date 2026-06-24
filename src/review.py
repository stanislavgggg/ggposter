"""
Единый вход для поверхности ревью. Выбор через env REVIEW_SURFACE:
  telegram (по умолчанию) -> bot.py    (аппрув в личке, кнопки A/B)
  airtable                -> bridge    (аппрув в гриде Airtable)

Публикация в обоих случаях идёт через Telegram Bot API (нужен только токен).
"""
import os

from . import _bootstrap  # noqa: F401  — env→файл GCP-креды; ДОЛЖЕН идти первым


def main():
    surface = os.environ.get("REVIEW_SURFACE", "telegram").lower()
    if surface == "airtable":
        from .airtable_bridge import AirtableBridge
        AirtableBridge(poll_every=int(os.environ.get("POLL_PENDING_EVERY", "10"))).loop()
    else:
        from .bot import ApprovalBot
        ApprovalBot().loop()

if __name__ == "__main__":
    main()
