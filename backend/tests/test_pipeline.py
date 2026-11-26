import pytest
from app import db
from app.packs import load_pack
from app.pipeline import detect_language, handle
from app.retrieval import retrieve


def _uid(name="rina"):
    uid = db.user_id_for("test", name)
    db.upsert_user(uid, "test", "BD", "bn")
    return uid


def test_pack_is_valid_and_ingested():
    pack = load_pack("BD")
    assert pack["country"] == "BD"
    with db.pool().connection() as conn:
        n = conn.execute("SELECT count(*) AS n FROM pack_chunks WHERE country='BD'").fetchone()["n"]
    assert n == len(pack["entries"])
    assert any(c["phone"] == "109" for c in db.contacts("BD"))


def test_retrieval_finds_widow_inheritance_in_bangla():
    hits = retrieve("BD", "স্বামী মারা যাওয়ার পর দেবর বলছে জমি আমার না")
    assert hits[0]["id"].startswith("bd-rights-inheritance")


def test_retrieval_finds_rice_advice():
    hits = retrieve("BD", "আমার ধানের পাতা হলুদ হয়ে যাচ্ছে")
    assert hits[0]["id"] == "bd-farm-rice-yellowing#0"


def test_language_detection():
    assert detect_language("জমি আমার না") == "bn"
    assert detect_language("Mwajiri wangu hajanilipa mshahara") == "sw"
    assert detect_language("¿Cómo consigo un préstamo?") == "es"
    assert detect_language("How do I save money?") == "en"


@pytest.mark.parametrize("text,lang", [
    ("Wikendi niliuza maandazi 40 kwa shilingi 800.", "sw"),
    ("Mwajiri wangu hajanilipa mshahara kwa miezi miwili.", "sw"),
    ("Nawezaje kuweka akiba kwa usalama?", "sw"),
    ("Mwajiri wangu ananipiga na kunitishia.", "sw"),
    ("Mi expareja no paga la pensión de mis hijos.", "es"),
    ("Hoy vendí 1,200 pesos de tlayudas en el mercado.", "es"),
    ("¿Cómo consigo un préstamo para mi puesto?", "es"),
    ("Mi expareja me amenaza cuando voy al mercado.", "es"),
    ("At the weekend I sold 40 mandazi for 800 shillings.", "en"),
    ("My employer hits me and threatens me", "en"),
    ("I sold vegetables for 800 taka today", "en"),
    ("Where can I get free legal aid?", "en"),
    ("Hi", "en"),
    ("Hola", "es"),
    ("Habari", "sw"),
])
def test_detects_the_language_of_every_demo_message(text, lang):
    assert detect_language(text) == lang


def test_safety_escalates_before_any_ai_call(fake_llm, recorder):
    uid = _uid()
    r = handle(uid=uid, channel="test", text="দেবর হুমকি দিচ্ছে আমাকে বাড়ি থেকে বের করে দেবে।", district="Rangpur")
    assert r.safety == "ESCALATE" and r.intent == "SAFETY" and r.answered_by == "safety_rule"
    assert "১০৯" in r.reply and "999" in r.reply_en
    assert fake_llm.calls == []                      # Claude never saw it
    assert recorder.sent and "safety case #1" in recorder.sent[0][1] and "Rangpur" in recorder.sent[0][1]
    assert db.escalation(r.escalation_id)["status"] == "open"
    assert db.evidence() == [{"country": "BD", "gap": "safety_threats", "n": 1}]


def test_grounded_answer_drops_invented_citations_and_logs_case(fake_llm, recorder):
    uid = _uid()
    r = handle(uid=uid, channel="test", text="স্বামী মারা যাওয়ার পর দেবর বলছে জমি আমার না।")
    assert r.answered_by == "claude" and r.intent == "RIGHTS" and r.safety == "OK"
    assert [c.id for c in r.citations] and "made-up-id" not in [c.id for c in r.citations]
    assert r.citations[0].review_status == "draft"
    assert db.evidence() == [{"country": "BD", "gap": "land_inheritance", "n": 1}]
    assert recorder.sent == []


