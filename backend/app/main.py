"""HerEvo API.

  GET  /health                       liveness + which features are configured
  POST /v1/ask                       one turn from the web app (or any client)
  GET  /v1/ledger/{session_id}       her income record (web)
  GET  /v1/evidence                  anonymized counts for the evidence map
  GET  /v1/contacts/{country}        free help contacts
  POST /telegram/webhook/{secret}    Telegram bot updates
  GET  /v1/scenarios                 rehearsal room scenarios
  POST /v1/lens                      Paper Lens: read a photographed paper aloud and flag what not to sign
  POST /v1/rehearse                  one turn of a rehearsal (the other side's line + coaching)
  POST /v1/counter                   whisper at the real desk: what is true, what to say
  POST /v1/statement                 Witness diary notes -> dated statement
  POST /v1/share                     exact inheritance shares (for the land picture)
  POST /v1/plan                      "just tell me": her words -> a path through the tools
  POST /v1/minutes                   listen mode: the record of a meeting (who promised what)
  POST /v1/radar, GET /v1/radar/{cc} anonymous warnings (bribes, pressure to sign) and their counts
  GET  / and /app                    HerEvo app: the five tools (web/app.html)
  GET  /chat                         the conversation with HerEvo (web/index.html), talking to this API
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pathlib import Path

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, db
from .channels import telegram
from .config import get_settings
from .packs import available_countries
from .pipeline import handle

log = logging.getLogger("herevo.api")

WEB_DIR = Path(__file__).resolve().parents[2] / "web"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


PURGE_EVERY_SECONDS = 6 * 3600


def purge_once() -> int:
    """Privacy promise: the text of her messages is deleted after 30 days. (Anonymized counts stay.)"""
    try:
        n = db.purge_old_messages(30)
        if n:
            log.info("privacy purge: deleted the text of %s messages older than 30 days", n)
        return n
    except Exception as exc:  # never let the purge take the service down; it runs again later
        log.error("privacy purge failed: %s", exc)
        return -1


async def _purge_forever():
    import asyncio
    while True:
        await asyncio.to_thread(purge_once)
        await asyncio.sleep(PURGE_EVERY_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    db.init_schema()
    purger = asyncio.create_task(_purge_forever())  # at start-up, then every 6 hours
    yield
    purger.cancel()
    db.close()


app = FastAPI(title="HerEvo API", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def fresh_app_files(request, call_next):
    """The page and its offline helper must never be kept stale by the browser, or a phone keeps running
    an old version after an update. (The offline helper still keeps its own copy for use without signal.)"""
    response = await call_next(request)
    path = request.url.path
    if (path in ("/", "/index.html", "/sw.js", "/app", "/app.html", "/chat") or path.startswith("/v1/") or path.startswith("/health")) \
            and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response


app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

# Simple per-user rate limit: 20 messages per 10 minutes (protects cost and stops abuse).
_WINDOW, _LIMIT = 600, 20
_hits: dict[str, deque] = defaultdict(deque)


def rate_limited(key: str, limit: int = _LIMIT) -> bool:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > _WINDOW:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


@app.get("/health")
def health():
    s = get_settings()
    return {"ok": True, "version": __version__, "countries": available_countries(), "data_mode": s.data_mode,
            "llm": s.llm_provider if (s.anthropic_api_key or s.gemini_api_key) else None,
            "model": (s.gemini_model if s.llm_provider == "gemini" else s.claude_model),
            "claude": bool(s.anthropic_api_key), "embeddings": s.embeddings_provider,
            "speech": s.speech_provider if (s.google_api_key or s.gemini_api_key) else None,
            "telegram": bool(s.telegram_bot_token), "helper_alerts": bool(s.helper_chat_id)}


_ai_check: dict = {"at": 0.0, "result": None}


@app.get("/health/ai")
def health_ai():
    """Tests the AI with one short, made-up question and shows exactly what the provider said.
    Never shows the key. Cached for 60 seconds so opening it repeatedly does not use up the free quota."""
    if _ai_check["result"] and time.monotonic() - _ai_check["at"] < 60:
        return {**_ai_check["result"], "cached": True}
    from .llm import get_llm
    s = get_settings()
    key = (s.gemini_api_key if s.llm_provider == "gemini" else s.anthropic_api_key) or ""
    out: dict = {"provider": s.llm_provider, "data_mode": s.data_mode,
                 "key": {"set": bool(key.strip()), "length": len(key.strip()),
                         "has_spaces_or_quotes": key != key.strip() or any(c in key for c in " \"'\n"),
                         "looks_like_google_key": key.strip().startswith("AIza")} if s.llm_provider == "gemini"
                 else {"set": bool(key.strip()), "length": len(key.strip())}}
    llm = get_llm()
    if llm is None:
        out.update(ok=False, verdict="No AI key is set on the server. Add it in Render → Environment.")
    else:
        t0 = time.monotonic()
        try:
            raw = llm.respond("en", [{"id": "test#0", "title": "Test", "context": "", "body": "Rice needs water.",
                                      "source_name": "test", "review_status": "draft"}], "How do I grow rice?")
            out.update(ok=True, model_used=getattr(llm, "last_model", llm.model),
                       verdict="The AI works. HerEvo answers with it.", fields=sorted(raw))
        except Exception as exc:  # report, never crash
            msg = str(exc)
            if s.llm_provider == "gemini":
                msg = msg.replace(s.gemini_api_key.strip(), "[key]") if s.gemini_api_key.strip() else msg
            out.update(ok=False, error=f"{type(exc).__name__}: {msg[:600]}", verdict=_ai_verdict(msg))
        out["seconds"] = round(time.monotonic() - t0, 1)
    _ai_check.update(at=time.monotonic(), result=out)
    return out


def _ai_verdict(msg: str) -> str:
    if any(x in msg for x in ("API key not valid", "API_KEY_INVALID", "401", "UNAUTHENTICATED")):
        return "Google rejected the key. Copy the key again from AI Studio into Render (no spaces), or make a new one."
    if "PERMISSION_DENIED" in msg or "403" in msg:
        return "The key is not allowed to use Gemini. In AI Studio, create a new key in a new project."
    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
        return "The free quota for this key is used up or zero. In AI Studio, create a new key in a new project."
    if "404" in msg or "NOT_FOUND" in msg:
        return "The model names were not found for this key."
    if "503" in msg or "UNAVAILABLE" in msg or "500" in msg:
        return "Google's servers are busy. Try again in a few minutes."
    if "network" in msg:
        return "The server could not reach Google."
    return "Unexpected error; send this page to the developer."


class AskIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)
    text: str = Field(min_length=1, max_length=2000)
    country: str = "BD"
    district: str | None = None
    reply_to: int | None = None  # a helper message (inbox id) she is answering


@app.post("/v1/ask")
def ask(body: AskIn):
    country = body.country.upper()
    if country not in available_countries():
        raise HTTPException(404, f"No country pack for {country}")
    uid = db.user_id_for("web", body.session_id)
    if rate_limited(uid):
        raise HTTPException(429, "Too many messages. Please wait a few minutes.")
    db.upsert_user(uid, "web", country, "bn")
    return handle(uid=uid, channel="web", text=body.text, country=country, district=body.district,
                  reply_to=body.reply_to).to_dict()


class SpeakIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    lang: str = Field(default="bn", max_length=5)
    session_id: str = Field(default="anon-000", min_length=8, max_length=100)


_tts_cache: dict[tuple[str, str], bytes] = {}


@app.post("/v1/tts")
def tts(body: SpeakIn):
    """Read an answer aloud (WAV) for browsers that have no voice for her language, such as Bangla on Windows."""
    from fastapi.responses import Response

    from .speech import get_speech
    key = (body.lang, body.text)
    if key not in _tts_cache:
        if rate_limited("tts:" + body.session_id):
            raise HTTPException(429, "Too many voice requests. Please wait a few minutes.")
        sp = get_speech()
        speak = getattr(sp, "speak_wav", None)
        wav = speak(body.text, body.lang) if speak else None
        if not wav:
            raise HTTPException(503, "Voice is not available right now.")
        if len(_tts_cache) > 200:
            _tts_cache.clear()
        _tts_cache[key] = wav
    return Response(_tts_cache[key], media_type="audio/wav", headers={"Cache-Control": "no-store"})


@app.get("/v1/inbox/{session_id}")
def inbox(session_id: str):
    """Messages from a trained helper for a woman on the website (polled by her open page)."""
    rows = db.take_inbox(db.user_id_for("web", session_id))
    return {"messages": [{"id": r["id"], "case": r["escalation_id"], "text": r["text"], "quote": r["quote"],
                          "at": r["created_at"].isoformat()} for r in rows]}


@app.get("/v1/ledger/{session_id}")
def ledger(session_id: str):
    rows = db.ledger(db.user_id_for("web", session_id))
    return {"sales": rows, "total": sum(float(r["amount"]) for r in rows)}


@app.get("/v1/evidence")
def evidence(min_count: int = 1):
    return {"cells": db.evidence(max(1, min_count))}


@app.get("/v1/contacts/{country}")
def contacts(country: str):
    return {"contacts": db.contacts(country.upper())}


@app.get("/v1/packs/{country}")
def offline_pack(country: str):
    """A country pack for offline use: reviewed entries, help contacts, messages and the safety terms,
    so the app can still answer (and still escalate) with no signal. Public, reviewed content only."""
    from fastapi.responses import JSONResponse

    from . import safety
    from .packs import load_pack

    cc = country.upper()
    if cc not in available_countries():
        raise HTTPException(404, f"No country pack for {cc}")
    pack = load_pack(cc)
    body = {
        "country": pack["country"], "name": pack["name"], "version": str(pack["version"]),
        "review_status": "reviewed" if pack.get("reviewed_by") else "draft",
        "messages": pack["messages"], "contacts": pack["contacts"],
        "entries": [{"id": e["id"], "module": e["module"], "topic": e["topic"], "gap": e.get("gap"),
                     "title": e["title"], "text": " ".join(e["text"].split()), "keywords": e.get("keywords", ""),
                     "local": {k: {"title": v["title"], "text": " ".join(v["text"].split())}
                               for k, v in (e.get("local") or {}).items()},
                     "source": e["source"]} for e in pack["entries"]],
        "safety_terms": sorted({t for lists in (safety.TERMS, safety.SELF_HARM, safety.HARM_INTENT)
                                 for terms in lists.values() for t in terms}),
    }
    return JSONResponse(body, headers={"Cache-Control": "public, max-age=3600"})


# --- HerEvo tools: Paper Lens, Rehearsal, Counter, Witness, Share -------------------------------------------
TOOL_LIMIT = 40  # AI calls per person per 10 minutes across the tools (a rehearsal is many short turns)
LANGS = ("bn", "en", "sw", "es")


def _tool_guard(session_id: str, lang: str) -> str:
    if rate_limited("tool:" + session_id, TOOL_LIMIT):
        raise HTTPException(429, "Too many requests. Please wait a few minutes.")
    return lang if lang in LANGS else "en"


@app.get("/v1/scenarios")
def scenarios(lang: str = "bn"):
    from . import tools
    return {"scenarios": tools.scenario_list(lang if lang in LANGS else "en"),
            "places": {k: (v.get(lang) or v["en"]) for k, v in tools.PLACES.items()}}


class LensIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)
    image: str = Field(min_length=100, max_length=7_000_000)  # base64, no data: prefix
    mime: str = Field(default="image/jpeg", pattern=r"^image/(jpeg|png|webp)$")
    lang: str = "bn"
    country: str = "BD"
    sample: str | None = Field(default=None, max_length=40)


@app.post("/v1/lens")
def lens(body: LensIn):
    """The photo is read and forgotten: it is never stored or logged."""
    from . import tools
    lang = _tool_guard(body.session_id, body.lang)
    image = body.image.split(",", 1)[1] if body.image.startswith("data:") else body.image
    return tools.read_paper(image, body.mime, lang, body.country.upper(), body.sample)


class Line(BaseModel):
    who: str = Field(pattern=r"^(her|them)$")
    text: str = Field(max_length=1000)


class RehearseIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)
    scenario: str = Field(max_length=40)
    history: list[Line] = Field(default_factory=list, max_length=40)
    said: str = Field(min_length=1, max_length=1000)
    lang: str = "bn"
    context: str | None = Field(default=None, max_length=400)
    finish: bool = False


@app.post("/v1/rehearse")
def rehearse_turn(body: RehearseIn):
    from . import tools
    lang = _tool_guard(body.session_id, body.lang)
    if body.scenario not in tools.SCENARIOS:
        raise HTTPException(404, "Unknown scenario")
    return tools.rehearse(body.scenario, [h.model_dump() for h in body.history], body.said, lang, body.context,
                          body.finish)


class CounterIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)
    place: str = Field(default="land_office", max_length=40)
    heard: str = Field(min_length=1, max_length=1000)
    lang: str = "bn"
    country: str = "BD"


@app.post("/v1/counter")
def counter_whisper(body: CounterIn):
    from . import tools
    lang = _tool_guard(body.session_id, body.lang)
    return tools.counter(body.place, body.heard, lang, body.country.upper())


class Entry(BaseModel):
    at: str = Field(max_length=40)
    text: str = Field(min_length=1, max_length=2000)


class StatementIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)
    entries: list[Entry] = Field(min_length=1, max_length=60)
    lang: str = "bn"
    country: str = "BD"


@app.post("/v1/statement")
def witness_statement(body: StatementIn):
    """Her notes are used for this one answer and not kept on the server."""
    from . import tools
    lang = _tool_guard(body.session_id, body.lang)
    return tools.statement([e.model_dump() for e in body.entries], lang, body.country.upper())


class ShareIn(BaseModel):
    deceased: str = Field(default="husband", pattern=r"^(husband|wife|father|mother)$")
    wives: int = Field(default=0, ge=0, le=4)
    husband: bool = False
    sons: int = Field(default=0, ge=0, le=20)
    daughters: int = Field(default=0, ge=0, le=20)
    father: bool = False
    mother: bool = False
    siblings: int | None = Field(default=None, ge=0, le=30)
    land: float | None = Field(default=None, gt=0, le=1_000_000)
    lang: str = "bn"


@app.post("/v1/share")
def share(body: ShareIn):
    """Exact shares (Hanafi, as applied in Bangladesh): the arithmetic is never left to the AI."""
    from .inheritance import DISCLAIMER, Family, compute
    f = Family(deceased=body.deceased, wives=body.wives, husband=body.husband, sons=body.sons,
               daughters=body.daughters, father=body.father, mother=body.mother, siblings=body.siblings,
               land=body.land)
    result, reason = compute(f)
    lang = "bn" if body.lang == "bn" else "en"
    if result is None:
        return {"ok": False, "reason": reason, "disclaimer": DISCLAIMER[lang]}
    return {"ok": True, "disclaimer": DISCLAIMER[lang],
            "shares": [{"heir": s.heir, "count": s.count, "num": s.each.numerator, "den": s.each.denominator,
                        "pct": round(float(s.each) * 100, 3), "land_each": s.land_each} for s in result.shares]}


class PlanIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)
    text: str = Field(min_length=2, max_length=2000)
    lang: str = "bn"
    country: str = "BD"


@app.post("/v1/plan")
def make_plan(body: PlanIn):
    """She says what is happening in her own words; HerEvo builds her path through the tools."""
    from . import tools
    lang = _tool_guard(body.session_id, body.lang)
    return tools.plan(body.text, lang, body.country.upper())


class MinutesIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)
    place: str = Field(default="union_parishad", max_length=40)
    lines: list[str] = Field(min_length=1, max_length=120)
    lang: str = "bn"
    country: str = "BD"


@app.post("/v1/minutes")
def meeting_minutes(body: MinutesIn):
    """Listen mode: the record of a meeting (promises, warnings, next steps). Not kept on the server."""
    from . import tools
    lang = _tool_guard(body.session_id, body.lang)
    return tools.minutes(body.place, [x[:600] for x in body.lines if x.strip()], lang, body.country.upper())


RADAR_KINDS = ("bribe", "pressure", "false_rule", "dowry", "land_grab", "other")


class RadarIn(BaseModel):
    session_id: str = Field(min_length=8, max_length=100)
    country: str = Field(default="BD", max_length=2)
    area: str | None = Field(default=None, max_length=40)
    place: str = Field(max_length=40)
    kind: str = Field(max_length=20)


@app.post("/v1/radar")
def radar_report(body: RadarIn):
    """An anonymous warning: no name, phone or text is taken, only the place type, an optional area and the kind."""
    from . import tools
    if rate_limited("radar:" + body.session_id, 5):
        raise HTTPException(429, "Thank you. You have already sent several warnings.")
    if body.place not in tools.PLACES or body.kind not in RADAR_KINDS:
        raise HTTPException(422, "Unknown place or kind")
    area = " ".join((body.area or "").split()).strip().lower()[:40] or None
    cc = body.country.upper()
    if cc not in available_countries():
        raise HTTPException(404, "Unknown country")
    db.add_radar(cc, area, body.place, body.kind)
    return {"ok": True, **db.radar_summary(cc)}


@app.get("/v1/radar/{country}")
def radar(country: str, days: int = 90):
    return db.radar_summary(country.upper(), max(1, min(days, 365)))


@app.get("/", include_in_schema=False)
@app.get("/app", include_in_schema=False)
def app_page():
    """HerEvo's main app: the five tools for the moments her rights are decided."""
    from fastapi.responses import FileResponse
    return FileResponse(WEB_DIR / "app.html", media_type="text/html")


@app.get("/chat", include_in_schema=False)
def chat_page():
    """The conversation with HerEvo (typed or spoken questions, helper relay, evidence map)."""
    from fastapi.responses import FileResponse
    return FileResponse(WEB_DIR / "index.html", media_type="text/html")


@app.post("/telegram/webhook/{secret}")
def telegram_webhook(secret: str, update: dict):
    # Sync handler: FastAPI runs it in a worker thread, so DB and Claude calls don't block the server.
    if secret != get_settings().telegram_webhook_secret:
        raise HTTPException(403, "bad secret")
    chat = ((update.get("message") or {}).get("chat") or {}).get("id")
    if chat is not None and rate_limited(f"tg:{chat}"):
        return {"ok": True}
    telegram.handle_update(update)
    return {"ok": True}


# The web app is served last, so API routes always take priority.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
