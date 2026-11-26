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


def test_message_text_is_deleted_after_30_days():
    from app import db
    from app.main import purge_once
    with db.pool().connection() as conn:
        conn.execute("INSERT INTO turns (user_id, channel, language, intent, safety, answered_by, message, created_at) "
                     "VALUES (NULL, 'purge-old', 'bn', 'ADVICE', 'OK', 'fallback', 'very old message', now() - interval '31 days')")
        conn.execute("INSERT INTO turns (user_id, channel, language, intent, safety, answered_by, message) "
                     "VALUES (NULL, 'purge-new', 'bn', 'ADVICE', 'OK', 'fallback', 'recent message')")
    assert purge_once() >= 1
    with db.pool().connection() as conn:
        rows = {r["channel"]: r["message"] for r in conn.execute(
            "SELECT channel, message FROM turns WHERE channel IN ('purge-old', 'purge-new')").fetchall()}
    assert rows == {"purge-old": None, "purge-new": "recent message"}


def test_bot_ignores_groups_other_than_the_helpers(fake_llm, recorder):
    client.post("/telegram/webhook/s3cret", json={"update_id": 9, "message": {
        "message_id": 9, "chat": {"id": -42, "type": "group", "title": "Family"}, "text": "/start@HerEvo_bot"}})
    assert recorder.sent == [] and db.get_user(db.user_id_for("telegram", "-42")) is None


def test_helper_reply_works_with_the_bot_name_in_the_command(fake_llm, recorder):
    tg(223, "স্বামী আমাকে মারধর করে")
    case = int([t for w, t in recorder.sent if w == "helper"][-1].split("#")[1].split()[0])
    client.post("/telegram/webhook/s3cret", json={"update_id": 10, "message": {
        "message_id": 10, "chat": {"id": int(HELPER), "type": "group"}, "text": f"/reply@HerEvo_bot {case} আমি আছি"}})
    assert ("223", "HerEvo সহায়তাকর্মী / helper: আমি আছি") in recorder.sent


def test_alert_follows_the_group_when_telegram_upgrades_it(monkeypatch):
    import httpx
    from app.alerts import TelegramNotifier
    sent_to = []

    class R:
        def __init__(self, code, body): self.status_code, self._b, self.text = code, body, str(body)
        def json(self): return self._b

    def fake_post(url, json=None, timeout=None):
        sent_to.append(json["chat_id"])
        if json["chat_id"] == "-5378753373":
            return R(400, {"ok": False, "description": "group chat was upgraded to a supergroup chat",
                           "parameters": {"migrate_to_chat_id": -1009876543210}})
        return R(200, {"ok": True})

    monkeypatch.setattr(httpx, "post", fake_post)
    n = TelegramNotifier("t", "-5378753373")
    assert n.notify_helper("case #1") is True and n.chat_id == "-1009876543210"
    assert sent_to == ["-5378753373", "-1009876543210"]


def test_consent_in_her_language_with_short_reminders(fake_llm, recorder):
    chat = 444
    tg(chat, "/start")
    assert "আসসালামু আলাইকুম" in recorder.sent[-1][1]          # first welcome: the pack's own language
    tg(chat, "hi")
    assert recorder.sent[-1][1] == "To continue, type 'yes', then ask your question."  # short, in English
    tg(chat, "can I talk in English")
    assert "Hello, I am HerEvo" in recorder.sent[-1][1] and "in Bangla or English" in recorder.sent[-1][1]
    tg(chat, "Yes.")
    assert recorder.sent[-1][1] == "Thank you. Now type or say your question."
    tg(chat, "How do I save money?")
    assert fake_llm.calls[-1]["language"] == "en"


def test_new_user_writing_in_english_gets_the_english_welcome(fake_llm, recorder):
    tg(445, "hello")
    assert "Hello, I am HerEvo" in recorder.sent[-1][1] and "⚠️ Test version" in recorder.sent[-1][1]
    tg(445, "হ্যাঁ")
    assert recorder.sent[-1][1] == "ধন্যবাদ। এখন আপনার প্রশ্ন লিখুন বা বলুন।"


def test_unknown_command_in_her_chat_shows_her_commands_not_the_ai(fake_llm, recorder):
    tg(446, "yes")
    tg(446, "/reply 3 hello")
    assert recorder.sent[-1][1].startswith("যা লিখতে পারেন / What you can type") and fake_llm.calls == []


