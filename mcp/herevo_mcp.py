"""HerEvo country-pack MCP server.

Lets any MCP-compatible AI assistant (NGO chatbots, Claude, partner apps) reuse HerEvo's reviewed
country packs. It reads the SAME files as the HerEvo app (data/packs/*.yaml) and uses the SAME safety
screen, so partners and HerEvo always give identical, sourced answers.

Run:  pip install -r mcp/requirements.txt  &&  python mcp/herevo_mcp.py          (stdio transport)
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import yaml
from mcp.server.mcpserver import MCPServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.safety import screen  # noqa: E402  (shared with the app)
from app.text import query_terms, tokens  # noqa: E402

PACKS = ROOT / "data" / "packs"
mcp = MCPServer("herevo-country-packs")


@lru_cache
def load(country: str) -> dict:
    path = PACKS / f"{country.strip().lower()}.yaml"
    if not path.exists():
        raise ValueError(f"No country pack for '{country}'. Available: {', '.join(list_countries())}")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _entry_out(pack: dict, e: dict) -> dict:
    return {"id": e["id"], "module": e["module"], "topic": e["topic"], "title": e["title"],
            "text": " ".join(e["text"].split()), "source": e["source"], "source_url": e.get("source_url"),
            "review_status": "reviewed" if pack.get("reviewed_by") else "draft", "pack_version": str(pack["version"])}


def _search(pack: dict, question: str, module: str | None, k: int) -> list[dict]:
    terms = query_terms(question)
    scored = []
    for e in pack["entries"]:
        if module and e["module"] != module:
            continue
        doc = tokens(f"{e['title']} {e['topic'].replace('_', ' ')} {e['text']} {e.get('keywords', '')}")
        score = sum(1 for t in terms if any(w.startswith(t) for w in doc))
        if score:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return [_entry_out(pack, e) for _, e in scored[:k]]


@mcp.tool()
def list_countries() -> list[str]:
    """List the countries that have a HerEvo country pack (ISO-2 codes)."""
    return sorted(p.stem.upper() for p in PACKS.glob("*.yaml"))


@mcp.tool()
def search_knowledge(country: str, question: str, k: int = 3) -> list[dict]:
    """Find reviewed country-pack entries for a woman's question (any module), each with its source.
    Questions can be in the local language (e.g. Bangla) or English."""
    return _search(load(country), question, None, max(1, min(k, 10)))


@mcp.tool()
def get_rights(country: str, topic: str) -> list[dict]:
    """General information on women's legal rights (e.g. 'inheritance', 'dower', 'maintenance', 'wages',
    'child marriage'), with sources. General information only, never legal advice."""
    pack = load(country)
    return _search(pack, topic, "rights", 3) or [{"note": "Not covered by this pack. Refer her to free legal aid.",
                                                   "legal_aid": find_help(country, "legal_aid")}]


@mcp.tool()
def find_help(country: str, kind: str | None = None) -> list[dict]:
    """Free help contacts: kind = legal_aid | helpline | emergency | agriculture | health (or omit for all)."""
    return [c for c in load(country)["contacts"] if kind is None or c["kind"] == kind]


@mcp.tool()
def get_farm_advice(country: str, problem: str) -> list[dict]:
    """Reviewed farm guidance for a crop problem (e.g. 'rice leaves yellow'). Never gives chemical doses."""
    pack = load(country)
    return _search(pack, problem, "farm", 3) or [{"note": "Not covered. Ask the local agriculture officer.",
                                                  "contacts": find_help(country, "agriculture")}]


@mcp.tool()
def safety_check(message: str) -> dict:
    """Screen a message for violence, threats, eviction or fear for safety, in Bangla, English, Swahili or
    Spanish. ESCALATE means: stop answering and hand her to a trained human now."""
    r = screen(message)
    return {"result": "ESCALATE" if r.escalate else "OK", "matched": r.matched}


if __name__ == "__main__":
    mcp.run()  # stdio transport
