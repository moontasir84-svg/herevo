"""HerEvo evidence MCP server: serves the anonymized rights-gap evidence map.
All counts are aggregate only - no caller, no personal field, ever.
Run: pip install "mcp>=2"  then  python herevo_evidence.py   (stdio transport)"""
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("herevo-evidence")

GAP_TYPES = {
    "land_and_inheritance": "Land, property and inheritance denied or withheld",
    "unpaid_wages": "Work done but wages unpaid or underpaid",
    "family_support": "Child support or maintenance not provided",
    "safety_threats": "Violence, threats or forced eviction",
}

# Aggregate counts only. Demo data, synthetic, written by the team.
COUNTS = {
    "bangladesh": {"land_and_inheritance": 41, "unpaid_wages": 12, "family_support": 9, "safety_threats": 17},
    "kenya": {"land_and_inheritance": 14, "unpaid_wages": 38, "family_support": 11, "safety_threats": 15},
    "mexico": {"land_and_inheritance": 8, "unpaid_wages": 13, "family_support": 34, "safety_threats": 19},
}
ISO2 = {"bangladesh": "BD", "kenya": "KE", "mexico": "MX"}


def _country(name: str) -> str:
    key = name.strip().lower()
    if key not in COUNTS:
        raise ValueError(f"No evidence data for '{name}'. Available: {', '.join(COUNTS)}")
    return key


@mcp.tool()
def list_gap_types() -> dict:
    """List the four categories HerEvo classifies a rights gap into, with their meaning."""
    return GAP_TYPES


@mcp.tool()
def gap_counts(country: str) -> dict:
    """Anonymized report counts by gap type for one country, for the evidence map."""
    key = _country(country)
    return {"country": key, "iso2": ISO2[key], "counts": COUNTS[key], "total": sum(COUNTS[key].values())}


@mcp.tool()
def top_gaps(limit: int = 3) -> list[dict]:
    """The country and gap type pairs with the most reports, highest first."""
    rows = [{"country": c, "gap_type": g, "reports": n} for c, gs in COUNTS.items() for g, n in gs.items()]
    return sorted(rows, key=lambda r: r["reports"], reverse=True)[: max(1, limit)]


@mcp.tool()
def country_summary(country: str) -> dict:
    """One-line plain summary of where law and services fail women most in this country."""
    key = _country(country)
    worst = max(COUNTS[key], key=COUNTS[key].get)
    return {"country": key, "largest_gap": worst, "reports": COUNTS[key][worst],
            "meaning": GAP_TYPES[worst], "note": "Aggregate counts only; no individual case is stored or served."}


@mcp.tool()
def as_csv() -> str:
    """The whole anonymized map as CSV, for partner dashboards and evidence briefs."""
    lines = ["country,iso2,gap_type,reports"]
    for c, gs in COUNTS.items():
        for g, n in gs.items():
            lines.append(f"{c},{ISO2[c]},{g},{n}")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()  # stdio transport
