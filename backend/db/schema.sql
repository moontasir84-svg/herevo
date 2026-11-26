-- HerEvo database schema. Idempotent: safe to run on every start.
-- Works on Supabase or any PostgreSQL 15+ with the pgvector extension.

CREATE EXTENSION IF NOT EXISTS vector;

-- Reviewed knowledge from country packs, one row per chunk.
CREATE TABLE IF NOT EXISTS pack_chunks (
    id            TEXT PRIMARY KEY,               -- e.g. bd-rights-inheritance-muslim#0
    country       TEXT NOT NULL,                  -- ISO-2, e.g. BD
    module        TEXT NOT NULL,                  -- rights | farm | money | safety | health
    topic         TEXT NOT NULL,
    title         TEXT NOT NULL,
    context       TEXT NOT NULL,                  -- short context line prepended before embedding
    body          TEXT NOT NULL,                  -- the reviewed statement itself
    keywords      TEXT NOT NULL DEFAULT '',       -- local-language keywords for keyword search
    source_name   TEXT NOT NULL,
    source_url    TEXT,
    review_status TEXT NOT NULL DEFAULT 'draft',  -- draft | reviewed
    pack_version  TEXT NOT NULL,
    embedding     vector(1024),
    tsv           tsvector GENERATED ALWAYS AS
                  (to_tsvector('simple', title || ' ' || context || ' ' || body || ' ' || keywords)) STORED
);
CREATE INDEX IF NOT EXISTS pack_chunks_country_idx ON pack_chunks (country, module);
CREATE INDEX IF NOT EXISTS pack_chunks_tsv_idx ON pack_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS pack_chunks_embedding_idx ON pack_chunks USING hnsw (embedding vector_cosine_ops);

-- Free help contacts (legal aid, helplines, agriculture, health).
CREATE TABLE IF NOT EXISTS contacts (
    id        TEXT PRIMARY KEY,
    country   TEXT NOT NULL,
    kind      TEXT NOT NULL,                      -- legal_aid | helpline | agriculture | health | emergency
    name      TEXT NOT NULL,
    phone     TEXT,
    area      TEXT,
    free      BOOLEAN NOT NULL DEFAULT TRUE,
    source    TEXT NOT NULL
);

-- People, identified only by a salted hash of their channel id.
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,                 -- sha256(salt + channel + channel_user_id)
    channel     TEXT NOT NULL,                    -- telegram | web | voice | sms
    country     TEXT NOT NULL DEFAULT 'BD',
    district    TEXT,
    language    TEXT NOT NULL DEFAULT 'bn',
    consented   BOOLEAN NOT NULL DEFAULT FALSE,   -- agreed to the privacy notice
    address     TEXT,                             -- channel address needed to reply (e.g. Telegram chat id); never a phone number
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Her income record. Shared with a lender only with explicit consent.
CREATE TABLE IF NOT EXISTS sales (
    id         BIGSERIAL PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product    TEXT,
    quantity   NUMERIC,
    unit       TEXT,
    amount     NUMERIC NOT NULL,
    currency   TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Anonymized cases for the evidence map. No user id, no message text.
CREATE TABLE IF NOT EXISTS cases (
    id         BIGSERIAL PRIMARY KEY,
    country    TEXT NOT NULL,
    district   TEXT,
    intent     TEXT NOT NULL,                     -- RIGHTS | SAFETY | EARN | ADVICE
    gap        TEXT,                              -- land_inheritance | unpaid_wages | family_support | safety_threats | other
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Safety escalations handed to a trained human.
CREATE TABLE IF NOT EXISTS escalations (
    id           BIGSERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel      TEXT NOT NULL,
    reason       TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',    -- open | contacted | closed
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    contacted_at TIMESTAMPTZ
);

-- Audit trail of every AI turn (for evaluation and lineage). Message text is kept 30 days max.
CREATE TABLE IF NOT EXISTS turns (
    id          BIGSERIAL PRIMARY KEY,
    user_id     TEXT REFERENCES users(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL,
    language    TEXT NOT NULL,
    intent      TEXT NOT NULL,
    confidence  REAL,
    safety      TEXT NOT NULL,
    cited_ids   TEXT[] NOT NULL DEFAULT '{}',
    answered_by TEXT NOT NULL,                    -- claude | fallback | safety_rule
    model       TEXT,
    latency_ms  INTEGER,
    message     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Upgrades for databases created by earlier versions
ALTER TABLE users ADD COLUMN IF NOT EXISTS address TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_language TEXT;
ALTER TABLE turns ADD COLUMN IF NOT EXISTS reply TEXT;
-- Helper replies waiting for a woman who wrote on the website (picked up by her open page; deleted after 30 days)
CREATE TABLE IF NOT EXISTS inbox (
    id            BIGSERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    escalation_id BIGINT,
    text          TEXT,
    delivered     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS inbox_user_idx ON inbox (user_id, delivered);
ALTER TABLE inbox ADD COLUMN IF NOT EXISTS quote TEXT;
ALTER TABLE inbox ADD COLUMN IF NOT EXISTS tg_message_id BIGINT;  -- the helper's message in the helpers' group  -- her message the helper answered  -- HerEvo's answer, so the conversation can continue (deleted after 30 days)  -- set when she asks for a language
