# HerEvo

**Her land. Her harvest. Her money. Her health. Her safety.**
HerEvo brings expert help for all of it into one AI system that works on any phone, even offline, and turns her voice into the evidence that changes laws and services.

Finalist, International AI Builders Congress 2026 · Team Headless Chickens

## What is in this repo

| Folder | What it is |
|---|---|
| [`backend/`](backend/) | The HerEvo API (Python, FastAPI, PostgreSQL + pgvector, Claude). Safety screen, hybrid retrieval, grounded answers, income records, evidence map, Telegram bot with voice notes. [Setup and deploy guide](backend/README.md). |
| [`data/packs/`](data/packs/) | Country packs: reviewed knowledge for Bangladesh, Kenya and Mexico, every statement with its source. A new country is a new pack, not new code. |
| [`web/`](web/) | The web app. Served by the backend, every message goes through the real system; opened anywhere else, it runs as a scripted demo. |
| [`mcp/`](mcp/) | Three MCP servers: `herevo-country-packs` (the same packs and safety screen as the app, so NGOs and other AI assistants can reuse HerEvo's verified content), `herevo-evidence` (anonymized rights-gap counts for the evidence map) and `herevo-pack-review` (checks a country pack against HerEvo's content rules before it can answer anyone). |
| [`eval/`](eval/) | Evaluation set (50 questions) and its latest report. |
| [`docs/`](docs/) | Architecture. |

## How one message is handled

```
her message (Telegram text or voice note, web, later SMS and phone calls)
  → speech to text (voice notes)
  → SAFETY SCREEN (rules, before any AI) ── threat? → trained human helper alerted, case opened
  → hybrid retrieval from her country pack (pgvector + keyword search, rank fusion)
  → one Claude call → structured, validated answer that may only use the retrieved passages
  → (invalid or unavailable AI → rules + pre-checked text, never an unchecked answer)
  → reply in her language (text, and a voice note if she spoke)
  → records: income (if a sale), anonymized case for the evidence map, audit log
```

## Evaluation (latest run)

50 questions (Bangla and English): 12 safety, 16 rights, 6 farm, 6 money, 4 health, 6 sales. Offline mode (no Claude key yet).

| Metric | Result |
|---|---|
| Safety recall (threats escalated to a human) | **12 / 12 (100%)** |
| False escalations | **0 / 38 (0%)** |
| Right knowledge in the top 3 | **100%** |
| Right knowledge first | 84% |
| Sale amounts recorded correctly | **6 / 6** |

The questions were written by the team; the next version adds real questions from field interviews. Run it yourself: see [backend/README.md](backend/README.md#evaluation).

## Live demo

- Prototype (scripted, on Claude): https://claude.ai/artifact/SCWQpdJRjA2xrUhPNR2WMN
- Live system: deployed from this repo (see [backend/README.md](backend/README.md#deploy))

## Cost

About USD 0.10 per 3-minute call; about USD 2.30 per woman per year. Free for women, paid by programmes, lenders and mobile operators.

## Data and safety

Country packs use public sources (World Bank Women, Business and the Law 2026, national laws, government services). Every statement is marked **draft** until a named lawyer, agriculture officer or clinician reviews it. HerEvo gives general information, not legal or medical advice. No names or phone numbers are stored; users are identified by a salted hash, and `/delete` erases her data.

## Team

Five members, two of them women, including our NRB medical lead.

| Member | Role |
|---|---|
| Md. Moontasir Arafat | Team lead, backend |
| Md. Fahad Sarker | Full-stack developer |
| Jannatul Ferdous Mredula | Product and UX designer |
| Kazi Zubayer Ahmad | AI/ML engineer |
| Meftahul Jannat | NRB domain expert, health lead |
