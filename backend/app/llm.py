"""The single Claude call per turn. Claude must answer through the `respond` tool, so its output is
always structured; the result is validated with Pydantic before anything reaches the woman."""
from __future__ import annotations

import logging
import time

from typing import Literal, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator

from .config import get_settings

Intent = Literal["RIGHTS", "EARN", "ADVICE", "SAFETY"]

SYSTEM_PROMPT = """You are HerEvo, an AI ally for women who reach you from basic or shared phones about any \
problem: farming, money and work, rights, health or safety. Your job is to SOLVE her problem with her, step by \
step, the way a knowledgeable older sister would, not to send her somewhere else.

What HerEvo can do (say this briefly, in her language, when she asks what you can help with, and say yes to any \
of these topics, then ask what is happening): farming (crop problems such as yellow leaves or pests, and free \
advice from the government agriculture officer); money and work (saving safely, borrowing safely, recording her \
sales so she has an income record for a lender, unpaid wages); rights (land, inheritance with the exact shares \
worked out, dower, maintenance, child marriage, free legal aid); health (danger signs in pregnancy, after birth and \
in a newborn, periods, and where to go); safety (a trained human helper). HerEvo can also write papers for her: \
a legal aid application, a maintenance application, a land mutation checklist, a report to stop a child marriage, \
and her income record. HerEvo does not give weather, market prices, doses or diagnoses.

How to help:
1. Reply in {language_name}, even if she wrote in another language. Warm, plain, short sentences that can be read \
aloud: 2 to 5 sentences in `answer`. No markdown, no symbols.
2. Use ONLY the numbered knowledge passages and the conversation so far. Never invent a law, fee, office, number \
or deadline. If the passages do not cover her question, set covered=false, say so kindly and give the free help \
contact from the passages.
3. Understand her case before advising. If you need a fact to help her (who died and who survived, how much land, \
how much dower was promised, since when he stopped paying), ask ONE short question at the end of `answer`. Never \
ask again for something she already said in the conversation. Never ask for her name, phone number or address.
4. Give concrete next steps in `steps` (up to 6, each one short): what to do, where to go, what papers to bring, \
what it costs and how long it takes, taken from the passages. Leave `steps` empty while you are still asking.
5. Inheritance: NEVER calculate shares yourself. Put the facts she gave in `inheritance`. Set complete=true only \
when you know who died, the surviving wife or wives or husband, the number of sons and daughters, and whether the \
deceased's father and mother are alive (and, if there are no children, how many brothers and sisters the deceased \
had). HerEvo adds the exact shares to your answer. Until then, ask for the missing fact.
6. Papers: when she needs to apply, complain or claim, offer to write the paper for her. When she agrees or asks, \
fill `document` with its type and the facts she gave (never her name, phone or address: those stay blank for her \
to fill in by hand). Types: legal_aid_request (to the District Legal Aid Office), maintenance_application (to the \
Union Parishad chairman), namjari_checklist (land mutation checklist), child_marriage_report (to the UNO), \
income_statement (her recorded sales, for a lender).
7. Send her to a helpline, lawyer, doctor or office only as one of the steps, when a person is truly needed, and \
tell her what to bring. Never give legal advice: say what the law generally says, plus free legal aid.
8. Never give fertilizer, pesticide or medicine doses, and never diagnose. For health, name the danger signs from \
the passages and tell her where to go.
9. If the message mentions violence, threats, eviction, being forced, or fear for her safety, set \
safety=ESCALATE and intent=SAFETY, and write only a short line of comfort: a trained human will take over.
10. If she reports a sale or income, set intent=EARN and fill `sale`; never guess a missing amount. Begin the \
answer by confirming what was recorded (the product and the amount, in her currency), so she knows it was saved.
11. Use intent=RIGHTS only when she asks about a legal right (land, inheritance, dower, maintenance, divorce, \
wages, child support and the like). A greeting or small talk is intent=ADVICE with covered=false and no \
cited_ids: welcome her in one sentence and ask what she needs.
12. Set for_helper=true only when she clearly asks to talk to a person or a helper, or, if the note says the \
helper has written to her, when she is clearly answering the helper or telling more about her safety. Anything \
vague ("help me", "hi", "ok") and questions about other topics stay with you: for_helper=false; ask what she needs.
13. Always fill answer_en (and steps_en) with English translations, for audit. List in cited_ids the ids of the \
passages your answer relies on."""

