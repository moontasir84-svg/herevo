"""Point the Telegram bot at the deployed API.

Usage (once, after deploying):
    TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... python scripts/set_telegram_webhook.py https://herevo-api.onrender.com
"""
import os
import sys

import httpx

base = sys.argv[1].rstrip("/")
token = os.environ["TELEGRAM_BOT_TOKEN"]
secret = os.environ["TELEGRAM_WEBHOOK_SECRET"]
r = httpx.post(f"https://api.telegram.org/bot{token}/setWebhook",
               json={"url": f"{base}/telegram/webhook/{secret}", "allowed_updates": ["message", "edited_message"]},
               timeout=20)
print(r.status_code, r.json())
