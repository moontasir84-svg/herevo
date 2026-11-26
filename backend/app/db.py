"""Postgres access: a small connection pool and the queries HerEvo needs. Plain SQL, no ORM."""
from __future__ import annotations

import hashlib
from pathlib import Path

from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
_pool: ConnectionPool | None = None


# Connections to a hosted database can be dropped silently while idle (by the pooler, a load balancer or
# the network). Without these settings a query on a dead connection waits for the operating system's TCP
# timeout, which is many minutes. With them a dead connection is noticed in seconds and replaced.
CONNECT_KWARGS = {
    "row_factory": dict_row,
    "connect_timeout": 10,
    "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
    "tcp_user_timeout": 20000,  # ms: give up on unacknowledged sends after 20 s
}


def _configure(conn):
    register_vector(conn)
    conn.execute("SET statement_timeout = '15s'")  # no single query may hold her up
    conn.commit()


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        s = get_settings()
        # The extension must exist before register_vector can find the type.
        import psycopg

        with psycopg.connect(s.database_url, autocommit=True, connect_timeout=10) as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        _pool = ConnectionPool(s.database_url, min_size=1, max_size=5, configure=_configure,
                               kwargs=CONNECT_KWARGS, open=True,
                               check=ConnectionPool.check_connection,  # test each connection before use
                               max_idle=120,  # close connections idle for 2 minutes instead of reusing stale ones
                               timeout=15)  # never wait more than 15 s for a free connection
    return _pool


def init_schema() -> None:
    with pool().connection() as conn:
        conn.execute(SCHEMA.read_text())


def close() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


# ---------- users ----------

def user_id_for(channel: str, channel_user_id: str) -> str:
    salt = get_settings().id_salt
    return hashlib.sha256(f"{salt}:{channel}:{channel_user_id}".encode()).hexdigest()[:32]


def upsert_user(uid: str, channel: str, country: str, language: str) -> dict:
    with pool().connection() as conn:
        conn.execute(
            """INSERT INTO users (id, channel, country, language) VALUES (%s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET language = EXCLUDED.language""",
            (uid, channel, country, language),
        )
        return conn.execute("SELECT * FROM users WHERE id = %s", (uid,)).fetchone()


def set_preferred_language(uid: str, channel: str, country: str, language: str) -> None:
    with pool().connection() as conn:
        conn.execute(
            """INSERT INTO users (id, channel, country, language, preferred_language) VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET preferred_language = EXCLUDED.preferred_language""",
            (uid, channel, country, language, language),
        )


def preferred_language(uid: str) -> str | None:
    with pool().connection() as conn:
        row = conn.execute("SELECT preferred_language FROM users WHERE id = %s", (uid,)).fetchone()
        return row["preferred_language"] if row else None


def set_address(uid: str, address: str) -> None:
    with pool().connection() as conn:
        conn.execute("UPDATE users SET address = %s WHERE id = %s", (address, uid))


def get_user(uid: str) -> dict | None:
    with pool().connection() as conn:
        return conn.execute("SELECT * FROM users WHERE id = %s", (uid,)).fetchone()


def set_consent(uid: str, consented: bool) -> None:
    with pool().connection() as conn:
        conn.execute("UPDATE users SET consented = %s WHERE id = %s", (consented, uid))


def delete_user(uid: str) -> None:
    """Right to be forgotten: removes the user, her sales, escalations and turns (cascade)."""
    with pool().connection() as conn:
        conn.execute("DELETE FROM users WHERE id = %s", (uid,))


# ---------- knowledge ----------

def upsert_chunk(row: dict) -> None:
    with pool().connection() as conn:
        conn.execute(
            """INSERT INTO pack_chunks (id, country, module, topic, title, context, body, keywords,
                                         source_name, source_url, review_status, pack_version, embedding)
               VALUES (%(id)s, %(country)s, %(module)s, %(topic)s, %(title)s, %(context)s, %(body)s, %(keywords)s,
                       %(source_name)s, %(source_url)s, %(review_status)s, %(pack_version)s, %(embedding)s)
               ON CONFLICT (id) DO UPDATE SET module = EXCLUDED.module, topic = EXCLUDED.topic,
                 title = EXCLUDED.title, context = EXCLUDED.context, body = EXCLUDED.body,
                 keywords = EXCLUDED.keywords, source_name = EXCLUDED.source_name,
                 source_url = EXCLUDED.source_url, review_status = EXCLUDED.review_status,
                 pack_version = EXCLUDED.pack_version, embedding = EXCLUDED.embedding""",
            row,
        )


def delete_stale_chunks(country: str, keep_ids: list[str]) -> int:
    with pool().connection() as conn:
        cur = conn.execute("DELETE FROM pack_chunks WHERE country = %s AND NOT (id = ANY(%s))", (country, keep_ids))
        return cur.rowcount