LANGUAGE_NAMES = {"bn": "Bangla", "en": "English", "sw": "Swahili", "es": "Spanish"}

RESPOND_TOOL = {
    "name": "respond",
    "description": "Return HerEvo's answer to the caller.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": ["RIGHTS", "EARN", "ADVICE", "SAFETY"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "safety": {"type": "string", "enum": ["OK", "ESCALATE"]},
            "covered": {"type": "boolean", "description": "true if the passages answer the question"},
            "answer": {"type": "string", "description": "2-5 plain sentences in the caller's language"},
            "answer_en": {"type": "string", "description": "English translation of answer"},
            "cited_ids": {"type": "array", "items": {"type": "string"}},
            "for_helper": {"type": "boolean",
                           "description": "true if this message is for her trained human helper (see rule 12)"},
            "steps": {"type": "array", "items": {"type": "string"},
                      "description": "up to 6 short concrete next steps in the caller's language"},
            "steps_en": {"type": "array", "items": {"type": "string"}, "description": "English translation of steps"},
            "inheritance": {
                "type": ["object", "null"],
                "description": "facts for HerEvo's inheritance calculator; never compute shares yourself",
                "properties": {
                    "deceased": {"type": "string", "enum": ["husband", "wife", "father", "mother", "other"]},
                    "wives": {"type": "integer", "description": "surviving wives, if a man died"},
                    "husband": {"type": "boolean", "description": "surviving husband, if a woman died"},
                    "sons": {"type": "integer"},
                    "daughters": {"type": "integer"},
                    "father_alive": {"type": "boolean", "description": "the deceased's father"},
                    "mother_alive": {"type": "boolean", "description": "the deceased's mother"},
                    "siblings": {"type": ["integer", "null"], "description": "the deceased's brothers and sisters"},
                    "land_amount": {"type": ["number", "null"]},
                    "land_unit": {"type": ["string", "null"], "description": "e.g. decimal, bigha, katha, acre"},
                    "complete": {"type": "boolean"},
                },
                "required": ["deceased", "complete"],
            },
            "document": {
                "type": ["object", "null"],
                "description": "a paper HerEvo writes for her, only when she agrees or asks",
                "properties": {
                    "type": {"type": "string", "enum": ["legal_aid_request", "maintenance_application",
                                                        "namjari_checklist", "child_marriage_report",
                                                        "income_statement"]},
                    "fields": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                        "required": ["name", "value"]}},
                },
                "required": ["type"],
            },
            "sale": {
                "type": ["object", "null"],
                "properties": {
                    "product": {"type": ["string", "null"]},
                    "quantity": {"type": ["number", "null"]},
                    "unit": {"type": ["string", "null"]},
                    "amount": {"type": "number"},
                    "currency": {"type": "string"},
                },
                "required": ["amount", "currency"],
            },
        },
        "required": ["intent", "confidence", "safety", "covered", "answer", "answer_en", "cited_ids"],
    },
}


log = logging.getLogger("herevo.llm")


class Sale(BaseModel):
    product: str | None = None
    quantity: float | None = None
    unit: str | None = None
    amount: float = Field(gt=0)
    currency: str


class InheritanceFacts(BaseModel):
    deceased: Literal["husband", "wife", "father", "mother", "other"]
    wives: int = Field(default=0, ge=0, le=4)
    husband: bool = False
    sons: int = Field(default=0, ge=0, le=30)
    daughters: int = Field(default=0, ge=0, le=30)
    father_alive: bool = False
    mother_alive: bool = False
    siblings: int | None = Field(default=None, ge=0, le=40)
    land_amount: float | None = Field(default=None, gt=0)
    land_unit: str | None = None
    complete: bool = False


class DocField(BaseModel):
    name: str
    value: str


DOC_TYPES = ("legal_aid_request", "maintenance_application", "namjari_checklist", "child_marriage_report",
             "income_statement")


class DocRequest(BaseModel):
    type: Literal[DOC_TYPES]  # type: ignore[valid-type]
    fields: list[DocField] = []


