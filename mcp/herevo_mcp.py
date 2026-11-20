"""HerEvo country-pack MCP server: lets any MCP-compatible AI assistant reuse HerEvo's reviewed content.
Run: pip install "mcp>=2"  then  python herevo_mcp.py   (stdio transport)"""
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("herevo-country-packs")

PACKS = {
    "bangladesh": {
        "language": "Bangla",
        "rights": {
            "inheritance": "Under Muslim inheritance law in Bangladesh a widow receives one-eighth of her husband's estate if there are children and one-quarter if there are none. Hindu and other personal laws differ. General information, not legal advice.",
        },
        "legal_aid": ["Rangpur District Legal Aid Office (National Legal Aid Services Organisation), free", "BLAST", "Ain o Salish Kendra"],
        "farm": {"rice yellowing": "Yellowing of older, lower rice leaves first usually means nitrogen deficiency. Check with the local Sub-Assistant Agriculture Officer before applying fertilizer."},
        "source": "Muslim personal law; World Bank Women, Business and the Law 2026 (Assets)",
    },
    "kenya": {
        "language": "Swahili",
        "rights": {
            "wages": "Kenya's Employment Act (2007) covers domestic workers; an employer must pay wages that are due. Unpaid wages can be reported free of charge to the County Labour Office. General information, not legal advice.",
        },
        "legal_aid": ["County Labour Office, free", "KUDHEIHA (domestic workers' union)", "National Legal Aid Service (NLAS)", "FIDA Kenya"],
        "farm": {},
        "source": "Employment Act 2007; World Bank Women, Business and the Law 2026 (Pay)",
    },
    "mexico": {
        "language": "Spanish",
        "rights": {
            "child support": "In Mexico both parents are legally obliged to provide child support (pension alimenticia); a mother can claim it before a family judge. General information, not legal advice.",
        },
        "legal_aid": ["Defensoria Publica de Oaxaca, free", "DIF"],
        "farm": {},
        "source": "Civil law (pension alimenticia); World Bank Women, Business and the Law 2026 (Parenthood)",
    },
}


def _pack(country: str) -> dict:
    pack = PACKS.get(country.strip().lower())
    if not pack:
        raise ValueError(f"No country pack for '{country}'. Available: {', '.join(PACKS)}")
    return pack


@mcp.tool()
def list_countries() -> list[str]:
    """List the countries that have a reviewed HerEvo country pack."""
    return sorted(PACKS)


@mcp.tool()
def get_rights(country: str, topic: str) -> dict:
    """Return general rights information for a woman's situation (e.g. topic='inheritance', 'wages', 'child support'), with its source. Not legal advice."""
    pack = _pack(country)
    for key, text in pack["rights"].items():
        if key in topic.lower() or topic.lower() in key:
            return {"country": country, "topic": key, "answer": text, "source": pack["source"]}
    return {"country": country, "topic": topic, "answer": None, "note": "Not covered by this pack. Refer her to free legal aid.", "legal_aid": pack["legal_aid"]}


@mcp.tool()
def find_legal_aid(country: str) -> list[str]:
    """Return free legal aid contacts for a country."""
    return _pack(country)["legal_aid"]


@mcp.tool()
def get_farm_advice(country: str, problem: str) -> dict:
    """Return reviewed farm guidance for a crop problem (e.g. 'rice yellowing'). Never gives chemical doses."""
    pack = _pack(country)
    for key, text in pack["farm"].items():
        if all(word in problem.lower() for word in key.split()):
            return {"problem": key, "advice": text}
    return {"problem": problem, "advice": None, "note": "Not covered. Ask the local agriculture officer."}


@mcp.tool()
def safety_check(message: str) -> dict:
    """Screen a message for violence, threats or eviction. ESCALATE means: hand her to a trained human now."""
    words = ["threat", "beat", "hit", "hurt", "kill", "evict", "throw me out", "violence", "afraid", "scared"]
    flagged = [w for w in words if w in message.lower()]
    return {"result": "ESCALATE" if flagged else "OK", "matched": flagged}


if __name__ == "__main__":
    mcp.run()  # stdio transport
