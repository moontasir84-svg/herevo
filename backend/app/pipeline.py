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
    if re.search(r"\b(nina|wangu|mshahara|sijui|tafadhali|habari|jambo|hujambo|mambo)\b", lowered):
        return "sw"
    if re.search(r"[ñ¿¡]|\b(mi|tengo|cómo|como|hijos|dinero|pesos|hola|buenos|buenas)\b", lowered):
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
    fallback_reason: str | None = None  # why the AI was not used (plain words, never a key)
    language: str = "en"  # detected language of her message
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


_GREETING = re.compile(r"^\W*(hi+|hello|hey|salam|assalamu ?alaikum|আসসালামু আলাইকুম|সালাম|হ্যালো|হাই|নমস্কার|"
                       r"habari|jambo|mambo|hujambo|hola|buen[oa]s (d[ií]as|tardes|noches))\W*$", re.IGNORECASE)


def is_greeting(text: str) -> bool:
    return bool(_GREETING.match(text.strip()))


def fallback_sale(text: str, currency: str) -> dict | None:
    m = _AMOUNT.search(text.translate(_BN_DIGITS))
    if not m:
        return None
    return {"product": None, "quantity": None, "unit": None,
            "amount": float(m.group(1).replace(",", "")), "currency": currency}


def why_fallback(exc: Exception | None, llm_configured: bool) -> str:
    """A short, safe reason for the fallback. Built from fixed phrases so no key or URL can leak."""
    if not llm_configured:
        return "no AI key is set on the server"
    msg = str(exc or "")
    if re.search(r"HTTP (401|403)|API key not valid|UNAUTHENTICATED|PERMISSION_DENIED|authentication", msg, re.I):
        return "the AI provider rejected the API key"
    tried = re.findall(r"([\w.-]+) (HTTP \d{3}(?: [A-Z_]+)?|network \w+)", msg)
    detail = " (" + "; ".join(f"{m}: {e}" for m, e in tried[:4]) + ")" if tried else ""
    if tried and all("429" in e for _, e in tried):
        return "the free AI limit was reached; try again in a minute" + detail
    if tried:
        return "the AI provider did not answer" + detail
    if re.search(r"HTTP 429|RESOURCE_EXHAUSTED", msg):
        return "the free AI limit was reached; try again in a minute"
    if re.search(r"HTTP 404|NOT_FOUND", msg):
        return "the AI model name was not found"
    if re.search(r"HTTP 5\d\d", msg):
        return "the AI provider did not answer"
    if isinstance(exc, (ValidationError, ValueError, KeyError, TypeError)):
        return "the AI reply failed HerEvo's checks"
    return "the AI call failed"


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
    failure: Exception | None = None
    if llm is not None:
        try:
            parsed = parse(llm.respond(lang, passages, text), set(by_id))
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            failure = exc
            log.warning("invalid model output, using fallback: %s", exc)
        except Exception as exc:  # network / API errors must never break the call
            failure = exc
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
        cites: list[Citation] = []
        sale = fallback_sale(text, pack.get("currency", "BDT")) if intent == "EARN" else None
        if sale and "sale_recorded" in pack["messages"]:
            amt = f"{sale['amount']:,.0f}"
            local_amt = amt.translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")) if lang == "bn" else amt
            reply, reply_en = (_msg(pack, "sale_recorded", lang).format(amount=local_amt),
                               _msg(pack, "sale_recorded", "en").format(amount=amt))
        elif is_greeting(text) and "hello" in pack["messages"]:
            reply, reply_en = _msg(pack, "hello", lang), _msg(pack, "hello", "en")
            intent = "ADVICE"
        elif passages and passages[0].get("keyword_match") and "closest" in pack["messages"]:
            # Same as the phone's offline mode: the best-matching pre-checked pack passage, with its source.
            top = passages[0]
            passage = f"{top['title']}: {top['body']}"
            reply = f"{_msg(pack, 'closest', lang)}\n\n{passage}"
            reply_en = f"{_msg(pack, 'closest', 'en')}\n\n{passage}"
            if intent == "RIGHTS":
                reply += "\n\n" + _msg(pack, "not_covered", lang).split(". ", 1)[-1].split("। ", 1)[-1]
                reply_en += "\n\n" + _msg(pack, "not_covered", "en").split(". ", 1)[-1]
            cites = [Citation(top["id"], top["title"], top["source_name"], top["review_status"])]
        else:
            reply, reply_en = _msg(pack, "not_covered", lang), _msg(pack, "not_covered", "en")
        result = TurnResult(reply=reply, reply_en=reply_en, intent=intent, confidence=None, safety="OK",
                            answered_by="fallback", sale=sale, citations=cites,
                            fallback_reason=why_fallback(failure, llm is not None))

    # 5. Records. The evidence map must stay clean: a greeting, or a "rights" label the AI gave without
    # citing a rights passage and without any rights word in her message, is not counted as a case.
    if result.intent == "RIGHTS" and (is_greeting(text) or not (result.citations or fallback_intent(text) == "RIGHTS")):
        result.intent = "ADVICE"
    if result.intent == "EARN" and result.sale:
        db.add_sale(uid, result.sale)
    if result.intent == "RIGHTS":
        cited = [c.id for c in result.citations] or ([passages[0]["id"]] if passages and result.answered_by == "fallback" else [])
        gap = next((entry_gap(cc, i) for i in cited if entry_gap(cc, i)), "other")
        db.add_case(cc, district, "RIGHTS", gap)
        result.gap = gap
    return _finish(result, uid, channel, lang, text, t0, model=getattr(llm, "last_model", llm.model) if parsed is not None else None)


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
    result.language = lang
    db.log_turn({"user_id": uid, "channel": channel, "language": lang, "intent": result.intent,
                 "confidence": result.confidence, "safety": result.safety,
                 "cited_ids": [c.id for c in result.citations], "answered_by": result.answered_by,
                 "model": model, "latency_ms": result.latency_ms, "message": text})
    return result
