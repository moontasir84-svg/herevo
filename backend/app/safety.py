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


# Two situations that need different words from HerEvo (both still go to a trained helper at once):
SELF_HARM = {
    "bn": ["আত্মহত্যা", "মরে যেতে চাই", "বাঁচতে চাই না", "নিজেকে শেষ", "নিজের ক্ষতি", "আর বাঁচব না"],
    "en": ["suicid", "kill myself", "end my life", "want to die", "self harm", "hurt myself", "don't want to live",
           "dont want to live"],
    "sw": ["kujiua", "nataka kufa"],
    "es": ["suicid", "matarme", "quiero morir", "no quiero vivir", "hacerme daño"],
}
HARM_INTENT = {
    "bn": ["কীভাবে খুন", "কিভাবে খুন", "খুন করতে", "খুন করব", "মেরে ফেলব", "মেরে ফেলতে", "বিষ দেব", "বিষ দিয়ে"],
    "en": ["how to kill", "kill someone", "kill him", "kill her", "kill them", "poison him", "poison her",
           "poison someone", "how to hurt", "hurt someone", "how to poison"],
    "sw": ["kumuua", "nataka kumuua", "kumtia sumu"],
    "es": ["cómo matar", "como matar", "matarlo", "matarla", "envenenar"],
}


@dataclass
class SafetyResult:
    escalate: bool
    matched: list[str]
    kind: str = "danger"  # "danger" (she is at risk) | "self_harm" | "harm_intent"


def _match(toks: list[str], lists: dict) -> set[str]:
    found = set()
    for terms in lists.values():
        for term in terms:
            parts = term.lower().split()
            n = len(parts)
            for i in range(len(toks) - n + 1):
                window = toks[i : i + n]
                if window[:-1] == parts[:-1] and window[-1].startswith(parts[-1]):
                    found.add(term)
                    break
    return found


def screen(text: str) -> SafetyResult:
    toks = _tokens(text)
    self_harm, harm = _match(toks, SELF_HARM), _match(toks, HARM_INTENT)
    if self_harm:
        return SafetyResult(True, sorted(self_harm), "self_harm")
    if harm:
        return SafetyResult(True, sorted(harm), "harm_intent")
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
