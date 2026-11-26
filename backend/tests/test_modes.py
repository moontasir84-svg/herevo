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
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

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


def test_gemini_http_error_reports_reason(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp(
        {"error": {"status": "NOT_FOUND", "message": "models/x is not found"}}, status_code=404))
    with pytest.raises(ValueError, match="x HTTP 404 NOT_FOUND"):
        GeminiLLM("k", "x", 100).respond("bn", [], "x")


def test_gemini_switches_model_when_overloaded(monkeypatch):
    import app.llm as llm_mod
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    urls = []

    def fake_post(url, json=None, timeout=None, headers=None, params=None):
        urls.append(url.split("/models/")[1].split(":")[0])
        if "main" in url:
            return _Resp({"error": {"status": "UNAVAILABLE", "message": "The model is overloaded."}}, status_code=503)
        if "gone" in url:
            return _Resp({"error": {"status": "NOT_FOUND", "message": "retired"}}, status_code=404)
        return _Resp({"candidates": [{"content": {"parts": [{"functionCall": {"name": "respond", "args": {"ok": 2}}}]}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    g = GeminiLLM("k", "main", 100, ("gone", "backup"))
    assert g.respond("bn", [], "x") == {"ok": 2} and g.last_model == "backup"
    assert urls == ["main", "main", "gone", "backup"]


def test_gemini_bad_key_stops_without_trying_other_models(monkeypatch):
    urls = []

    def fake_post(url, **k):
        urls.append(url)
        return _Resp({"error": {"status": "UNAUTHENTICATED", "message": "API key not valid"}}, status_code=401)

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(ValueError, match="401"):
        GeminiLLM("k", "main", 100, ("backup",)).respond("bn", [], "x")
    assert all("main" in u for u in urls)


def test_gemini_cut_off_response_is_explained(monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp({"candidates": [{"finishReason": "MAX_TOKENS", "content": {}}]}))
    with pytest.raises(ValueError, match="MAX_TOKENS"):
        GeminiLLM("k", "m", 100).respond("bn", [], "x")


def test_gemini_retries_key_as_query_param(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None, params=None):
        calls.append("header" if headers else "param")
        if headers:
            return _Resp({"error": {"status": "UNAUTHENTICATED", "message": "API key not valid"}}, status_code=401)
        return _Resp({"candidates": [{"content": {"parts": [{"functionCall": {"name": "respond", "args": {"ok": 1}}}]}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    assert GeminiLLM(" AQ.key \n", "m", 100).respond("bn", [], "x") == {"ok": 1}
    assert calls == ["header", "param"]


def test_read_aloud_uses_gemini_tts_and_returns_wav(monkeypatch):
    import base64

    from fastapi.testclient import TestClient

    import app.main as m
    from app import speech
    calls = []

    class R:
        def __init__(self, code, body): self.status_code, self._b, self.text = code, body, str(body)[:50]
        def json(self): return self._b

    monkeypatch.setattr(httpx, "get", lambda *a, **k: R(200, {"models": [{"name": "models/gemini-x-flash-preview-tts"}]}))

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(url)
        return R(200, {"candidates": [{"content": {"parts": [{"inlineData": {
            "mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(b"\x00\x01" * 2400).decode()}}]}}]})

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(speech, "_speech", speech.GeminiSpeech("k", "m"))
    monkeypatch.setattr(speech, "_checked", True)
    m._tts_cache.clear()
    r = TestClient(m.app).post("/v1/tts", json={"text": "আপনি একা নন।", "lang": "bn", "session_id": "s-tts-0001"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.content[:4] == b"RIFF"
    assert "gemini-x-flash-preview-tts" in calls[0]
    TestClient(m.app).post("/v1/tts", json={"text": "আপনি একা নন।", "lang": "bn", "session_id": "s-tts-0001"})
    assert len(calls) == 1  # the same text is served from the cache