class Respond(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    safety: Literal["OK", "ESCALATE"]
    covered: bool
    answer: str = Field(min_length=1, max_length=2000)
    answer_en: str = Field(min_length=1, max_length=2000)
    cited_ids: list[str] = []
    sale: Sale | None = None
    for_helper: bool = False
    steps: list[str] = Field(default_factory=list, max_length=8)
    steps_en: list[str] = Field(default_factory=list, max_length=8)
    inheritance: InheritanceFacts | None = None
    document: DocRequest | None = None

    @field_validator("answer", "answer_en")
    @classmethod
    def strip(cls, v: str) -> str:
        return " ".join(v.split())

    @field_validator("steps", "steps_en")
    @classmethod
    def clean_steps(cls, v: list[str]) -> list[str]:
        return [" ".join(x.split()) for x in v if x and x.strip()][:6]


class LLM(Protocol):
    model: str

    def respond(self, language: str, passages: list[dict], message: str, history: list[dict] | None = None,
                note: str | None = None) -> dict: ...


def format_history(history: list[dict] | None) -> str:
    if not history:
        return ""
    lines = [f"Caller: {h['message']}\nHerEvo: {h['reply']}" for h in history]
    return "Earlier in this conversation (use it: do not ask again for facts she already gave):\n" + "\n".join(lines) + "\n\n"


def format_passages(passages: list[dict]) -> str:
    lines = []
    for p in passages:
        line = f"[{p['id']}] {p['title']} — {p['body']} (Source: {p['source_name']})"
        if p.get("local"):  # the checked translation: reuse its wording and terms when replying in that language
            line += f"\n    Same passage in the caller's language: {p['local']['title']} — {p['local']['text']}"
        lines.append(line)
    return "\n".join(lines) or "(no passages found)"


class ClaudeLLM:
    def __init__(self, api_key: str, model: str, max_tokens: int):
        import anthropic

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def respond(self, language: str, passages: list[dict], message: str, history: list[dict] | None = None,
                note: str | None = None) -> dict:
        system = SYSTEM_PROMPT.format(language_name=LANGUAGE_NAMES.get(language, "the caller's language"))
        user = f"{format_history(history)}{(note + chr(10) + chr(10)) if note else ''}Knowledge passages:\n{format_passages(passages)}\n\nCaller says now:\n{message}"
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            # The persona and tool are identical on every call: cache them.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[RESPOND_TOOL],
            tool_choice={"type": "tool", "name": "respond"},
            messages=[{"role": "user", "content": user}],
        )
        for block in msg.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "respond":
                return dict(block.input)
        raise ValueError("Claude did not call the respond tool")


def to_gemini_schema(schema: dict) -> dict:
    """Convert our JSON Schema to Gemini's OpenAPI-style schema (upper-case types, nullable flag)."""
    out: dict = {}
    t = schema.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        out["nullable"] = "null" in t
        t = non_null[0] if non_null else "string"
    if t:
        out["type"] = t.upper()
    for key in ("description", "enum", "required", "minimum", "maximum"):
        if key in schema:
            out[key] = schema[key]
    if "properties" in schema:
        out["properties"] = {k: to_gemini_schema(v) for k, v in schema["properties"].items()}
    if "items" in schema:
        out["items"] = to_gemini_schema(schema["items"])
    return out


