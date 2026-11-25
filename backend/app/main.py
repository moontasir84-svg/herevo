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
    return {"ok": True, "version": __version__, "countries": available_countries(),
            "claude": bool(s.anthropic_api_key), "embeddings": s.embeddings_provider,
            "telegram": bool(s.telegram_bot_token), "helper_alerts": bool(s.helper_chat_id)}


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
