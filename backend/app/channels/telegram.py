"""Telegram channel. Women chat with the HerEvo bot in Bangla, by text or by voice note.
A voice note is transcribed, answered, and (if VOICE_REPLIES) answered back with a voice note too.
The trained helper sits in a separate Telegram chat and answers safety cases with:  /reply <case> <message>

Commands for women:  /start  /myrecord (her income history)  /delete (erase all her data)"""
from __future__ import annotations

import logging
import re
from typing import Protocol

import httpx

from .. import db, safety
from ..config import get_settings
from ..packs import load_pack
from ..alerts import helper_chat_ids
from ..pipeline import detect_language, handle, requested_language
from ..speech import get_speech

log = logging.getLogger("herevo.telegram")

YES_WORDS = {"yes", "y", "ok", "okay", "yes please", "i agree", "agree", "হ্যাঁ", "হা", "হ্যা", "জি", "জি হ্যাঁ",
             "ঠিক আছে", "রাজি", "haan", "ha", "ji", "ndiyo", "ndio", "sawa", "sí", "si", "de acuerdo"}


def _is_yes(text: str) -> bool:
    return text.lower().strip(" .!?।'\"") in YES_WORDS


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


COMMANDS_HELP = ("যা লিখতে পারেন / What you can type:\n"
                 "/myrecord – আপনার আয়ের হিসাব / your income record\n"
                 "/delete – আপনার সব তথ্য মুছুন / erase all your data\n"
                 "/start – শুরু থেকে / start again\n"
                 "অথবা যেকোনো প্রশ্ন লিখুন বা বলুন। / Or just type or say any question.")

TEST_NOTICE = ("⚠️ পরীক্ষামূলক সংস্করণ: আসল নাম, ঠিকানা বা ব্যক্তিগত ঘটনা লিখবেন না, বানানো উদাহরণ ব্যবহার করুন।\n"
               "⚠️ Test version: do not share real names, places or personal stories. Use made-up examples.\n\n")


def _welcome(pack, lang: str) -> str:
    return (TEST_NOTICE if get_settings().is_test else "") + _text(pack, "welcome", lang)


def _gate_language(pack, uid: str, text: str, channel_country: str) -> tuple[str, bool]:
    """The language to talk to her in before she has agreed, and whether she just asked for it."""
    asked = requested_language(text)
    if asked:
        db.set_preferred_language(uid, "telegram", channel_country, asked)
        return asked, True
    if text.startswith("/"):
        return db.preferred_language(uid) or pack["languages"][0], False
    return db.preferred_language(uid) or detect_language(text), False


BN = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def format_reply(result) -> str:
    """Her answer, then the numbered next steps."""
    if not result.steps:
        return result.reply
    nums = [str(i).translate(BN) if result.reply_language == "bn" else str(i) for i in range(1, len(result.steps) + 1)]
    return result.reply + "\n\n" + "\n".join(f"{n}. {step}" for n, step in zip(nums, result.steps))


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
    if str(chat_id) in helper_chat_ids():
        _handle_helper(text, chat_id, msg.get("reply_to_message"), msg.get("message_id"))
        return
    if msg["chat"].get("type", "private") != "private":
        return  # HerEvo talks to women one to one; any other group the bot is added to is ignored

    pack = load_pack(s.default_country)
    cc = pack["country"]
    uid = db.user_id_for("telegram", str(chat_id))
    is_new = db.get_user(uid) is None
    lang = detect_language(text) if not text.startswith("/") else pack["languages"][0]
    user = db.upsert_user(uid, "telegram", cc, lang)
    db.set_address(uid, str(chat_id))
    sender = get_sender()

    if text.startswith("/start"):
        sender.send(chat_id, _welcome(pack, _gate_language(pack, uid, text, cc)[0]))
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

    if text.startswith("/"):
        # Any other command: show hers. (/reply is for helpers, in the helpers' group only.)
        sender.send(chat_id, COMMANDS_HELP)
        return

    # Safety is never gated behind consent.
    if not user["consented"] and not safety.screen(text).escalate:
        glang, asked = _gate_language(pack, uid, text, cc)
        if _is_yes(text):
            db.set_consent(uid, True)
            sender.send(chat_id, _text(pack, "consent_thanks", db.preferred_language(uid) or glang))
        elif is_new or asked:
            sender.send(chat_id, _welcome(pack, glang))  # the full notice, in her language
        else:
            sender.send(chat_id, _text(pack, "consent_reminder", glang))  # short, not the whole notice again
        return

    # She swiped to reply on a helper's message in her chat: it goes back to that helper, threaded.
    answered = None
    rt = msg.get("reply_to_message") or {}
    if (rt.get("from") or {}).get("is_bot") and "helper: " in (rt.get("text") or ""):
        item = db.inbox_item_by_text(uid, (rt.get("text") or "").split("helper: ", 1)[1])
        answered = item["id"] if item else None
    result = handle(uid=uid, channel="telegram-voice" if by_voice else "telegram", text=text, country=cc,
                    reply_to=answered,
                    district=user.get("district"))
    sender.send(chat_id, format_reply(result))
    if result.document:  # the paper as its own message, easy to forward or print
        sender.send(chat_id, f"📄 {result.document['title']}\n\n{result.document['text']}")
    if by_voice and s.voice_replies and get_speech() is not None:
        audio = get_speech().synthesize(result.reply, result.reply_language)
        if audio:
            sender.send_voice(chat_id, audio)


