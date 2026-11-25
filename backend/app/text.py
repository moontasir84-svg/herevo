"""Language-aware text helpers shared by retrieval, the safety screen and the MCP server.
Pure Python, no dependencies.

Bangla note: Python treats Bangla vowel signs as non-letters, so never split Bangla with isalnum()
or \\w; split on whitespace and punctuation instead."""
from __future__ import annotations

import re

SPLIT = re.compile(r"[\s\.,!?।॥;:\"'“”‘’()\[\]{}\-–—/&|<>*@#%^+=~`\\]+")
BANGLA = re.compile(r"[ঀ-৿]")

# Common Bangla inflection suffixes (case markers, classifiers, plurals). Longest first.
BN_SUFFIXES = sorted(["গুলোকে", "গুলোর", "গুলো", "দেরকে", "দের", "টাকে", "টির", "টার", "টা", "টি", "কে", "য়ের",
                      "ের", "র", "তে", "য়", "ে"], key=len, reverse=True)
STOPWORDS = {"আমার", "আমি", "আমাকে", "আমারে", "আমাদের", "না", "কি", "কী", "কেন", "করব", "করবো", "হয়ে", "হয়", "এবং",
             "ও", "আর", "এই", "সে", "তার", "যাচ্ছে", "the", "a", "an", "my", "is", "to", "of", "and", "i", "me", "what",
             "how", "do", "for", "in", "on", "it", "can", "mi", "el", "la", "de", "que", "y", "na", "ya", "wa"}


def tokens(text: str) -> list[str]:
    return [t for t in SPLIT.split(text.lower()) if t]


def stem(word: str) -> str:
    if BANGLA.search(word):
        for suf in BN_SUFFIXES:
            if word.endswith(suf) and len(word) - len(suf) >= 2:
                return word[: -len(suf)]
    return word


def query_terms(text: str) -> list[str]:
    out: list[str] = []
    for tok in tokens(text):
        if len(tok) < 2 or tok in STOPWORDS:
            continue
        t = stem(tok)
        if t not in out:
            out.append(t)
    return out
