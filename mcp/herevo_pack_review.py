"""HerEvo pack-review MCP server: lets a partner or reviewer check a country pack
against HerEvo's content rules before it is allowed to answer a woman's question.
Run: pip install "mcp>=2"  then  python herevo_pack_review.py   (stdio transport)"""
from datetime import date
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("herevo-pack-review")

REQUIRED = ["language", "rights", "legal_aid", "source"]
REVIEW_MONTHS = 6
# Content HerEvo must never carry, however it is phrased.
BANNED = ["mg ", "ml ", "dose", "dosage", "tablet", "spray at", "per acre", "per hectare",
          "you should sue", "file a case against", "guaranteed to win"]


@mcp.tool()
def required_fields() -> list[str]:
    """The fields every HerEvo country pack must contain before it can be used."""
    return REQUIRED


@mcp.tool()
def banned_content() -> dict:
    """The scope limits every pack must respect: no medicine or chemical doses, no legal advice."""
    return {"patterns": BANNED,
            "rule": "General rights information plus a free legal aid contact only. Never a medicine, pesticide or fertilizer dose."}


@mcp.tool()
def validate_pack(pack: dict) -> dict:
    """Check one country pack: required fields present, every rights statement sourced, no banned content."""
    problems = []
    for field in REQUIRED:
        if not pack.get(field):
            problems.append(f"missing required field: {field}")
    if not pack.get("source"):
        problems.append("rights statements have no source line")
    for topic, text in (pack.get("rights") or {}).items():
        low = str(text).lower()
        for bad in BANNED:
            if bad in low:
                problems.append(f"rights['{topic}'] contains banned content: '{bad.strip()}'")
        if "not legal advice" not in low:
            problems.append(f"rights['{topic}'] does not say it is general information, not legal advice")
    for problem, text in (pack.get("farm") or {}).items():
        low = str(text).lower()
        for bad in BANNED:
            if bad in low:
                problems.append(f"farm['{problem}'] contains a dose or rate: '{bad.strip()}'")
    return {"valid": not problems, "problems": problems,
            "checked": len(pack.get("rights") or {}) + len(pack.get("farm") or {})}


@mcp.tool()
def check_freshness(last_reviewed: str) -> dict:
    """Is a pack still fresh? HerEvo flags every pack for review after 6 months. Date as YYYY-MM-DD."""
    try:
        y, m, d = (int(p) for p in last_reviewed.split("-"))
        reviewed = date(y, m, d)
    except Exception:
        raise ValueError("last_reviewed must be a date like 2026-11-20")
    months = (date.today().year - reviewed.year) * 12 + (date.today().month - reviewed.month)
    return {"last_reviewed": last_reviewed, "months_since": months,
            "review_due": months >= REVIEW_MONTHS,
            "rule": f"Every country pack is flagged for human review after {REVIEW_MONTHS} months."}


if __name__ == "__main__":
    mcp.run()  # stdio transport