def test_helper_reply_formats_and_website_cases(fake_llm, recorder):
    tg(447, "স্বামী আমাকে মারধর করে")
    case = int([t for w, t in recorder.sent if w == "helper"][-1].split("#")[1].split()[0])
    tg(int(HELPER), f"/reply case {case} আমি আছি")
    assert ("447", "HerEvo সহায়তাকর্মী / helper: আমি আছি") in recorder.sent
    tg(int(HELPER), f"/reply #{case} আবার লিখছি")
    assert ("447", "HerEvo সহায়তাকর্মী / helper: আবার লিখছি") in recorder.sent
    web = client.post("/v1/ask", json={"session_id": "s-web-0001", "text": "My husband is beating me"}).json()
    alert = [t for w, t in recorder.sent if w == "helper"][-1]
    assert "on the website" in alert and f"/reply {web['escalation_id']}" in alert
    assert client.get("/v1/inbox/s-web-0001").json() == {"messages": []}
    tg(int(HELPER), f"/reply {web['escalation_id']} I am here. Are you safe now?")
    assert recorder.sent[-1][1].startswith(f"Sent to case {web['escalation_id']} (website chat")
    got = client.get("/v1/inbox/s-web-0001").json()["messages"]
    assert [m["text"] for m in got] == ["I am here. Are you safe now?"] and got[0]["case"] == web["escalation_id"]
    assert client.get("/v1/inbox/s-web-0001").json() == {"messages": []}  # delivered once
    assert client.get("/v1/inbox/someone-else").json() == {"messages": []}


def test_telegram_sends_steps_and_the_paper(fake_llm, recorder):
    tg(448, "হ্যাঁ")
    fake_llm.next = {"intent": "RIGHTS", "confidence": 0.9, "safety": "OK", "covered": True,
                     "answer": "আবেদনটি লিখে দিলাম।", "answer_en": "Here is the application.", "cited_ids": [],
                     "steps": ["আবেদনটি চেয়ারম্যানকে দিন", "কপি রেখে দিন"],
                     "document": {"type": "legal_aid_request", "fields": [{"name": "issue", "value": "দেনমোহর পাইনি"}]}}
    tg(448, "দেনমোহর পাইনি, লিগ্যাল এইডের আবেদন লিখে দিন")
    texts = [t for who, t in recorder.sent if who == "448"]
    assert "১. আবেদনটি চেয়ারম্যানকে দিন\n২. কপি রেখে দিন" in texts[-2]
    assert texts[-1].startswith("📄 বিনামূল্যে আইনি সহায়তার আবেদন") and "দেনমোহর পাইনি" in texts[-1]


def test_during_an_open_case_her_messages_go_to_the_helper_not_the_ai(fake_llm, recorder):
    first = client.post("/v1/ask", json={"session_id": "s-web-relay", "text": "My husband is beating me"}).json()
    case = first["escalation_id"]
    r = client.post("/v1/ask", json={"session_id": "s-web-relay", "text": "He locked the door. I am at my sister's now."}).json()
    assert r["answered_by"] == "helper_relay" and r["escalation_id"] == case and fake_llm.calls == []
    relay = [t for w, t in recorder.sent if w == "helper"][-1]
    assert f"Case #{case} · she says:" in relay and "I am at my sister's now." in relay and f"/close {case}" in relay
    tg(int(HELPER), f"/close {case}")
    assert recorder.sent[-1][1].startswith(f"Case {case} closed")
    r = client.post("/v1/ask", json={"session_id": "s-web-relay", "text": "How do I save money?"}).json()
    assert r["answered_by"] != "helper_relay"


def _swipe(text, replied_to):
    return client.post("/telegram/webhook/s3cret", json={"update_id": 20, "message": {
        "message_id": 20, "chat": {"id": int(HELPER), "type": "group"}, "text": text,
        "reply_to_message": {"message_id": 5, "from": {"id": 1, "is_bot": True}, "text": replied_to}}})


def test_helper_swipes_to_reply_on_the_alert_and_on_her_relayed_message(fake_llm, recorder):
    first = client.post("/v1/ask", json={"session_id": "s-web-swipe", "text": "My in laws beat me"}).json()
    case = first["escalation_id"]
    alert = [t for w, t in recorder.sent if w == "helper"][-1]
    assert "Swipe to reply" in alert
    _swipe("Are you safe right now?", alert)                       # reply on the alert, no command
    assert recorder.sent[-1][1].startswith(f"Sent to case {case}")
    client.post("/v1/ask", json={"session_id": "s-web-swipe", "text": "Yes, I am at my sister's house."})
    relayed = [t for w, t in recorder.sent if w == "helper"][-1]
    _swipe("Good. Stay there tonight.", relayed)                  # reply on her follow-up
    got = client.get("/v1/inbox/s-web-swipe").json()["messages"]
    assert [(m["text"], m["quote"]) for m in got] == [
        ("Are you safe right now?", "My in laws beat me"),
        ("Good. Stay there tonight.", "Yes, I am at my sister's house.")]


def test_swipe_reply_reaches_a_telegram_user_with_the_quote(fake_llm, recorder):
    tg(449, "স্বামী আমাকে মারধর করে")
    alert = [t for w, t in recorder.sent if w == "helper"][-1]
    _swipe("আপা, আপনি কি এখন নিরাপদ?", alert)
    assert ("449", "↩ “স্বামী আমাকে মারধর করে”\nHerEvo সহায়তাকর্মী / helper: আপা, আপনি কি এখন নিরাপদ?") in recorder.sent


def test_plain_message_in_the_group_gets_help_not_an_error(fake_llm, recorder):
    _swipe("ok", "Sent to case 3.")  # a reply on a bot message without a case number
    assert recorder.sent[-1][1].startswith("Swipe to reply on her message")
