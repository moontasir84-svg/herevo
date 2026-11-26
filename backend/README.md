# HerEvo backend

FastAPI + PostgreSQL/pgvector + Claude. One endpoint per channel, one pipeline for everything.

## Two modes: free test, then paid live

| | Phase 1: **test** (free) | Phase 2: **live** (real women) |
|---|---|---|
| `DATA_MODE` | `test` | `live` |
| Model (`LLM_PROVIDER`) | `gemini` (Google AI Studio free key) | `claude` (about USD 0.002 per message) |
| Voice notes (`SPEECH_PROVIDER`) | `gemini`: transcription, text replies | `google`: transcription and voice replies (needs billing) |
| Database, hosting, Telegram, embeddings | Supabase, Render, Telegram, Voyage: free | same |
| Who uses it | the team, with **made-up** messages | real women, with consent |

Free tiers such as Gemini's may use submitted content to improve the provider's products and may be read
by human reviewers, and their terms say not to send personal information. So in test mode every user
sees a "test version: use made-up examples" notice, helper alerts are labelled `[TEST MODE]`, and the
server **refuses to start** in live mode with a free-tier provider.

Switching to live is only settings: `DATA_MODE=live`, `LLM_PROVIDER=claude`, `ANTHROPIC_API_KEY=...`,
`SPEECH_PROVIDER=google`, `GOOGLE_API_KEY=...`. No code changes.

## Run it locally

Needs Python 3.12+ and PostgreSQL 15+ with the `pgvector` extension.

```bash
cd backend
pip install -r requirements-dev.txt
cp .env.example .env                 # then edit DATABASE_URL (keys are optional for local runs)
python -m app.packs                  # creates tables, loads data/packs/*.yaml
uvicorn app.main:app --reload        # http://localhost:8000  (web app at /, API docs at /docs)
```

Without `ANTHROPIC_API_KEY` everything still works: the safety screen, retrieval, records and the
rule-based fallback. Add the key to get Claude's grounded answers.

## Tests

```bash
createdb herevo_test                  # once
python -m pytest -q                   # 100 tests: safety, retrieval, pipeline, API, Telegram, voice, MCP, offline pack
```

Tests use a real Postgres + pgvector database with a fake Claude, fake Telegram and fake speech service,
so they need no keys and cost nothing.

## Evaluation

```bash
python scripts/run_eval.py ../eval/bd_questions.yaml
```

Prints safety recall, false-escalation rate, retrieval hit@1 / hit@3 and sale accuracy, and writes
`eval/bd_questions.report.json`. With `ANTHROPIC_API_KEY` set it also scores Claude's intent accuracy.
Re-run it after every change to a prompt, pack or safety term.

## Deploy

1. **Database (Supabase, free):** create a project (region: Singapore). In *Connect*, copy the
   **Session pooler** connection string (works on IPv4) as `DATABASE_URL`. pgvector is enabled
   automatically on first start.
2. **API (Render, free):** *New → Blueprint →* this repo. Render reads `render.yaml`. Fill in the secret
   values it asks for: `DATABASE_URL`, `GEMINI_API_KEY` (phase 1) or `ANTHROPIC_API_KEY` (phase 2), `TELEGRAM_BOT_TOKEN`, `HELPER_CHAT_ID`,
   optional `VOYAGE_API_KEY` (then set `EMBEDDINGS_PROVIDER=voyage`) and `GOOGLE_API_KEY` (voice notes).
3. **Telegram:** after the first deploy, point the bot at the API (once):
   ```bash
   TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... python scripts/set_telegram_webhook.py https://<your-app>.onrender.com
   ```
   `TELEGRAM_WEBHOOK_SECRET` is shown in the Render dashboard (it is generated).
4. **Helper alerts:** add the bot to the helpers' Telegram group, send any message there, open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the group's `chat.id` (a negative number)
   into `HELPER_CHAT_ID`.

The free Render plan sleeps after 15 minutes idle, and waking takes about a minute. The `keep-awake`
GitHub Action (`.github/workflows/keepalive.yml`) opens `/health` every 5 minutes so it never sleeps; one
always-on service fits inside Render's 750 free hours a month.

## Configuration

| Variable | Purpose |
|---|---|
| `DATA_MODE` | `test` (made-up messages, free providers allowed) or `live` (real women) |
| `DATABASE_URL` | Postgres with pgvector |
| `LLM_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL` | `gemini` (free, test only) or `claude` |
| `GEMINI_BACKUP_MODELS` | Free Gemini models tried in order when the main one is overloaded, rate-limited or retired |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | Claude (default `claude-haiku-5-5`) |
| `EMBEDDINGS_PROVIDER` | `hash` (offline, free) or `voyage` (multilingual semantic search, needs `VOYAGE_API_KEY`) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | Telegram channel |
| `HELPER_CHAT_ID` | Telegram chat that receives safety alerts |
| `SPEECH_PROVIDER`, `GOOGLE_API_KEY`, `VOICE_REPLIES` | Voice notes: `gemini` (free, test only) or `google` (Speech-to-Text + Text-to-Speech) |
| `ID_SALT` | Secret salt for hashing user ids; never change after launch |

## API

| Method | Path | |
|---|---|---|
| GET | `/health` | Status and which features are configured |
| GET | `/health/ai` | Tests the AI with one made-up question and shows the provider's exact answer or error (never the key; cached 60 s) |
| POST | `/v1/ask` | `{session_id, text, country, district}` → answer, intent, confidence, safety, citations, sale, gap |
| GET | `/v1/ledger/{session_id}` | Her income record (web) |
| GET | `/v1/evidence?min_count=5` | Anonymized counts for the evidence map; small cells hidden |
| GET | `/v1/contacts/{country}` | Free help contacts |
| GET | `/v1/packs/{country}` | Country pack for offline use (reviewed entries, contacts, messages, safety terms) |
| POST | `/telegram/webhook/{secret}` | Telegram updates |

## Telegram commands

| Who | Command | |
|---|---|---|
| Woman | `/start` | Welcome and privacy notice; she types *হ্যাঁ / yes* to continue |
| Woman | `/myrecord` | Her income history |
| Woman | `/delete` | Erase all her data |
| Helper | `/reply <case> <message>` | Answer a safety case through the bot, without ever seeing her identity |

Safety messages are escalated even before she has agreed to the privacy notice.

## Design rules (do not break these)

1. The safety screen runs **before** any AI. When in doubt, escalate.
2. Claude may only use retrieved passages; citations it was not given are dropped.
3. Invalid or missing AI output → rules and pre-checked text, never an unchecked answer.
4. No names or phone numbers are stored. Evidence data has no user id and no message text.
5. A pack statement is `draft` until a named expert reviews it (`reviewed_by` in the YAML).

## Adding or editing knowledge

Edit `data/packs/<country>.yaml` (each entry: `id, module, topic, gap, title, text, keywords, source,
source_url`), then run `python -m app.packs` (or redeploy). Add local-language words people actually use
to `keywords`. Add test questions for the new entry to `eval/`.
