# HerEvo backend

FastAPI + PostgreSQL/pgvector + Claude. One endpoint per channel, one pipeline for everything.

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
python -m pytest -q                   # 51 tests: safety, retrieval, pipeline, API, Telegram, voice, MCP
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
   values it asks for: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `HELPER_CHAT_ID`,
   optional `VOYAGE_API_KEY` (then set `EMBEDDINGS_PROVIDER=voyage`) and `GOOGLE_API_KEY` (voice notes).
3. **Telegram:** after the first deploy, point the bot at the API (once):
   ```bash
   TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... python scripts/set_telegram_webhook.py https://<your-app>.onrender.com
   ```
   `TELEGRAM_WEBHOOK_SECRET` is shown in the Render dashboard (it is generated).
4. **Helper alerts:** add the bot to the helpers' Telegram group, send any message there, open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the group's `chat.id` (a negative number)
   into `HELPER_CHAT_ID`.

The free Render plan sleeps after 15 minutes idle; the first message after that takes up to a minute.
Use a paid instance (or a ping) for the pilot and the Congress Day demo.

## Configuration

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres with pgvector |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | Claude (default `claude-haiku-5-5`) |
| `EMBEDDINGS_PROVIDER` | `hash` (offline, free) or `voyage` (multilingual semantic search, needs `VOYAGE_API_KEY`) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | Telegram channel |
| `HELPER_CHAT_ID` | Telegram chat that receives safety alerts |
| `GOOGLE_API_KEY`, `VOICE_REPLIES` | Voice notes: Google Speech-to-Text and Text-to-Speech |
| `ID_SALT` | Secret salt for hashing user ids; never change after launch |

## API

| Method | Path | |
|---|---|---|
| GET | `/health` | Status and which features are configured |
| POST | `/v1/ask` | `{session_id, text, country, district}` → answer, intent, confidence, safety, citations, sale, gap |
| GET | `/v1/ledger/{session_id}` | Her income record (web) |
| GET | `/v1/evidence?min_count=5` | Anonymized counts for the evidence map; small cells hidden |
| GET | `/v1/contacts/{country}` | Free help contacts |
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