class GeminiLLM:
    """Gemini free tier, for DATA_MODE=test with made-up messages only (see config.check_safe).
    Same prompt and same forced structured output as Claude, through Gemini function calling."""

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str, max_tokens: int, backups: tuple[str, ...] = ()):
        self.key = api_key.strip()  # a stray space or newline from copy-paste makes Google reject the key
        self.model = model
        self.models = [model] + [m for m in backups if m and m != model]
        self.max_tokens = max_tokens
        self.last_model = model

    def _post(self, httpx, model: str, body: dict, timeout: float = 20):
        url = self.URL.format(model=model)
        r = httpx.post(url, headers={"x-goog-api-key": self.key}, json=body, timeout=timeout)
        if r.status_code in (400, 401, 403):
            # Some key formats are only accepted as the ?key= query parameter: try that once.
            r2 = httpx.post(url, params={"key": self.key}, json=body, timeout=timeout)
            if r2.status_code == 200 or r.status_code != 400:
                return r2
        return r

    def respond(self, language: str, passages: list[dict], message: str, history: list[dict] | None = None,
                note: str | None = None) -> dict:
        import httpx

        system = SYSTEM_PROMPT.format(language_name=LANGUAGE_NAMES.get(language, "the caller's language"))
        user = f"{format_history(history)}{(note + chr(10) + chr(10)) if note else ''}Knowledge passages:\n{format_passages(passages)}\n\nCaller says now:\n{message}"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "tools": [{"functionDeclarations": [{"name": RESPOND_TOOL["name"],
                                                 "description": RESPOND_TOOL["description"],
                                                 "parameters": to_gemini_schema(RESPOND_TOOL["input_schema"])}]}],
            "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": ["respond"]}},
            # Newer Gemini models "think" before answering and those tokens count against this limit,
            # so leave generous room or the function call gets cut off.
            "generationConfig": {"maxOutputTokens": max(self.max_tokens, 8192), "temperature": 0.2},
        }
        data, notes = None, []
        deadline = time.monotonic() + 25  # she is waiting: give up and use the checked fallback after 25 s
        for model in self.models:
            for attempt in range(2):  # an overloaded model often answers on the second try
                left = deadline - time.monotonic()
                if left < 3:
                    break
                try:
                    r = self._post(httpx, model, body, timeout=min(20.0, left))
                except httpx.HTTPError as exc:  # network trouble: class name only, never the URL (it may hold the key)
                    notes.append(f"{model} network {type(exc).__name__}")
                    continue
                if r.status_code == 200:
                    data = r.json()
                    self.last_model = model
                    break
                try:
                    err = r.json().get("error", {})
                    status, detail = err.get("status") or "", err.get("message") or ""
                except ValueError:
                    status, detail = "", r.text[:200]
                notes.append(f"{model} HTTP {r.status_code} {status}".strip())
                log.warning("Gemini HTTP %s (%s): %s: %s", r.status_code, model, status, detail)
                if r.status_code in (401, 403) or (r.status_code == 400 and "API key" in detail):
                    # Bad key: every model will say the same, so stop here.
                    raise ValueError(f"Gemini HTTP {r.status_code} ({model}): {status}: {detail}")
                if r.status_code == 400 or r.status_code == 404:
                    break  # this model can't take the request (unknown or retired): try the next one
                time.sleep(min(1.5, max(0.0, deadline - time.monotonic())))  # 429 / 5xx: wait a moment, then retry or move on
            if data is not None:
                break
        if data is None:
            raise ValueError("Gemini failed: " + "; ".join(notes))
        data = r.json()
        cand = (data.get("candidates") or [{}])[0]
        for part in (cand.get("content") or {}).get("parts") or []:
            call = part.get("functionCall")
            if call and call.get("name") == "respond":
                return dict(call.get("args") or {})
        raise ValueError(f"Gemini returned no function call (finishReason={cand.get('finishReason')}, "
                         f"blocked={data.get('promptFeedback', {}).get('blockReason')})")


def parse(raw: dict, passage_ids: set[str]) -> Respond:
    """Validate the model's output. Citations to passages it was not given are dropped."""
    r = Respond.model_validate(raw)
    r.cited_ids = [c for c in r.cited_ids if c in passage_ids]
    if r.safety == "ESCALATE":
        r.intent = "SAFETY"
    return r


_llm: LLM | None = None


def get_llm() -> LLM | None:
    global _llm
    if _llm is None:
        s = get_settings()
        if s.llm_provider == "gemini" and s.gemini_api_key:
            _llm = GeminiLLM(s.gemini_api_key, s.gemini_model, s.claude_max_tokens,
                             tuple(m.strip() for m in s.gemini_backup_models.split(",")))
        elif s.llm_provider == "claude" and s.anthropic_api_key:
            _llm = ClaudeLLM(s.anthropic_api_key, s.claude_model, s.claude_max_tokens)
    return _llm


def set_llm(llm: LLM | None) -> None:  # used by tests
    global _llm
    _llm = llm


__all__ = ["Respond", "parse", "get_llm", "set_llm", "ValidationError", "LANGUAGE_NAMES"]
