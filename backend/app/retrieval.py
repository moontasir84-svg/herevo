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
    if get_settings().embeddings_provider != "voyage":
        # The offline hash embedder is not semantic (close to random for Bangla), so it must not outrank a
        # keyword match: keyword hits come first in keyword order, and the embedder only fills empty places.
        seen = {h["id"] for h in kw_hits}
        ordered = list(kw_hits) + [h for h in vec_hits if h["id"] not in seen]
        kw_ids = {h["id"] for h in kw_hits}
        return [{**p, "rrf": None, "keyword_match": p["id"] in kw_ids} for p in ordered[:top_k]]
    lists = [vec_hits, kw_hits]
    kw_ids = {h["id"] for h in kw_hits}
    return [{**p, "keyword_match": p["id"] in kw_ids} for p in rrf(lists)[:top_k]]
