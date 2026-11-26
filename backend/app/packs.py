"""Country packs: reviewed knowledge in YAML (data/packs/<cc>.yaml), loaded into Postgres + pgvector.

Contextual retrieval: before embedding, every chunk gets a short context line (country, module,
title) so a chunk like "she gets one-eighth" still matches "widow inheritance Bangladesh"."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from . import db
from .config import get_settings
from .embeddings import get_embedder

MODULE_NAMES = {"rights": "Legal rights", "farm": "Farming", "money": "Money and savings",
                "safety": "Safety", "health": "Health"}


def packs_dir() -> Path:
    p = Path(get_settings().packs_dir)
    if not p.is_absolute():
        p = (Path(__file__).resolve().parent.parent / p).resolve()
    return p


@lru_cache
def load_pack(country: str) -> dict:
    path = packs_dir() / f"{country.lower()}.yaml"
    if not path.exists():
        raise KeyError(f"No country pack for {country}")
    pack = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_pack(pack)
    return pack


def available_countries() -> list[str]:
    return sorted(p.stem.upper() for p in packs_dir().glob("*.yaml"))


REQUIRED_ENTRY_FIELDS = ("id", "module", "topic", "title", "text", "source")


def validate_pack(pack: dict) -> None:
    for key in ("country", "name", "version", "messages", "entries", "contacts"):
        if key not in pack:
            raise ValueError(f"pack missing '{key}'")
    for msg in ("escalate", "not_covered", "welcome"):
        if msg not in pack["messages"]:
            raise ValueError(f"pack messages missing '{msg}'")
    seen = set()
    for e in pack["entries"]:
        for f in REQUIRED_ENTRY_FIELDS:
            if not e.get(f):
                raise ValueError(f"entry {e.get('id')} missing '{f}'")
        for lang, loc in (e.get("local") or {}).items():
            if not (isinstance(loc, dict) and loc.get("title") and loc.get("text")):
                raise ValueError(f"entry {e.get('id')} local '{lang}' needs title and text")
        if e["module"] not in MODULE_NAMES:
            raise ValueError(f"entry {e['id']} has unknown module {e['module']}")
        if e["id"] in seen:
            raise ValueError(f"duplicate entry id {e['id']}")
        seen.add(e["id"])


def context_line(pack: dict, entry: dict) -> str:
    return f"{pack['name']} · {MODULE_NAMES[entry['module']]} · {entry['topic'].replace('_', ' ')}: {entry['title']}"


def ingest(country: str) -> dict:
    """Load one pack into the database. Idempotent; removes chunks that were deleted from the YAML."""
    load_pack.cache_clear()
    pack = load_pack(country)
    cc = pack["country"]
    entries = pack["entries"]
    texts = [f"{context_line(pack, e)}\n{e['text']}\n{e.get('keywords', '')}" for e in entries]
    vectors = get_embedder().embed(texts, input_type="document")
    ids = []
    for e, vec in zip(entries, vectors):
        cid = f"{e['id']}#0"
        ids.append(cid)
        db.upsert_chunk({
            "id": cid, "country": cc, "module": e["module"], "topic": e["topic"], "title": e["title"],
            "context": context_line(pack, e), "body": " ".join(e["text"].split()),
            "keywords": search_words(e), "source_name": e["source"], "source_url": e.get("source_url"),
            "review_status": "reviewed" if pack.get("reviewed_by") else "draft",
            "pack_version": str(pack["version"]), "embedding": vec,
        })
    removed = db.delete_stale_chunks(cc, ids)
    for c in pack["contacts"]:
        db.upsert_contact({"id": c["id"], "country": cc, "kind": c["kind"], "name": c["name"],
                           "phone": c.get("phone"), "area": c.get("area"), "free": c.get("free", True),
                           "source": c["source"]})
    return {"country": cc, "chunks": len(ids), "removed": removed, "contacts": len(pack["contacts"])}


def search_words(entry: dict) -> str:
    """Local-language keywords plus the entry in her language, so a question in Bangla, Swahili or Spanish
    finds the entry by its own words, not only by the keywords someone thought of."""
    local = " ".join(f"{v['title']} {' '.join(v['text'].split())}" for v in (entry.get("local") or {}).values())
    return f"{entry.get('keywords', '')} {local}".strip()


def local_entry(country: str, chunk_id: str, lang: str) -> dict | None:
    """The entry in her language ({title, text}), if the pack has it."""
    eid = chunk_id.split("#")[0]
    for e in load_pack(country)["entries"]:
        if e["id"] == eid:
            loc = (e.get("local") or {}).get(lang)
            return {"title": loc["title"], "text": " ".join(loc["text"].split())} if loc else None
    return None


def entry_gap(country: str, chunk_id: str) -> str | None:
    eid = chunk_id.split("#")[0]
    for e in load_pack(country)["entries"]:
        if e["id"] == eid:
            return e.get("gap")
    return None


if __name__ == "__main__":  # python -m app.packs  -> ingest every pack
    db.init_schema()
    for cc in available_countries():
        print(ingest(cc))
