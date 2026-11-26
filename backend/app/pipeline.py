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
from .documents import INTRO
from .documents import render as render_document
from .inheritance import Family, compute, describe
from .packs import entry_gap, load_pack, local_entry
from .retrieval import retrieve

log = logging.getLogger("herevo.pipeline")

_BN_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


# Common short words and word shapes of each Latin-script language HerEvo serves. A message is scored
# against each list; the best score wins, and English is the default when nothing stands out.
_LATIN_WORDS = {
    "sw": set("""ni na kwa ya wa za la kwenye katika mimi wewe yeye sisi wao yangu wangu wako yako
        sana hapana ndiyo ndio leo jana kesho wiki wikendi siku mwezi miezi mwaka pesa kazi shilingi
        mwajiri mshahara mtoto watoto mume bibi nyumba shamba ardhi usalama akiba msaada tafadhali
        habari jambo hujambo mambo sijui nawezaje vipi gani nini lini wapi kwa nini bado tu pia
        lakini kama hii hiyo huyu yule""".split()),
    "es": set("""el la los las de del que y en mi mis por para con una un unos es son no me te se
        tengo tiene hoy ayer mañana dinero trabajo hijos hijo hija esposo expareja pesos mercado
        cómo como qué que cuándo dónde por favor hola buenos buenas gracias puedo quiero necesito
        ayuda préstamo puesto vendí venta pensión pero también muy""".split()),
    "en": set("""the a an and is are was were i my me you your he she it we they to of for in on at
        with how what when where why do does did can could should would will not no yes sold sell
        money help please hello hi today yesterday this that have has had am""".split()),
}
_LATIN_SHAPES = {
    "sw": re.compile(r"\b(nili|nime|nita|tuli|tume|wali|ali|ana|hana|haja|sija|nina|una|ku)[a-z]{2,}", re.I),
    "es": re.compile(r"[ñ¿¡áéíóú]|\b\w+(ción|mente)\b", re.I),
    "en": re.compile(r"\b\w+(ing|ed)\b", re.I),
}


