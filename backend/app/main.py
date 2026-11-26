"""HerEvo API.

  GET  /health                       liveness + which features are configured
  POST /v1/ask                       one turn from the web app (or any client)
  GET  /v1/ledger/{session_id}       her income record (web)
  GET  /v1/evidence                  anonymized counts for the evidence map
  GET  /v1/contacts/{country}        free help contacts
  POST /telegram/webhook/{secret}    Telegram bot updates
  GET  /                             the HerEvo web app (web/index.html), talking to this API
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_schema()
    yield
    db.close()


app = FastAPI(title="HerEvo API", version=__version__, lifespan=lifespan)


@app.middleware("http")
async def fresh_app_files(request, call_next):
    """The page and its offline helper must never be kept stale by the browser, or a phone keeps running
    an old version after an update. (The offline helper still keeps its own copy for use without signal.)"""
    response = await call_next(request)
    path = request.url.path
    if (path in ("/", "/index.html", "/sw.js") or path.startswith("/v1/") or path.startswith("/health")) \
            and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response


app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

# Simple per-user rate limit: 20 messages per 10 minutes (protects cost and stops abuse).
_WINDOW, _LIMIT = 600, 20
_hits: dict[str, deque] = defaultdict(deque)


def rate_limited(key: str) -> bool:
    now = time.monotonic()
    q = _hits[key]
    while q and now - q[0] > _WINDOW:
        q.popleft()
    if len(q) >= _LIMIT:
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


@app.post("/v1/ask")
def ask(body: AskIn):
    country = body.country.upper()
    if country not in available_countries():
        raise HTTPException(404, f"No country pack for {country}")
    uid = db.user_id_for("web", body.session_id)
    if rate_limited(uid):
        raise HTTPException(429, "Too many messages. Please wait a few minutes.")
    db.upsert_user(uid, "web", country, "bn")
    return handle(uid=uid, channel="web", text=body.text, country=country, district=body.district).to_dict()


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
                     "source": e["source"]} for e in pack["entries"]],
        "safety_terms": sorted({t for terms in safety.TERMS.values() for t in terms}),
    }
    return JSONResponse(body, headers={"Cache-Control": "public, max-age=3600"})


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
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
