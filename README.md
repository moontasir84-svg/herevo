# HerEvo

**Her land. Her harvest. Her money. Her health. Her safety.**
HerEvo brings expert help for all of it into one AI system that works on any phone, even offline, and turns her voice into the evidence that changes laws and services.

Built for the International AI Builders Congress 2026.

## Live demo
https://claude.ai/artifact/SCWQpdJRjA2xrUhPNR2WMN
Pick a caller (Rina in Bangladesh, Amina in Kenya, Lucía in Mexico), then tap what she says.

## What the prototype shows
- Land & harvest: farm advice from local guidance
- Money & work: a spoken sale becomes an income record (shared only with her consent)
- Rights: answers from her country's actual law, plus the nearest free legal aid
- Safety: threats go straight to a trained human; the AI never handles violence alone
- Evidence: every case adds an anonymized point to a map of where laws and services fail women
- Next: Health (maternal and menstrual, led by our NRB medical lead) and Learning

## How it works
Any phone → speech to text → safety screen (always first) → intent router (RIGHTS / EARN / ADVICE / SAFETY) → Claude answers only from a verified country pack → spoken reply → anonymized evidence map.
Every answer shows its intent, confidence and source. Invalid model output falls back to a rule-based multilingual router.

## MCP server
`mcp/herevo_mcp.py` exposes HerEvo's reviewed country packs as an MCP server (stdio) with 5 tools: list_countries, get_rights, find_legal_aid, get_farm_advice, safety_check.
Run: `pip install -r mcp/requirements.txt` then `python mcp/herevo_mcp.py`

## Run the demo
HerEvo.html is a single self-contained file. Open it in any browser; no build step or install needed.

## Stack
HTML, CSS, JavaScript · jsVectorMap 1.5.3 · Claude (Anthropic) · Claude Artifacts runtime · MCP Python SDK · Web Speech API · Playwright (testing) · Kokoro-82M + FFmpeg (pitch video)

## Cost
About USD 0.10 per 3-minute call; about USD 2.30 per woman per year. Free for women, paid by programmes, lenders and mobile operators.

## Data
Public: World Bank Women, Business and the Law 2026, FAO, ILO, WHO, GSMA. Callers, sales and map counts are synthetic sample data. Rights answers are general information, not legal advice.

## Team
Six members, three of them women, including an NRB medical lead.
