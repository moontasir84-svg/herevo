"""Free test mode vs live mode, and the guard that keeps real women's data away from free tiers."""
import httpx
import pytest

from app import speech
from app.config import Settings, UnsafeConfiguration, check_safe
from app.llm import GeminiLLM, parse


def test_live_mode_refuses_free_tier_llm():
    with pytest.raises(UnsafeConfiguration):
        check_safe(Settings(data_mode="live", llm_provider="gemini"))


def test_live_mode_refuses_free_tier_speech():
    with pytest.raises(UnsafeConfiguration):
        check_safe(Settings(data_mode="live", llm_provider="claude", speech_provider="gemini"))


def test_allowed_combinations():
    check_safe(Settings(data_mode="test", llm_provider="gemini", speech_provider="gemini"))
    check_safe(Settings(data_mode="live", llm_provider="claude", speech_provider="google"))
    with pytest.raises(UnsafeConfiguration):
        check_safe(Settings(data_mode="production"))


class _Resp:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_gemini_forced_function_call(monkeypatch):
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, body=json)
        return _Resp({"candidates": [{"content": {"parts": [{"functionCall": {"name": "respond", "args": {
            "intent": "RIGHTS", "confidence": 0.8, "safety": "OK", "covered": True,
            "answer": "সন্তান থাকলে স্ত্রী আট ভাগের এক ভাগ পান।", "answer_en": "One-eighth with children.",
            "cited_ids": ["bd-rights-inheritance-widow#0"]}}}]}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    raw = GeminiLLM("k", "gemini-flash-latest", 700).respond("bn", [{"id": "bd-rights-inheritance-widow#0",
                                                                     "title": "t", "body": "b", "source_name": "s"}], "জমি")
    r = parse(raw, {"bd-rights-inheritance-widow#0"})
    assert r.intent == "RIGHTS" and r.cited_ids == ["bd-rights-inheritance-widow#0"]
    assert sent["headers"]["x-goog-api-key"] == "k" and "gemini-flash-latest" in sent["url"]
    assert sent["body"]["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
    params = sent["body"]["tools"][0]["functionDeclarations"][0]["parameters"]
    assert params["type"] == "OBJECT" and params["properties"]["sale"]["nullable"] is True


def test_gemini_without_function_call_is_rejected(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp({"candidates": [{"content": {"parts": [{"text": "hi"}]}}]}))
    with pytest.raises(ValueError):
        GeminiLLM("k", "m", 100).respond("bn", [], "x")


def test_gemini_transcription(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(
        {"candidates": [{"content": {"parts": [{"text": " ধানের পাতা হলুদ "}]}}]}))
    g = speech.GeminiSpeech("k", "m")
    assert g.transcribe(b"OggS", "bn") == "ধানের পাতা হলুদ"
    assert g.synthesize("x", "bn") is None


def test_test_mode_is_visible_to_users_and_helpers(fake_llm, recorder):
    from fastapi.testclient import TestClient

    from app.main import app
    c = TestClient(app)
    assert c.get("/health").json()["data_mode"] == "test"
    c.post("/telegram/webhook/s3cret", json={"message": {"chat": {"id": 888}, "text": "/start"}})
    assert recorder.sent[-1][1].startswith("⚠️ পরীক্ষামূলক সংস্করণ")
    c.post("/telegram/webhook/s3cret", json={"message": {"chat": {"id": 888}, "text": "স্বামী আমাকে মারধর করে"}})
    assert any(who == "helper" and text.startswith("[TEST MODE") for who, text in recorder.sent)
