"""Hybrid retrieval: pgvector similarity + Postgres keyword search, merged with reciprocal rank fusion."""
from __future__ import annotations

from . import db
from .config import get_settings
from .embeddings import get_embedder
from .text import query_terms


def rrf(result_lists: list[list[dict]], k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    rows: dict[str, dict] = {}
    for results in result_lists:
        for rank, row in enumerate(results):
            scores[row["id"]] = scores.get(row["id"], 0.0) + 1.0 / (k + rank + 1)
            rows.setdefault(row["id"], row)
    ordered = sorted(scores, key=scores.get, reverse=True)
    return [{**rows[i], "rrf": round(scores[i], 5)} for i in ordered]


def retrieve(country: str, query: str, top_k: int | None = None) -> list[dict]:
    top_k = top_k or get_settings().top_k
    qvec = get_embedder().embed([query], input_type="query")[0]
    vec_hits = db.vector_search(country, qvec, top_k * 2)
    kw_hits = db.keyword_search(country, query_terms(query), top_k * 2)
    # Keyword hits carry more weight with the offline hash embedder, which is not semantic.
    lists = [kw_hits, kw_hits, vec_hits] if get_settings().embeddings_provider != "voyage" else [vec_hits, kw_hits]
    kw_ids = {h["id"] for h in kw_hits}
    return [{**p, "keyword_match": p["id"] in kw_ids} for p in rrf(lists)[:top_k]]