def upsert_contact(row: dict) -> None:
    with pool().connection() as conn:
        conn.execute(
            """INSERT INTO contacts (id, country, kind, name, phone, area, free, source)
               VALUES (%(id)s, %(country)s, %(kind)s, %(name)s, %(phone)s, %(area)s, %(free)s, %(source)s)
               ON CONFLICT (id) DO UPDATE SET kind = EXCLUDED.kind, name = EXCLUDED.name, phone = EXCLUDED.phone,
                 area = EXCLUDED.area, free = EXCLUDED.free, source = EXCLUDED.source""",
            row,
        )


def contacts(country: str, kinds: list[str] | None = None) -> list[dict]:
    with pool().connection() as conn:
        if kinds:
            return conn.execute("SELECT * FROM contacts WHERE country = %s AND kind = ANY(%s) ORDER BY id",
                                (country, kinds)).fetchall()
        return conn.execute("SELECT * FROM contacts WHERE country = %s ORDER BY id", (country,)).fetchall()


def vector_search(country: str, embedding, k: int) -> list[dict]:
    with pool().connection() as conn:
        return conn.execute(
            """SELECT id, module, topic, title, context, body, source_name, source_url, review_status,
                      1 - (embedding <=> %s) AS score
               FROM pack_chunks WHERE country = %s ORDER BY embedding <=> %s LIMIT %s""",
            (embedding, country, embedding, k),
        ).fetchall()


def keyword_search(country: str, terms: list[str], k: int) -> list[dict]:
    """terms: already tokenized and stemmed (see retrieval.query_terms). Prefix match on each term."""
    if not terms:
        return []
    tsq = " | ".join(f"{t}:*" for t in terms[:30])
    with pool().connection() as conn:
        return conn.execute(
            """SELECT id, module, topic, title, context, body, source_name, source_url, review_status,
                      ts_rank(tsv, to_tsquery('simple', %s)) AS score
               FROM pack_chunks WHERE country = %s AND tsv @@ to_tsquery('simple', %s)
               ORDER BY score DESC LIMIT %s""",
            (tsq, country, tsq, k),
        ).fetchall()


# ---------- records ----------

def add_sale(uid: str, sale: dict) -> None:
    with pool().connection() as conn:
        conn.execute(
            "INSERT INTO sales (user_id, product, quantity, unit, amount, currency) VALUES (%s, %s, %s, %s, %s, %s)",
            (uid, sale.get("product"), sale.get("quantity"), sale.get("unit"), sale["amount"], sale["currency"]),
        )


def ledger(uid: str) -> list[dict]:
    with pool().connection() as conn:
        return conn.execute(
            "SELECT product, quantity, unit, amount, currency, created_at FROM sales WHERE user_id = %s ORDER BY created_at",
            (uid,),
        ).fetchall()


def add_case(country: str, district: str | None, intent: str, gap: str | None) -> None:
    with pool().connection() as conn:
        conn.execute("INSERT INTO cases (country, district, intent, gap) VALUES (%s, %s, %s, %s)",
                     (country, district, intent, gap))


def evidence(min_count: int = 1) -> list[dict]:
    """Aggregated, anonymized counts for the evidence map. min_count hides small cells (k-anonymity)."""
    with pool().connection() as conn:
        return conn.execute(
            """SELECT country, gap, count(*)::int AS n FROM cases
               WHERE gap IS NOT NULL GROUP BY country, gap HAVING count(*) >= %s ORDER BY country, gap""",
            (min_count,),
        ).fetchall()


def add_escalation(uid: str, channel: str, reason: str) -> int:
    with pool().connection() as conn:
        return conn.execute(
            "INSERT INTO escalations (user_id, channel, reason) VALUES (%s, %s, %s) RETURNING id",
            (uid, channel, reason),
        ).fetchone()["id"]


def escalation(eid: int) -> dict | None:
    with pool().connection() as conn:
        return conn.execute("SELECT * FROM escalations WHERE id = %s", (eid,)).fetchone()


def mark_contacted(eid: int) -> None:
    with pool().connection() as conn:
        conn.execute("UPDATE escalations SET status = 'contacted', contacted_at = now() WHERE id = %s", (eid,))


def log_turn(row: dict) -> None:
    with pool().connection() as conn:
        conn.execute(
            """INSERT INTO turns (user_id, channel, language, intent, confidence, safety, cited_ids, answered_by,
                                  model, latency_ms, message)
               VALUES (%(user_id)s, %(channel)s, %(language)s, %(intent)s, %(confidence)s, %(safety)s, %(cited_ids)s,
                       %(answered_by)s, %(model)s, %(latency_ms)s, %(message)s)""",
            row,
        )


def purge_old_messages(days: int = 30) -> int:
    with pool().connection() as conn:
        cur = conn.execute("UPDATE turns SET message = NULL WHERE message IS NOT NULL "
                           "AND created_at < now() - make_interval(days => %s)", (days,))
        return cur.rowcount
