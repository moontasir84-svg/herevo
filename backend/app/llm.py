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
problem: farming, money and work, rights, health or safety.

Rules:
1. Reply in {language_name}, even if she wrote in another language, in 2 or 3 short, warm, spoken sentences. No lists, no \
symbols, no markdown.
2. Use ONLY the numbered knowledge passages you are given. Never add facts from elsewhere. If the passages \
do not answer the question, set covered=false and say so kindly, then give a free help contact from the passages.
3. Never give legal advice: give general information about what the law says, then a free legal aid contact.
4. Never give fertilizer, pesticide or medicine doses, and never diagnose. For health, name the danger \
signs from the passages and tell her where to go.
5. If the message mentions violence, threats, eviction, being forced, or fear for her safety, set \
safety=ESCALATE and intent=SAFETY, and write only a short line of comfort: a trained human will take over.
6. If she reports a sale or income, set intent=EARN and fill `sale`; never guess a missing amount. Begin the \
answer by confirming what was recorded (the product and the amount, in her currency), so she knows it was saved.
7. Always fill answer_en with an English translation of your answer, for audit.
8. List in cited_ids the ids of the passages your answer relies on.
9. Use intent=RIGHTS only when she asks about a legal right (land, inheritance, dower, maintenance, divorce, \
wages, child support and the like). A greeting or small talk is intent=ADVICE with covered=false and no \
cited_ids: welcome her in one sentence and ask what she needs."""

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
            "answer": {"type": "string", "description": "2-3 spoken sentences in the caller's language"},
            "answer_en": {"type": "string", "description": "English translation of answer"},
            "cited_ids": {"type": "array", "items": {"type": "string"}},
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


class Respond(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    safety: Literal["OK", "ESCALATE"]
    covered: bool
    answer: str = Field(min_length=1, max_length=1200)
    answer_en: str = Field(min_length=1, max_length=1200)
    cited_ids: list[str] = []
    sale: Sale | None = None

    @field_validator("answer", "answer_en")
    @classmethod
    def strip(cls, v: str) -> str:
        return " ".join(v.split())


class LLM(Protocol):
    model: str

    def respond(self, language: str, passages: list[dict], message: str) -> dict: ...


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

    def respond(self, language: str, passages: list[dict], message: str) -> dict:
        system = SYSTEM_PROMPT.format(language_name=LANGUAGE_NAMES.get(language, "the caller's language"))
        user = f"Knowledge passages:\n{format_passages(passages)}\n\nCaller says:\n{message}"
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

    def respond(self, language: str, passages: list[dict], message: str) -> dict:
        import httpx

        system = SYSTEM_PROMPT.format(language_name=LANGUAGE_NAMES.get(language, "the caller's language"))
        user = f"Knowledge passages:\n{format_passages(passages)}\n\nCaller says:\n{message}"
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