CASE_IN_ALERT = re.compile(r"case #(\d+)", re.IGNORECASE)
HER_WORDS = re.compile(r"(?:She said: |she says:\n)(.+?)(?:\n\n|$)", re.DOTALL)


def _quote_from(alert_text: str) -> str | None:
    m = HER_WORDS.search(alert_text or "")
    if not m:
        return None
    q = " ".join(m.group(1).split())
    return q[:200] + ("…" if len(q) > 200 else "")


def _handle_helper(text: str, helper, reply_to: dict | None = None, message_id: int | None = None) -> None:
    """Helpers answer a woman without ever seeing her identity, either by swiping to reply on an alert or a
    'she says' message from the bot, or with /reply <case> <message>. /close <case> ends a case."""
    sender = get_sender()  # answers go back to the helpers' chat the command came from
    m = re.match(r"^/close(?:@\w+)?\s+(?:case\s+|#)?(\d+)", text, flags=re.IGNORECASE)
    if m:
        ok = db.close_case(int(m.group(1)))
        sender.send(helper, f"Case {m.group(1)} closed: her next messages go to HerEvo again." if ok
                    else f"Case {m.group(1)} not found.")
        return
    if text.startswith("/reply"):
        # Accept "/reply 3 ...", "/reply #3 ..." and "/reply case 3 ..."
        parts = re.sub(r"^(/reply(?:@\w+)?)\s+(?:case\s+|#)?", r"\1 ", text, flags=re.IGNORECASE).split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit():
            sender.send(helper, "Use: /reply <case number> <your message>")
            return
        _deliver(int(parts[1]), parts[2], None, sender, helper, message_id)
        return
    if reply_to and (reply_to.get("from") or {}).get("is_bot"):
        # Swipe-to-reply on the bot's alert or on one of her relayed messages.
        found = CASE_IN_ALERT.search(reply_to.get("text") or "")
        if found and not text.startswith("/"):
            _deliver(int(found.group(1)), text, _quote_from(reply_to.get("text") or ""), sender, helper, message_id)
            return
    sender.send(helper, "Swipe to reply on her message (or use /reply <case number> <your message>). "
                        "End a case with /close <case number>.")


def _deliver(case_id: int, message: str, quote: str | None, sender, helper, message_id: int | None = None) -> None:
    case = db.escalation(case_id)
    if not case:
        sender.send(helper, f"Case {case_id} not found.")
        return
    if not str(case["channel"]).startswith("telegram"):
        # She wrote on the website: her open page picks the reply up within seconds, shown under her message.
        db.add_inbox(case["user_id"], case["id"], message, quote, tg_message_id=message_id)
        db.mark_contacted(case["id"])
        sender.send(helper, f"Sent to case {case['id']} (website chat: she sees it while her page is open).")
        return
    user = db.get_user(case["user_id"])
    if not user or not user.get("address"):
        sender.send(helper, f"Case {case_id}: she deleted her data or cannot be reached.")
        return
    head = f"↩ “{quote}”\n" if quote else ""
    sender.send(user["address"], f"{head}HerEvo সহায়তাকর্মী / helper: {message}")
    db.add_inbox(case["user_id"], case["id"], message, quote, delivered=True,  # so HerEvo knows what the helper asked
                 tg_message_id=message_id)
    db.mark_contacted(case["id"])
    sender.send(helper, f"Sent to case {case['id']}.")
