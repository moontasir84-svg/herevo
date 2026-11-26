from fastapi.testclient import TestClient

from app import db
from app.main import app

client = TestClient(app)
HELPER = "999000"


def tg(chat_id, text):
    return client.post("/telegram/webhook/s3cret", json={"update_id": 1, "message": {
        "message_id": 1, "chat": {"id": chat_id, "type": "private"}, "text": text}})


def test_health():
    r = client.get("/health").json()
    assert r["ok"] and "BD" in r["countries"]


def test_ask_endpoint_and_ledger(fake_llm, recorder):
    r = client.post("/v1/ask", json={"session_id": "web-session-1", "text": "আজ ৪০ কেজি ধান বিক্রি করেছি, ১২০০ টাকা।"})
    assert r.status_code == 200 and r.json()["intent"] == "EARN"
    led = client.get("/v1/ledger/web-session-1").json()
    assert led["total"] == 1200


def test_ask_unknown_country(fake_llm):
    assert client.post("/v1/ask", json={"session_id": "web-session-1", "text": "hi", "country": "ZZ"}).status_code == 404


def test_evidence_hides_small_cells(fake_llm, recorder):
    client.post("/v1/ask", json={"session_id": "web-session-2", "text": "জমি নিয়ে নিচ্ছে দেবর"})
    assert client.get("/v1/evidence").json()["cells"]
    assert client.get("/v1/evidence?min_count=5").json()["cells"] == []


def test_rate_limit(fake_llm, recorder):
    codes = [client.post("/v1/ask", json={"session_id": "spammer-123", "text": "ধান"}).status_code for _ in range(22)]
    assert codes.count(429) == 2


def test_telegram_bad_secret():
    assert client.post("/telegram/webhook/wrong", json={}).status_code == 403


def test_telegram_consent_then_question(fake_llm, recorder):
    tg(111, "/start")
    assert "HerEvo" in recorder.sent[-1][1]
    tg(111, "জমি নিয়ে প্রশ্ন")            # not consented yet -> welcome again, no AI call
    assert fake_llm.calls == []
    tg(111, "হ্যাঁ")
    tg(111, "স্বামী মারা যাওয়ার পর দেবর বলছে জমি আমার না।")
    assert recorder.sent[-1] == ("111", "সন্তান থাকলে স্ত্রী আট ভাগের এক ভাগ পান।")


def test_telegram_safety_works_before_consent_and_helper_can_reply(fake_llm, recorder):
    tg(222, "স্বামী আমাকে মারধর করে, আমি ভয় পাচ্ছি")
    helper_alert = [t for who, t in recorder.sent if who == "helper"]
    assert helper_alert and "safety case #1" in helper_alert[0]
    assert "১০৯" in recorder.sent[-1][1]
    # the helper replies through the bot, never seeing her chat id
    tg(int(HELPER), "/reply 1 আমি রুমা, আপনার সাথে আছি। আপনি কি এখন নিরাপদ?")
    assert ("222", "HerEvo সহায়তাকর্মী / helper: আমি রুমা, আপনার সাথে আছি। আপনি কি এখন নিরাপদ?") in recorder.sent
    assert db.escalation(1)["status"] == "contacted"


def test_telegram_myrecord_and_delete(fake_llm, recorder):
    tg(333, "হ্যাঁ")
    tg(333, "আজ ৪০ কেজি ধান বিক্রি করেছি, ১২০০ টাকা।")
    tg(333, "/myrecord")
    assert "1,200 BDT" in recorder.sent[-1][1]
    tg(333, "/delete")
    assert db.get_user(db.user_id_for("telegram", "333")) is None


def tg_voice(chat_id, duration=8):
    return client.post("/telegram/webhook/s3cret", json={"update_id": 2, "message": {
        "message_id": 2, "chat": {"id": chat_id, "type": "private"},
        "voice": {"file_id": "VOICE123", "duration": duration, "mime_type": "audio/ogg"}}})


def test_voice_note_is_transcribed_answered_and_spoken_back(fake_llm, recorder, fake_speech):
    tg(444, "হ্যাঁ")
    tg_voice(444)
    assert fake_speech.heard == [b"OggS-fake-audio-VOICE123"]          # audio fetched from Telegram
    assert fake_llm.calls[-1]["message"].startswith("স্বামী মারা")     # transcript went through the pipeline
    assert ("444", "সন্তান থাকলে স্ত্রী আট ভাগের এক ভাগ পান।") in recorder.sent
    assert recorder.sent[-1] == ("444", ("VOICE", b"OggS-tts-bn"))     # answered with a Bangla voice note


def test_voice_threat_escalates_even_before_consent(fake_llm, recorder, fake_speech):
    fake_speech.transcript = "স্বামী আমাকে মারধর করে"
    tg_voice(555)
    assert any(who == "helper" for who, _ in recorder.sent) and fake_llm.calls == []


def test_voice_without_speech_service(fake_llm, recorder, no_speech):
    tg(666, "হ্যাঁ")
    tg_voice(666)
    assert "লিখে পাঠান" in recorder.sent[-1][1] and fake_llm.calls == []


def test_voice_too_long(fake_llm, recorder, fake_speech):
    tg_voice(777, duration=300)
    assert "১ মিনিটের" in recorder.sent[-1][1] and fake_speech.heard == []


def test_web_app_is_served_and_api_still_wins():
    r = client.get("/")
    assert r.status_code == 200 and "HerEvo" in r.text and "askApi" in r.text
    assert client.get("/health").json()["ok"]


def test_ask_returns_evidence_gap(fake_llm, recorder):
    r = client.post("/v1/ask", json={"session_id": "web-session-9", "text": "স্বামী মারা যাওয়ার পর দেবর বলছে জমি আমার না।"}).json()
    assert r["gap"] == "land_inheritance"


def test_offline_pack_download():
    r = client.get("/v1/packs/bd")
    assert r.status_code == 200 and "max-age" in r.headers["cache-control"]
    p = r.json()
    assert p["country"] == "BD" and p["review_status"] == "draft"
    assert any(e["id"] == "bd-farm-rice-yellowing" for e in p["entries"])
    assert any(c["phone"] == "109" for c in p["contacts"])
    assert "হুমকি" in p["safety_terms"] and "escalate" in p["messages"]
    assert client.get("/v1/packs/zz").status_code == 404


def test_ai_health_check_reports_failure_without_the_key(fake_llm):
    import app.main as m
    m._ai_check.update(at=0.0, result=None)
    fake_llm.next = ValueError("Gemini failed: gemini-flash-latest HTTP 429 RESOURCE_EXHAUSTED")
    r = client.get("/health/ai").json()
    assert r["ok"] is False and "quota" in r["verdict"] and "429" in r["error"]
    assert client.get("/health/ai").json()["cached"] is True
    m._ai_check.update(at=0.0, result=None)
    fake_llm.next = {"intent": "ADVICE"}
    r = client.get("/health/ai").json()
    assert r["ok"] is True and r["verdict"].startswith("The AI works")
    m._ai_check.update(at=0.0, result=None)


def test_page_and_offline_helper_are_never_served_stale():
    for path in ("/", "/sw.js", "/health"):
        assert client.get(path).headers.get("cache-control") == "no-cache"