def detect_language(text: str) -> str:
    if re.search(r"[ঀ-৿]", text):
        return "bn"
    words = re.findall(r"[a-záéíóúñü]+", text.lower())
    scores = {lang: sum(w in vocab for w in words) + 1.5 * len(_LATIN_SHAPES[lang].findall(text))
              for lang, vocab in _LATIN_WORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > scores["en"] else "en"


_LANG_ASK = [
    ("bn", re.compile(r"\b(bangla|bengali|bangla\s*te|banglay)\b|বাংলায়|বাংলাতে|বাংলা ভাষায়", re.I)),
    ("en", re.compile(r"\b(english)\b|ইংরেজিতে|ইংলিশে|kwa kiingereza|en ingl[eé]s", re.I)),
    ("sw", re.compile(r"\b(swahili|kiswahili)\b", re.I)),
    ("es", re.compile(r"\b(spanish|espa[nñ]ol)\b", re.I)),
]
_LANG_VERB = re.compile(r"\b(in|into|use|speak|talk|answer|reply|respond|write|please|kwa|en)\b|য়$|তে\b|ভাষায়|বলুন|বলো|লিখুন", re.I)


def requested_language(text: str) -> str | None:
    """'Reply me in Bangla', 'বাংলায় বলুন', 'en español por favor' -> the language she asked for."""
    if len(text) > 80 or not _LANG_VERB.search(text):
        return None
    hits = [code for code, rx in _LANG_ASK if rx.search(text)]
    return hits[0] if len(hits) == 1 else None


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
    reply_language: str = "en"
    helper_alerted: bool | None = None  # safety cases: did a real helper channel receive the alert?
    steps: list[str] = field(default_factory=list)       # concrete next steps, in her language
    steps_en: list[str] = field(default_factory=list)
    calculation: str | None = None                       # inheritance shares worked out by HerEvo, not the AI
    document: dict | None = None                         # a paper HerEvo wrote for her (see documents.py)  # language of the reply (hers, or the one she asked for)
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
    lang_in = detect_language(text)
    asked = requested_language(text)
    if asked:
        db.set_preferred_language(uid, channel, cc, asked)
    lang = asked or db.preferred_language(uid) or lang_in  # once she asks for a language, keep using it

    case = db.active_case(uid)  # an open safety case: some of her messages belong with her trained helper

    # 0. She asks for a person: a helper, not the AI.
    if wants_a_person(text):
        relayed = _relay(case, uid, pack, lang, text) if case else None
        result = relayed or _escalate(uid, channel, cc, district, lang, pack, reason="asked_for_person",
                                      message=text, answered_by="rule", kind="person")
        return _finish(result, uid, channel, lang, text, t0, model=None)

    # 1. Safety first, before any AI.
    screen = safety.screen(text)
    if screen.escalate:
        relayed = _relay(case, uid, pack, lang, text, kind=screen.kind) if case else None  # same case, not a new one
        if relayed:
            return _finish(relayed, uid, channel, lang, text, t0, model=None)
        result = _escalate(uid, channel, cc, district, lang, pack, reason="rule:" + ",".join(screen.matched),
                           message=text, answered_by="safety_rule", kind=screen.kind)
        return _finish(result, uid, channel, lang, text, t0, model=None)

    # "What can you do?" has one right answer: HerEvo's own, not the AI's guess.
    if asks_what_herevo_does(text) and "capabilities" in pack["messages"]:
        result = TurnResult(reply=_msg(pack, "capabilities", lang), reply_en=_msg(pack, "capabilities", "en"),
                            intent="ADVICE", confidence=None, safety="OK", answered_by="rule")
        return _finish(result, uid, channel, lang, text, t0, model=None)

    # 2. Retrieve from her country pack.
    history = db.recent_turns(uid)
    # A short follow-up ("2 sons and a daughter") has no topic words of its own: search with her last message too.
    query = text if not history or len(text.split()) >= 8 else f"{history[-1]['message']} {text}"
    passages = retrieve(cc, query)
    for p in passages:  # the checked translation in her language, when the pack has one
        if lang != "en":
            p["local"] = local_entry(cc, p["id"], lang)
    by_id = {p["id"]: p for p in passages}

    # 3. One Claude call.
    llm = get_llm()
    parsed = None
    failure: Exception | None = None
    if llm is not None:
        try:
            parsed = parse(llm.respond(lang, passages, text, history=history, note=_case_note(case, uid)), set(by_id))
        except (ValidationError, ValueError, KeyError, TypeError) as exc:
            failure = exc
            log.warning("invalid model output, using fallback: %s", exc)
        except Exception as exc:  # network / API errors must never break the call
            failure = exc
            log.error("model call failed, using fallback: %s", exc)

    if case and (parsed.for_helper if parsed is not None else _answering_helper(uid)):
        # She is answering her helper or telling more about her situation: it goes to the person, not the AI.
        relayed = _relay(case, uid, pack, lang, text)
        if relayed:
            return _finish(relayed, uid, channel, lang, text, t0, model=None)
    if parsed is not None and parsed.for_helper and not case:
        result = _escalate(uid, channel, cc, district, lang, pack, reason="asked_for_person", message=text,
                           answered_by=get_settings().llm_provider, kind="person")
        return _finish(result, uid, channel, lang, text, t0, model=llm.model)
    if parsed is not None and parsed.safety == "ESCALATE":
        relayed = _relay(case, uid, pack, lang, text) if case else None
        if relayed:
            return _finish(relayed, uid, channel, lang, text, t0, model=llm.model)
        result = _escalate(uid, channel, cc, district, lang, pack, reason="model", message=text,
                           answered_by=get_settings().llm_provider)
        return _finish(result, uid, channel, lang, text, t0, model=llm.model)

    if parsed is not None:
        cites = [Citation(c, by_id[c]["title"], by_id[c]["source_name"], by_id[c]["review_status"])
                 for c in parsed.cited_ids]
        result = TurnResult(reply=parsed.answer, reply_en=parsed.answer_en, intent=parsed.intent,
                            confidence=parsed.confidence, safety="OK", answered_by=get_settings().llm_provider,
                            citations=cites,
                            sale=parsed.sale.model_dump() if parsed.sale else None,
                            steps=parsed.steps, steps_en=parsed.steps_en)
        if not parsed.covered:
            result.citations = []
        _solve(result, parsed, pack, cc, lang, uid)
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
        elif (asked or is_greeting(text)) and "hello" in pack["messages"]:
            reply, reply_en = _msg(pack, "hello", lang), _msg(pack, "hello", "en")
            intent = "ADVICE"
        elif passages and passages[0].get("keyword_match") and "closest" in pack["messages"]:
            # Same as the phone's offline mode: the best-matching pre-checked pack passage, with its source.
            top = passages[0]
            passage = f"{top['title']}\n{top['body']}"
            local = top.get("local")
            if local or lang == "en":
                reply = f"{_msg(pack, 'closest', lang)}\n\n" + (f"{local['title']}\n{local['text']}" if local else passage)
            else:  # no translation yet: say so, then the English passage
                reply = f"{_msg(pack, 'closest_en_only', lang)}\n\n{passage}"
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
    if result.intent == "RIGHTS" and (is_greeting(text) or asked or not (result.citations or fallback_intent(text) == "RIGHTS")):
        result.intent = "ADVICE"
    if result.intent == "EARN" and result.sale:
        db.add_sale(uid, result.sale)
    if result.intent == "RIGHTS":
        cited = [c.id for c in result.citations] or ([passages[0]["id"]] if passages and result.answered_by == "fallback" else [])
        gap = next((entry_gap(cc, i) for i in cited if entry_gap(cc, i)), "other")
        db.add_case(cc, district, "RIGHTS", gap)
        result.gap = gap
    return _finish(result, uid, channel, lang, text, t0, model=getattr(llm, "last_model", llm.model) if parsed is not None else None)


_PERSON = re.compile(
    r"\b(talk|speak|chat)\s+(to|with)\s+(a\s+|an\s+|the\s+|my\s+)?(person|human|helper|someone|somebody|counsel+or|real person)"
    r"|\b(need|want)\s+(a\s+|an\s+)?(human|helper|counsel+or|real person)\b|\bconnect me\b"
    r"|মানুষের সাথে কথা|মানুষের সঙ্গে কথা|কারো সাথে কথা বলতে চাই|কারও সাথে কথা বলতে চাই|সহায়তাকর্মীর সাথে|হেল্পারের সাথে"
    r"|nataka kuongea na mtu|nataka kuzungumza na mtu|mhudumu"
    r"|hablar con (una persona|alguien|un humano)|necesito (una persona|hablar con alguien)", re.IGNORECASE)


_CAPABILITIES = re.compile(
    r"\bwhat (else )?(can|could|do) you (do|help|offer)|\bhow (can|could|will) you help\b"
    r"|\bwhat (kind of |sort of )?(information|info|help|services?|topics?) (do |can )?you\b"
    r"|\bwhat do you know\b|\bwho are you\b|\bwhat is herevo\b|\bwhat are you\b"
    r"|কী কী (বিষয়ে )?সাহায্য|কি কি (বিষয়ে )?সাহায্য|কীভাবে সাহায্য করতে পার|কিভাবে সাহায্য করতে পার|কী করতে পারেন|কি করতে পারেন"
    r"|আপনি কে|তুমি কে|কী কী তথ্য|কি কি তথ্য"
    r"|unaweza kunisaidia (nini|vipi)|unaweza kufanya nini|wewe ni nani"
    r"|qu[ée] puedes hacer|c[óo]mo (me )?puedes ayudar|en qu[ée] me puede(s)? ayudar|qui[ée]n eres", re.IGNORECASE)


def asks_what_herevo_does(text: str) -> bool:
    return bool(_CAPABILITIES.search(text)) and len(text.split()) <= 12


def wants_a_person(text: str) -> bool:
    return bool(_PERSON.search(text))


def _case_note(case, uid: str) -> str | None:
    if not case:
        return None
    last = db.last_helper_message(uid, minutes=60)
    said = f' The helper\'s last message to her: "{last["text"][:300]}"' if last else " The helper has not written yet."
    return (f"Note: she has an open safety case (#{case['id']}) with a trained human helper.{said} "
            "Set for_helper=true only if this message answers the helper, tells more about her safety, or asks "
            "for a person. Otherwise help her yourself as usual.")


def _answering_helper(uid: str) -> bool:
    """Without the AI: if her helper wrote in the last 30 minutes, her message is most likely the answer."""
    return db.last_helper_message(uid, minutes=30) is not None


def _relay(case, uid: str, pack: dict, lang: str, text: str, kind: str = "danger") -> TurnResult | None:
    """Pass her message to the helper on her open case. None if no helper channel took it."""
    if not case or "sent_to_helper" not in pack["messages"]:
        return None
    prefix = "[TEST MODE · made-up message] " if get_settings().is_test else ""
    if not get_notifier().notify_helper(f"{prefix}💬 Case #{case['id']}{KIND_LABEL.get(kind, '')} · she says:\n{text[:1000]}\n\n"
                                        f"👉 Swipe to reply (or /reply {case['id']} <message>). Close: /close {case['id']}"):
        return None
    key = KIND_MESSAGE.get(kind) if kind in ("self_harm", "harm_intent") else "sent_to_helper"
    key = key if key in pack["messages"] else "sent_to_helper"
    return TurnResult(reply=_msg(pack, key, lang), reply_en=_msg(pack, key, "en"),
                      intent="SAFETY", confidence=None, safety="ESCALATE", answered_by="helper_relay",
                      escalation_id=case["id"], helper_alerted=True)


REFER_TO_LAWYER = {
    "bn": "এই পরিবারের ভাগ হিসাব করতে আইনজীবী দরকার। বিনামূল্যে আইনি সহায়তা: ১৬৪৩০ (টোল-ফ্রি)।",
    "en": "Working out the shares for this family needs a lawyer. Free legal aid: 16430 (toll-free).",
}


def _solve(result: TurnResult, parsed, pack: dict, cc: str, lang: str, uid: str) -> None:
    """The parts HerEvo does itself, never the AI: inheritance arithmetic and the papers she needs."""
    inh = parsed.inheritance
    if inh and inh.complete and cc == "BD":  # the calculator follows Muslim law as applied in Bangladesh
        fam = Family(deceased=inh.deceased if inh.deceased != "other" else "other", wives=inh.wives,
                     husband=inh.husband, sons=inh.sons, daughters=inh.daughters, father=inh.father_alive,
                     mother=inh.mother_alive, siblings=inh.siblings, land=inh.land_amount, unit=inh.land_unit)
        res, why = compute(fam) if inh.deceased != "other" else (None, "other relative: ask legal aid")
        if res:
            result.calculation = describe(res, lang, inh.land_unit)
            result.reply += "\n\n" + result.calculation
            result.reply_en += "\n\n" + describe(res, "en", inh.land_unit)
            if result.intent != "RIGHTS":
                result.intent = "RIGHTS"
        elif not why.startswith("need"):
            result.reply += "\n\n" + REFER_TO_LAWYER.get(lang, REFER_TO_LAWYER["en"])
            result.reply_en += "\n\n" + REFER_TO_LAWYER["en"]
    if parsed.document:
        fields = {f.name.strip().lower(): f.value for f in parsed.document.fields}
        doc = render_document(pack, parsed.document.type, fields, lang, uid)
        if doc:
            result.document = doc
            result.reply += "\n\n" + INTRO.get(doc["language"], INTRO["en"])
            result.reply_en += "\n\n" + INTRO["en"]


KIND_LABEL = {"danger": "", "self_harm": " · ⚠️ SELF-HARM RISK", "harm_intent": " · ⚠️ talks about hurting someone",
              "person": " · asked to talk to a person"}
KIND_MESSAGE = {"danger": "escalate", "self_harm": "escalate_self_harm", "harm_intent": "escalate_harm_intent",
                "person": "escalate"}


def _escalate(uid, channel, cc, district, lang, pack, *, reason, message, answered_by, kind="danger") -> TurnResult:
    eid = db.add_escalation(uid, channel, reason)
    if kind == "danger":  # the evidence map counts threats to her, not other kinds of cases
        db.add_case(cc, district, "SAFETY", "safety_threats")
    prefix = "[TEST MODE · made-up message] " if get_settings().is_test else ""
    alert = (f"{prefix}🔴 HerEvo safety case #{eid}{KIND_LABEL.get(kind, '')}\nCountry: {cc}  District: {district or 'unknown'}  Channel: {channel}\n"
             f"Language: {lang}\nShe said: {message[:500]}\n\n" +
             "👉 Swipe to reply to this message (or /reply " + f"{eid} <message>). End the case: /close {eid}" +
             ("" if channel.startswith("telegram") else "\nShe wrote on the website; she sees replies while her page is open."))
    sent = get_notifier().notify_helper(alert)
    if not sent:
        log.error("escalation %s could not alert a helper", eid)
    key = KIND_MESSAGE.get(kind, "escalate")
    key = key if key in pack["messages"] else "escalate"
    return TurnResult(reply=_msg(pack, key, lang), reply_en=_msg(pack, key, "en"), intent="SAFETY",
                      confidence=None, safety="ESCALATE", answered_by=answered_by, escalation_id=eid,
                      gap="safety_threats" if kind == "danger" else None, helper_alerted=bool(sent))


def _finish(result: TurnResult, uid, channel, lang, text, t0, model) -> TurnResult:
    result.latency_ms = int((time.monotonic() - t0) * 1000)
    result.language = detect_language(text)
    result.reply_language = lang
    db.log_turn({"user_id": uid, "channel": channel, "language": lang, "intent": result.intent,
                 "confidence": result.confidence, "safety": result.safety,
                 "cited_ids": [c.id for c in result.citations], "answered_by": result.answered_by,
                 "model": model, "latency_ms": result.latency_ms, "message": text, "reply": result.reply})
    return result
