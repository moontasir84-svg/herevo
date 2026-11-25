"""Safety screen. Runs BEFORE any AI call. Any sign of violence, threats, eviction or fear for safety
returns ESCALATE: the woman is handed to a trained human and the AI does not counsel her.
When in doubt, escalate. Term lists must be reviewed per language by native speakers.

Matching is by word prefix, not substring: Bangla adds suffixes (মারছে, মারবে), but a plain substring
search would flag harmless words (the verb "মারে" is inside "আমারে", "to me")."""
from __future__ import annotations

from dataclasses import dataclass

from .text import tokens as _tokens

TERMS = {
    "bn": ["মারধর", "মারে", "মারছে", "মারবে", "মেরে", "পেটায়", "পেটাচ্ছে", "হুমকি", "খুন", "বের করে দেবে",
           "বের করে দিচ্ছে", "বের করে দিয়েছে", "তাড়িয়ে", "নির্যাতন", "ধর্ষণ", "জোর করে", "ভয় পাচ্ছি", "ভয় লাগছে",
           "আত্মহত্যা", "অ্যাসিড ছুড়", "এসিড মার", "আটকে রেখেছে", "গায়ে হাত", "যৌন হয়রানি"],
    "en": ["hit me", "hits me", "beat", "threat", "kill", "throw me out", "kick me out", "evict", "violen", "abuse",
           "rape", "forced", "afraid", "scared", "suicid", "acid attack", "locked me", "hurt me", "hurts me", "harass"],
    "sw": ["ananipiga", "kunipiga", "kunitishia", "ananitishia", "kuniua", "ubakaji", "naogopa", "vurugu", "kunifukuza"],
    "es": ["me pega", "golpe", "amenaz", "matar", "violencia", "violación", "miedo", "me corre", "echar de la casa",
           "abuso", "suicid"],
}


@dataclass
class SafetyResult:
    escalate: bool
    matched: list[str]


def screen(text: str) -> SafetyResult:
    toks = _tokens(text)
    found = set()
    for terms in TERMS.values():
        for term in terms:
            parts = term.lower().split()
            n = len(parts)
            for i in range(len(toks) - n + 1):
                window = toks[i : i + n]
                # every word must match exactly, except the last, which may carry a suffix
                if window[:-1] == parts[:-1] and window[-1].startswith(parts[-1]):
                    found.add(term)
                    break
    return SafetyResult(escalate=bool(found), matched=sorted(found))