def test_model_can_escalate_too(fake_llm, recorder):
    fake_llm.next = {"intent": "ADVICE", "confidence": 0.6, "safety": "ESCALATE", "covered": False,
                     "answer": "…", "answer_en": "…", "cited_ids": []}
    r = handle(uid=_uid(), channel="test", text="আমি আর পারছি না, কেউ সাহায্য করুন")
    assert r.safety == "ESCALATE" and r.escalation_id == 1 and recorder.sent


def test_invalid_model_output_falls_back(fake_llm, recorder):
    fake_llm.next = {"intent": "SOMETHING", "answer": ""}
    r = handle(uid=_uid(), channel="test", text="জমির নামজারি কীভাবে করব?")
    assert r.answered_by == "fallback" and r.intent == "RIGHTS"
    assert "১৬৪৩০" in r.reply


def test_model_error_falls_back(fake_llm, recorder):
    fake_llm.next = RuntimeError("API down")
    r = handle(uid=_uid(), channel="test", text="সঞ্চয় কীভাবে করব?")
    assert r.answered_by == "fallback" and r.fallback_reason == "the AI call failed"


def test_fallback_reason_names_a_rejected_key_without_leaking_it(fake_llm, recorder):
    fake_llm.next = ValueError("Gemini HTTP 400 (gemini-flash-latest): INVALID_ARGUMENT: API key not valid")
    r = handle(uid=_uid(), channel="test", text="সঞ্চয় কীভাবে করব?")
    assert r.fallback_reason == "the AI provider rejected the API key"
    fake_llm.next = ValueError("Gemini HTTP 429 (gemini-flash-latest): RESOURCE_EXHAUSTED: quota")
    assert handle(uid=_uid(), channel="test", text="সঞ্চয় কীভাবে করব?").fallback_reason.startswith("the free AI limit")
    fake_llm.next = ValueError("Gemini failed: gemini-flash-latest HTTP 503 UNAVAILABLE; gemini-2.5-flash network ConnectTimeout")
    assert handle(uid=_uid(), channel="test", text="সঞ্চয় কীভাবে করব?").fallback_reason == (
        "the AI provider did not answer (gemini-flash-latest: HTTP 503 UNAVAILABLE; gemini-2.5-flash: network ConnectTimeout)")


def test_sale_is_recorded(fake_llm, recorder):
    uid = _uid()
    r = handle(uid=uid, channel="test", text="আজ ৪০ কেজি ধান বিক্রি করেছি, ১২০০ টাকা।")
    assert r.intent == "EARN" and r.sale["amount"] == 1200
    rows = db.ledger(uid)
    assert len(rows) == 1 and float(rows[0]["amount"]) == 1200 and rows[0]["product"] == "rice"


def test_sale_fallback_without_claude(recorder):
    uid = _uid()
    r = handle(uid=uid, channel="test", text="আজ ধান বিক্রি করেছি ১২০০ টাকা")
    assert r.answered_by == "fallback" and r.intent == "EARN"
    assert r.fallback_reason == "no AI key is set on the server"
    assert float(db.ledger(uid)[0]["amount"]) == 1200
    assert "১,২০০" in r.reply and "1,200" in r.reply_en


def test_every_turn_is_audited(fake_llm, recorder):
    uid = _uid()
    handle(uid=uid, channel="test", text="ধানের পাতা হলুদ")
    with db.pool().connection() as conn:
        t = conn.execute("SELECT * FROM turns").fetchone()
    assert t["answered_by"] == "claude" and t["model"] == "fake-claude" and t["latency_ms"] >= 0


def test_delete_user_removes_her_data(fake_llm, recorder):
    uid = _uid()
    handle(uid=uid, channel="test", text="আজ ৪০ কেজি ধান বিক্রি করেছি, ১২০০ টাকা।")
    db.delete_user(uid)
    assert db.ledger(uid) == [] and db.get_user(uid) is None


def test_all_three_countries_ingested():
    from app.packs import available_countries
    assert available_countries() == ["BD", "KE", "MX"]


def test_kenya_and_mexico_retrieval():
    assert retrieve("KE", "Mwajiri wangu hajanilipa mshahara kwa miezi miwili")[0]["id"] == "ke-rights-wages-domestic#0"
    assert retrieve("MX", "Mi expareja no paga la pensión de mis hijos")[0]["id"] == "mx-rights-child-support#0"


