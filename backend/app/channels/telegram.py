"""Telegram channel. Women chat with the HerEvo bot in Bangla, by text or by voice note.
A voice note is transcribed, answered, and (if VOICE_REPLIES) answered back with a voice note too.
The trained helper sits in a separate Telegram chat and answers safety cases with:  /reply <case> <message>

Commands for women:  /start  /myrecord (her income history)  /delete (erase all her data)"""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

from .. import db, safety
from ..config import get_settings
from ..packs import load_pack
from ..pipeline import detect_language, handle
from ..speech import get_speech

log = logging.getLogger("herevo.telegram")

YES_WORDS = {"yes", "y", "ok", "হ্যাঁ", "হা", "হ্যা", "জি", "ঠিক আছে", "ndiyo", "sí", "si"}


class Sender(Protocol):
    def send(self, chat_id: int | str, text: str) -> None: ...

    def send_voice(self, chat_id: int | str, audio: bytes) -> None: ...

    def download(self, file_id: str) -> bytes | None: ...


class BotApiSender:
    def __init__(self, token: str):
        self.api = f"https://api.telegram.org/bot{token}"
        self.files = f"https://api.telegram.org/file/bot{token}"

    def send(self, chat_id, text):
        try:
            httpx.post(f"{self.api}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
        except httpx.HTTPError as exc:
            log.error("telegram send failed: %s", exc)

    def send_voice(self, chat_id, audio):
        try:
            httpx.post(f"{self.api}/sendVoice", data={"chat_id": str(chat_id)},
                       files={"voice": ("reply.ogg", audio, "audio/ogg")}, timeout=30)
        except httpx.HTTPError as exc:
            log.error("telegram voice send failed: %s", exc)

    def download(self, file_id):
        try:
            info = httpx.get(f"{self.api}/getFile", params={"file_id": file_id}, timeout=10).json()
            path = info["result"]["file_path"]
            return httpx.get(f"{self.files}/{path}", timeout=30).content
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.error("telegram download failed: %s", exc)
            return None


_sender: Sender | None = None


def get_sender() -> Sender:
    global _sender
    if _sender is None:
        _sender = BotApiSender(get_settings().telegram_bot_token)
    return _sender


def set_sender(s: Sender | None) -> None:  # used by tests
    global _sender
    _sender = s


def _text(pack, key, lang):
    return pack["messages"][key].get(lang) or pack["messages"][key]["en"]


VOICE_UNAVAILABLE = "ভয়েস বার্তা এখনো চালু হয়নি, অনুগ্রহ করে লিখে পাঠান। / Voice messages are not available yet, please type."
VOICE_TOO_LONG = "অনুগ্রহ করে ১ মিনিটের ছোট ভয়েস বার্তা পাঠান। / Please send a voice message shorter than 1 minute."
VOICE_UNCLEAR = "দুঃখিত, বুঝতে পারিনি। আবার বলুন বা লিখে পাঠান। / Sorry, I could not understand. Please say it again or type."


def _voice_to_text(msg: dict) -> tuple[str | None, str | None]:
    """Returns (text, error_message)."""
    voice = msg["voice"]
    speech = get_speech()
    if speech is None:
        return None, VOICE_UNAVAILABLE
    if voice.get("duration", 0) > get_settings().max_voice_seconds:
        return None, VOICE_TOO_LONG
    audio = get_sender().download(voice["file_id"])
    text = speech.transcribe(audio, "bn") if audio else None
    return (text, None) if text else (None, VOICE_UNCLEAR)


TEST_NOTICE = ("⚠️ পরীক্ষামূলক সংস্করণ: আসল নাম, ঠিকানা বা ব্যক্তিগত ঘটনা লিখবেন না, বানানো উদাহরণ ব্যবহার করুন।\n"
               "⚠️ Test version: do not share real names, places or personal stories. Use made-up examples.\n\n")


def _welcome(pack) -> str:
    return (TEST_NOTICE if get_settings().is_test else "") + _text(pack, "welcome", "bn")


def handle_update(update: dict) -> None:
    msg = update.get("message") or update.get("edited_message")
    if not msg or ("text" not in msg and "voice" not in msg):
        return  # photos, stickers etc. are ignored
    chat_id = msg["chat"]["id"]
    s = get_settings()
    by_voice = "voice" in msg and "text" not in msg
    if by_voice:
        text, err = _voice_to_text(msg)
        if err:
            get_sender().send(chat_id, err)
            return
    else:
        text = msg["text"]
    text = text.strip()

    # Messages from the helpers' chat
    if s.helper_chat_id and str(chat_id) == str(s.helper_chat_id):
        _handle_helper(text)
        return

    pack = load_pack(s.default_country)
    cc = pack["country"]
    uid = db.user_id_for("telegram", str(chat_id))
    lang = detect_language(text) if not text.startswith("/") else "bn"
    user = db.upsert_user(uid, "telegram", cc, lang)
    db.set_address(uid, str(chat_id))
    sender = get_sender()

    if text.startswith("/start"):
        sender.send(chat_id, _welcome(pack))
        return
    if text.startswith("/delete"):
        db.delete_user(uid)
        sender.send(chat_id, "আপনার সব তথ্য মুছে ফেলা হয়েছে। / All your data has been deleted.")
        return
    if text.startswith("/myrecord"):
        rows = db.ledger(uid)
        if not rows:
            sender.send(chat_id, "এখনো কোনো বিক্রির হিসাব নেই। / No sales recorded yet.")
        else:
            total = sum(float(r["amount"]) for r in rows)
            lines = [f"{r['created_at']:%d %b}: {r['product'] or '—'} {float(r['amount']):,.0f} {r['currency']}" for r in rows]
            sender.send(chat_id, "আপনার আয়ের হিসাব / Your income record:\n" + "\n".join(lines) +
                        f"\nমোট / Total: {total:,.0f} {rows[0]['currency']}")
        return

    # Safety is never gated behind consent.
    if not user["consented"] and not safety.screen(text).escalate:
        if text.lower() in YES_WORDS:
            db.set_consent(uid, True)
            sender.send(chat_id, "ধন্যবাদ। এখন আপনার প্রশ্ন লিখুন। / Thank you. Now type your question.")
        else:
            sender.send(chat_id, _welcome(pack))
        return

    result = handle(uid=uid, channel="telegram-voice" if by_voice else "telegram", text=text, country=cc,
                    district=user.get("district"))
    sender.send(chat_id, result.reply)
    if by_voice and s.voice_replies and get_speech() is not None:
        audio = get_speech().synthesize(result.reply, result.reply_language)
        if audio:
            sender.send_voice(chat_id, audio)


def _handle_helper(text: str) -> None:
    """/reply <case_id> <message>: the helper answers a woman without ever seeing her identity."""
    sender = get_sender()
    helper = get_settings().helper_chat_id
    if not text.startswith("/reply"):
        sender.send(helper, "Use: /reply <case number> <your message>")
        return
    parts = text.split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        sender.send(helper, "Use: /reply <case number> <your message>")
        return
    case = db.escalation(int(parts[1]))
    if not case:
        sender.send(helper, f"Case {parts[1]} not found.")
        return
    user = db.get_user(case["user_id"])
    if not user or not user.get("address"):
        sender.send(helper, f"Case {parts[1]}: she deleted her data or cannot be reached.")
        return
    sender.send(user["address"], "HerEvo সহায়তাকর্মী / helper: " + parts[2])
    db.mark_contacted(case["id"])
    sender.send(helper, f"Sent to case {case['id']}.")
