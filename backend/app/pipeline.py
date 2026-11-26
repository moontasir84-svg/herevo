"""One turn of HerEvo, in a fixed order that never changes:

    1. safety screen (rules, no AI)  -> escalate to a human if needed
    2. hybrid retrieval from the woman's country pack
    3. one Claude call, structured output, validated
    4. fallback to rules + pre-checked text if Claude is unavailable or its output is invalid
    5. record: income (if a sale), anonymized case (rights/safety), audit turn
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field

from . import db, safety
from .alerts import get_notifier
from .config import get_settings
from .llm import ValidationError, get_llm, parse
from .packs import entry_gap, load_pack
from .retrieval import retrieve

log = logging.getLogger("herevo.pipeline")

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def detect_language(text: str) -> str:
    if re.search(r"[ঀ-৿]", text):
        return "bn"
    lowered = text.lower()
    if re.search(r"\b(nina|wangu|mshahara|sijui|tafadhali|habari)\b", lowered):
        return "sw"
    if re.search(r"[ñ¿¡]|\b(mi|tengo|cómo|como|hijos|dinero|pesos)\b", lowered):
        return "es"
    return "en"


@dataclass
class Citation:
    id: str
    title: str
    source: str
    review_status: str


@dataclass
class TurnResult:
    reply: str
    reply_en: str
    intent: str
    confidence: float | None
    safety: str
    answered_by: str
    citations: list[Citation] = field(default_factory=list)
    escalation_id: int | None = None
    sale: dict | None = None
    gap: str | None = None  # evidence-map category this turn added to, if any
    latency_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _msg(pack: dict, key: str, lang: str) -> str:
    m = pack["messages"][key]
    return m.get(lang) or m["en"]


_SALE_WORDS = re.compile(r"(বিক্রি|sold|sell|nimeuza|niliuza|vendí|vendi)", re.IGNORECASE)
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(টাকা|taka|tk|৳|shilingi|ksh|pesos|mxn)", re.IGNORECASE)


def fallback_intent(text: str) -> str:
    norm = text.translate(_BN_DIGITS)
    if _SALE_WORDS.search(norm) and _AMOUNT.search(norm):
        return "EARN"
    if re.search(r"(জমি|সম্পত্তি|উত্তরাধিকার|দেনমোহর|ভরণপোষণ|খোরপোশ|তালাক|বেতন|মজুরি|যৌতুক|land|inherit|wage|salary|"
                 r"divorce|dowry|child support|pensión|mshahara)", norm, re.IGNORECASE):
        return "RIGHTS"
    return "ADVICE"


def fallback_sale(text: str, currency: str) -> dict | None:
    m = _AMOUNT.search(text.translate(_BN_DIGITS))
    if not m:
        return None
    return {"product": None, "quantity": None, "unit": None,
            "amount": float(m.group(1).replace(",", "")), "currency": currency}


def handle(*, uid: str, channel: str, text: str, country: str | None = None, district: str | None = None) -> TurnResult:
    t0 = time.monotonic()
    text = text.strip()[:2000]
    pack = load_pack(country or "BD")
    cc = pack["country"]
    lang = detect_language(text)

    # 1. Safety first, before any AI.
    screen = safety.screen(text)
    if screen.escalate:
        result = _escalate(uid, channel, cc, district, lang, pack, reason="rule:" + ",".join(screen.matched),
                           message=text, answered_by="safety_rule")
        return _finish(result, uid, channel, lang, text, t0, model=None)

    # 2. Retrieve from her country pack.
    passages = retrieve(cc, text)
    by_id = {p["id"]: p for p in passages}

    # 3. One Claude call.
    llm = get_llm()
    parsed = None
    if llm is not None:
        try:
            parsed = parse(llm.respond(lang, passages, text), set(by_id))
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            log.warning("invalid model output, using fallback: %s", exc)
        except Exception as exc:  # network / API errors must never break the call
            log.error("model call failed, using fallback: %s", exc)

    if parsed is not None and parsed.safety == "ESCALATE":
        result = _escalate(uid, channel, cc, district, lang, pack, reason="model", message=text,
                           answered_by=get_settings().llm_provider)
        return _finish(result, uid, channel, lang, text, t0, model=llm.model)

    if parsed is not None:
        cites = [Citation(c, by_id[c]["title"], by_id[c]["source_name"], by_id[c]["review_status"])
                 for c in parsed.cited_ids]
        result = TurnResult(reply=parsed.answer, reply_en=parsed.answer_en, intent=parsed.intent,
                            confidence=parsed.confidence, safety="OK", answered_by=get_settings().llm_provider,
                            citations=cites,
                            sale=parsed.sale.model_dump() if parsed.sale else None)
        if not parsed.covered:
            result.citations = []
    else:
        # 4. Fallback: rules + pre-checked text only. Never an unvalidated answer.
        intent = fallback_intent(text)
        sale = fallback_sale(text, pack.get("currency", "BDT")) if intent == "EARN" else None
        if sale and "sale_recorded" in pack["messages"]:
            amt = f"{sale['amount']:,.0f}"
            local_amt = amt.translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")) if lang == "bn" else amt
            reply, reply_en = (_msg(pack, "sale_recorded", lang).format(amount=local_amt),
                               _msg(pack, "sale_recorded", "en").format(amount=amt))
        else:
            reply, reply_en = _msg(pack, "not_covered", lang), _msg(pack, "not_covered", "en")
        result = TurnResult(reply=reply, reply_en=reply_en, intent=intent, confidence=None, safety="OK",
                            answered_by="fallback", sale=sale)

    # 5. Records.
    if result.intent == "EARN" and result.sale:
        db.add_sale(uid, result.sale)
    if result.intent == "RIGHTS":
        cited = [c.id for c in result.citations] or ([passages[0]["id"]] if passages and result.answered_by == "fallback" else [])
        gap = next((entry_gap(cc, i) for i in cited if entry_gap(cc, i)), "other")
        db.add_case(cc, district, "RIGHTS", gap)
        result.gap = gap
    return _finish(result, uid, channel, lang, text, t0, model=llm.model if parsed is not None else None)


def _escalate(uid, channel, cc, district, lang, pack, *, reason, message, answered_by) -> TurnResult:
    eid = db.add_escalation(uid, channel, reason)
    db.add_case(cc, district, "SAFETY", "safety_threats")
    prefix = "[TEST MODE · made-up message] " if get_settings().is_test else ""
    alert = (f"{prefix}🔴 HerEvo safety case #{eid}\nCountry: {cc}  District: {district or 'unknown'}  Channel: {channel}\n"
             f"Language: {lang}\nShe said: {message[:500]}\n\nReply to her through the bot with:\n/reply {eid} <your message>")
    sent = get_notifier().notify_helper(alert)
    if not sent:
        log.error("escalation %s could not alert a helper", eid)
    return TurnResult(reply=_msg(pack, "escalate", lang), reply_en=_msg(pack, "escalate", "en"), intent="SAFETY",
                      confidence=None, safety="ESCALATE", answered_by=answered_by, escalation_id=eid,
                      gap="safety_threats")


def _finish(result: TurnResult, uid, channel, lang, text, t0, model) -> TurnResult:
    result.latency_ms = int((time.monotonic() - t0) * 1000)
    db.log_turn({"user_id": uid, "channel": channel, "language": lang, "intent": result.intent,
                 "confidence": result.confidence, "safety": result.safety,
                 "cited_ids": [c.id for c in result.citations], "answered_by": result.answered_by,
                 "model": model, "latency_ms": result.latency_ms, "message": text})
    return result