def test_kenya_escalation_message_in_swahili(fake_llm, recorder):
    uid = db.user_id_for("test", "amina")
    db.upsert_user(uid, "test", "KE", "sw")
    r = handle(uid=uid, channel="test", text="Mwajiri wangu ananipiga na kunitishia.", country="KE")
    assert r.safety == "ESCALATE" and "1195" in r.reply and r.reply.startswith("Hauko peke yako")


def test_fallback_rights_case_uses_top_passage_category(recorder):
    handle(uid=_uid(), channel="test", text="স্বামী মারা যাওয়ার পর দেবর বলছে জমি আমার না।")
    assert db.evidence() == [{"country": "BD", "gap": "land_inheritance", "n": 1}]


def test_greeting_gets_a_hello_not_a_legal_aid_number(recorder):
    r = handle(uid=_uid(), channel="test", text="Hi")
    assert r.answered_by == "fallback" and r.reply.startswith("Hello! I am HerEvo") and "16430" not in r.reply
    assert handle(uid=_uid(), channel="test", text="আসসালামু আলাইকুম").reply.startswith("আসসালামু আলাইকুম! আমি HerEvo")
    assert handle(uid=_uid(), channel="test", text="Hola", country="MX").reply.startswith("¡Hola! Soy HerEvo")


def test_fallback_gives_closest_checked_passage_with_its_source(recorder):
    r = handle(uid=_uid(), channel="test", text="স্বামী মারা যাওয়ার পর দেবর বলছে জমি আমার না।")
    assert r.answered_by == "fallback" and r.intent == "RIGHTS"
    assert r.reply.startswith("এআই এখন পাওয়া যাচ্ছে না") and "১৬৪৩০" in r.reply
    assert r.citations and r.citations[0].id.startswith("bd-rights") and r.citations[0].review_status == "draft"
    assert r.gap == "land_inheritance"


def test_fallback_without_a_keyword_match_admits_it(recorder):
    r = handle(uid=_uid(), channel="test", text="zzqx blorf")
    assert r.citations == [] and r.reply.startswith("I don't have a reliable answer")


def test_greeting_labelled_rights_by_the_ai_is_not_counted_as_a_case(fake_llm, recorder):
    fake_llm.next = {"intent": "RIGHTS", "confidence": 1.0, "safety": "OK", "covered": False,
                     "answer": "Hello sister, I am HerEvo.", "answer_en": "Hello sister, I am HerEvo.", "cited_ids": []}
    r = handle(uid=_uid(), channel="test", text="Hi")
    assert r.intent == "ADVICE" and r.gap is None and r.language == "en"
    assert db.evidence(min_count=1) == []


def test_asking_for_bangla_switches_and_remembers_the_reply_language(fake_llm, recorder):
    from app.pipeline import requested_language
    assert requested_language("Reply me in Bangla") == "bn"
    assert requested_language("বাংলায় বলুন") == "bn"
    assert requested_language("en español por favor") == "es"
    assert requested_language("My brother lives in Bangladesh and took my land") is None
    uid = _uid()
    r = handle(uid=uid, channel="web", text="Reply me in Bangla")
    assert r.reply_language == "bn" and r.language == "en" and fake_llm.calls[-1]["language"] == "bn"
    assert r.intent != "RIGHTS"
    r = handle(uid=uid, channel="web", text="How do I save money?")
    assert r.reply_language == "bn" and fake_llm.calls[-1]["language"] == "bn"


def test_language_request_without_ai_gets_a_hello_in_that_language(recorder):
    r = handle(uid=_uid(), channel="web", text="Reply me in Bangla")
    assert r.answered_by == "fallback" and r.reply.startswith("আসসালামু আলাইকুম!")


def test_safety_case_without_a_helper_group_says_nobody_was_alerted(fake_llm):
    from app import alerts
    alerts.set_notifier(alerts.LogNotifier())
    try:
        r = handle(uid=_uid(), channel="web", text="My husband is beating me")
    finally:
        alerts.set_notifier(None)
    assert r.safety == "ESCALATE" and r.helper_alerted is False


def test_safety_case_with_a_helper_group_says_alerted(recorder):
    assert handle(uid=_uid(), channel="web", text="My husband is beating me").helper_alerted is True
