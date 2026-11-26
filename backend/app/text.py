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
             "how", "do", "for", "in", "on", "it", "can", "you", "your", "with", "help", "about", "this", "that", "have",
             "has", "are", "was", "be", "will", "would", "could", "should", "please", "kind", "information", "get",
             "need", "want", "tell", "give", "gets", "got", "does", "any", "some", "there", "they", "them", "who", "which", "where", "when", "mi", "el", "la", "de", "que", "y", "na", "ya", "wa",
             # Bangla function words that say nothing about the topic ("from", "after", "how", "will take"...)
             "থেকে", "পর", "পরে", "আগে", "কীভাবে", "কিভাবে", "কত", "কতটুকু", "কতবার", "দেখে", "নেব", "দেব", "পাব",
             "হবে", "করে", "করা", "ঠিক", "কোথায়", "কখন", "এখন", "খুব", "দিচ্ছে", "হচ্ছে", "যায়", "আছে", "নেই",
             "জন্য", "সাথে", "সঙ্গে", "উচিত", "যদি", "কাছ", "কাছে", "হিসেবে", "আপনি", "আপনার", "তুমি", "তোমার",
             "আমরা", "একটা", "কোনো", "সময়", "ব্যবহার",
             "আমাদের", "কার", "গেছে", "চাই", "তাদের", "দিয়ে", "দেয়", "নিয়ে", "পারব", "পারি", "বলছে", "হয়েছে"}


def tokens(text: str) -> list[str]:
    return [t for t in SPLIT.split(text.lower()) if t]


EN_SUFFIXES = ("ingly", "ings", "ing", "edly", "ers", "er", "ies", "ied", "es", "ed", "s")


def _stem_en(word: str) -> str:
    """Light English stemming: farming/farmer/farms -> farm, crops -> crop, paid stays paid."""
    if not word.isascii() or len(word) < 5:
        return word
    for suf in EN_SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            base = word[: -len(suf)]
            if suf in ("ies", "ied"):
                base += "y"
            if len(base) > 3 and base[-1] == base[-2] and base[-1] not in "aeiouls":  # stopped -> stop
                base = base[:-1]
            return base
    return word


def stem(word: str) -> str:
    if not BANGLA.search(word):
        return _stem_en(word)
    if BANGLA.search(word):
        for suf in BN_SUFFIXES:
            if word.endswith(suf) and len(word) - len(suf) >= 3:  # never cut a word down to a fragment
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
