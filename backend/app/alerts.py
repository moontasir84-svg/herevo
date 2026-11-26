"""Alerts to the trained human helper when the safety screen escalates. The helper never sees the
woman's phone number or Telegram id: only a case number, her district and her message."""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

from .config import get_settings

log = logging.getLogger("herevo.alerts")


class Notifier(Protocol):
    def notify_helper(self, text: str, reply_to: int | None = None) -> bool: ...


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def notify_helper(self, text: str, reply_to: int | None = None) -> bool:
        payload = {"chat_id": self.chat_id, "text": text}
        if reply_to:  # thread it under the helper's own message in the group
            payload["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        try:
            r = httpx.post(self.url, json=payload, timeout=10)
            if r.status_code == 400:
                # Telegram moved the group to a "supergroup", which has a new id: follow it, and say so.
                new_id = (r.json().get("parameters") or {}).get("migrate_to_chat_id")
                if new_id:
                    log.warning("Helper group moved to a new id. Set HELPER_CHAT_ID=%s in Render.", new_id)
                    self.chat_id = str(new_id)
                    r = httpx.post(self.url, json={"chat_id": self.chat_id, "text": text}, timeout=10)
            if r.status_code != 200:
                log.error("helper alert failed: HTTP %s %s", r.status_code, r.text[:200])
            return r.status_code == 200
        except (httpx.HTTPError, ValueError) as exc:  # never let an alert failure hide the escalation
            log.error("helper alert failed: %s", exc)
            return False


class LogNotifier:
    """Fallback when no helper channel is configured: the alert goes to the server log."""

    def __init__(self):
        self.sent: list[str] = []

    def notify_helper(self, text: str, reply_to: int | None = None) -> bool:
        self.sent.append(text)
        log.warning("SAFETY ALERT (no helper channel configured): %s", text)
        return False  # nobody was actually alerted


_notifier: Notifier | None = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        s = get_settings()
        _notifier = TelegramNotifier(s.telegram_bot_token, s.helper_chat_id) if (
            s.telegram_bot_token and s.helper_chat_id) else LogNotifier()
    return _notifier


def set_notifier(n: Notifier | None) -> None:  # used by tests
    global _notifier
    _notifier = n


def helper_chat_ids() -> set[str]:
    """The configured helper chat id, plus its new id if Telegram moved the group."""
    ids = {str(get_settings().helper_chat_id)} if get_settings().helper_chat_id else set()
    if isinstance(_notifier, TelegramNotifier):
        ids.add(str(_notifier.chat_id))
    return ids
