"""HerEvo's tools: Paper Lens, Rehearsal, Counter, Witness and the land share picture.
Each must work with the AI (structured answer cleaned up) and without it (saved answers)."""
import pytest
from fastapi.testclient import TestClient

from app import llm, tools
from app.main import app

client = TestClient(app)
SID = "tools-session-1"
IMG = "A" * 200  # stands in for base64 image data


class ToolLLM:
    model = "fake-tools"

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls = answer, error, []

    def respond(self, *a, **k):  # not used by the tools
        raise AssertionError("tools must use generate()")

    def generate(self, system, parts, tool, schema, seconds=25, temperature=0.4):
        self.calls.append({"system": system, "parts": parts, "tool": tool})
        if self.error:
            raise self.error
        return self.answer


@pytest.fixture
def ai():
    def use(answer=None, error=None):
        f = ToolLLM(answer, error)
        llm.set_llm(f)
        return f
    yield use
    llm.set_llm(None)


def test_scenarios_in_both_languages():
    bn = client.get("/v1/scenarios?lang=bn").json()
    en = client.get("/v1/scenarios?lang=en").json()
    ids = {s["id"] for s in bn["scenarios"]}
    assert {"inlaw_land", "land_office", "salish", "dowry", "ke_home", "mx_ejido"} <= ids
    land = next(s for s in bn["scenarios"] if s["id"] == "inlaw_land")
    assert "দেবর" in land["title"] and land["opening"]
    assert next(s for s in en["scenarios"] if s["id"] == "land_office")["title"].startswith("Land office")
    assert bn["places"]["land_office"] == "ভূমি অফিস"


def test_share_widow_two_sons_one_daughter():
    r = client.post("/v1/share", json={"deceased": "husband", "wives": 1, "sons": 2, "daughters": 1, "land": 100}).json()
    assert r["ok"]
    by = {s["heir"]: s for s in r["shares"]}
    assert (by["wife"]["num"], by["wife"]["den"], by["wife"]["land_each"]) == (1, 8, 12.5)
    assert by["son"]["land_each"] == 35 and by["daughter"]["land_each"] == 17.5
    assert "১৬৪৩০" in r["disclaimer"]


def test_share_needs_lawyer_case():
    r = client.post("/v1/share", json={"deceased": "husband", "wives": 1, "lang": "en"}).json()
    assert r["ok"] is False and "relatives" in r["reason"]


def test_rehearse_with_ai(ai):
    f = ai({"reply": "সবাই কী বলবে?", "score": 140, "good": "ভালো", "try_saying": "আমার ভাগ ১২.৫ শতাংশ।",
            "law": "স্ত্রী ১/৮", "done": False, "real_danger": False})
    r = client.post("/v1/rehearse", json={"session_id": SID, "scenario": "inlaw_land", "said": "আমার ভাগ চাই",
                                          "history": [{"who": "them", "text": "চুপ থাকেন"}], "lang": "bn",
                                          "context": "her share is 12.5 decimals"}).json()
    assert r["source"] == "ai" and r["reply"] == "সবাই কী বলবে?"
    assert r["score"] == 100  # clamped
    system = f.calls[0]["system"]
    assert "Karim" in system and "1,170" in system and "12.5 decimals" in system
    assert "HER (her last line, score this one): আমার ভাগ চাই" in f.calls[0]["parts"][0]["text"]


def test_rehearse_saved_when_ai_down(ai):
    ai(error=ValueError("Gemini failed"))
    hist = []
    for i in range(4):
        r = client.post("/v1/rehearse", json={"session_id": SID, "scenario": "land_office", "lang": "en",
                                              "said": "The official fee is 1,170 taka. I want a receipt.",
                                              "history": hist}).json()
        assert r["source"] == "saved" and r["reply"]
        hist += [{"who": "her", "text": "x"}, {"who": "them", "text": r["reply"]}]
    assert r["done"] and len(r["remember"]) == 3
    assert r["score"] >= 70  # used the fee and the receipt


def test_rehearse_real_danger_stops_the_play(ai):
    f = ai({"reply": "x", "score": 50, "good": "", "try_saying": "", "law": "", "done": False, "real_danger": False})
    r = client.post("/v1/rehearse", json={"session_id": SID, "scenario": "dowry", "lang": "en",
                                          "said": "my husband is beating me right now"}).json()
    assert r["real_danger"] and "999" in r["good"] and not f.calls


def test_rehearse_unknown_scenario():
    assert client.post("/v1/rehearse", json={"session_id": SID, "scenario": "nope", "said": "hi"}).status_code == 404


def test_lens_cleans_ai_answer(ai):
    f = ai({"is_document": True, "doc_type": "হেবা দলিল", "title": "দানের ঘোষণা", "summary": "আপনি জমি দিয়ে দিচ্ছেন।",
            "say": "সই করবেন না।", "verdict": "do_not_sign",
            "facts": [{"label": "জমি", "value": "১২.৫ শতাংশ", "box": [100, 100, 200, 900]},
                      {"label": "তারিখ", "value": "", "box": [1, 2, 3, 4]},
                      {"label": "নাম", "value": "রিনা", "box": [500, 900, 400, 100]}],
            "flags": [{"level": "nonsense", "text": "দান চিরস্থায়ী", "box": [10, 10, 20]}], "steps": ["১৬৪৩০"]})
    r = client.post("/v1/lens", json={"session_id": SID, "image": "data:image/jpeg;base64," + IMG, "lang": "bn"}).json()
    assert r["source"] == "ai" and r["verdict"] == "do_not_sign"
    assert [x["label"] for x in r["facts"]] == ["জমি", "নাম"]  # empty value dropped
    assert r["facts"][0]["box"] == [100, 100, 200, 900] and r["facts"][1]["box"] is None  # inverted box dropped
    assert r["flags"][0]["level"] == "warn" and r["flags"][0]["box"] is None
    img = f.calls[0]["parts"][0]
    assert img["image"] == IMG and img["mime"] == "image/jpeg"  # data: prefix removed


def test_lens_saved_reading_for_sample(ai):
    ai(error=ValueError("down"))
    r = client.post("/v1/lens", json={"session_id": SID, "image": IMG, "lang": "bn", "sample": "heba"}).json()
    assert r["source"] == "saved" and r["verdict"] == "do_not_sign" and r["flags"]
    r = client.post("/v1/lens", json={"session_id": SID, "image": IMG, "lang": "en"}).json()
    assert r["source"] == "unavailable" and "Don't sign" in r["summary"]


def test_saved_samples_are_complete():
    assert set(tools.SAMPLE_READINGS) == {"notice", "heba", "kabin"}
    for name, by_lang in tools.SAMPLE_READINGS.items():
        for lang in ("bn", "en"):
            r = by_lang[lang]
            assert r["summary"] and r["say"] and r["facts"] and r["verdict"] in ("do_not_sign", "check_first", "keep_safe")
            for f in r["facts"] + r["flags"]:
                assert f.get("box") is None or tools._box(f["box"]) == f["box"]


def test_counter_saved_whisper_flags_bribe(ai):
    ai(error=ValueError("down"))
    r = client.post("/v1/counter", json={"session_id": SID, "heard": "পাঁচ হাজার টাকা দিতে হবে", "lang": "bn"}).json()
    assert r["red_flag"] and "১,১৭০" in r["truth"] and r["source"] == "saved"
    r = client.post("/v1/counter", json={"session_id": SID, "heard": "Sign here quickly", "lang": "en"}).json()
    assert r["red_flag"] and "sign" in r["whisper"].lower()


def test_counter_with_ai(ai):
    ai({"whisper": "Please give me a receipt.", "truth": "Fee is Tk 1,170.", "red_flag": True, "ask_for": "Receipt"})
    r = client.post("/v1/counter", json={"session_id": SID, "heard": "Pay 5000", "lang": "en", "place": "land_office"}).json()
    assert r == {"whisper": "Please give me a receipt.", "truth": "Fee is Tk 1,170.", "red_flag": True,
                 "ask_for": "Receipt", "source": "ai"}


def test_statement_saved_and_ai(ai):
    entries = [{"at": "2026-11-02", "text": "He pushed me against the wall."},
               {"at": "2026-11-09", "text": "He took my phone and locked the door."}]
    ai(error=ValueError("down"))
    r = client.post("/v1/statement", json={"session_id": SID, "entries": entries, "lang": "en"}).json()
    assert r["source"] == "saved" and "[name]" in r["statement"] and "2026-11-09" in r["statement"]
    assert "Women Affairs" in r["where"]
    f = ai({"statement": "On 2 November...", "statement_en": "On 2 November...", "timeline": [{"date": "2 Nov", "what": "pushed"}],
            "missing": ["Who saw it?"], "where": "Upazila Women Affairs Officer"})
    r = client.post("/v1/statement", json={"session_id": SID, "entries": entries, "lang": "en"}).json()
    assert r["source"] == "ai" and r["timeline"][0]["date"] == "2 Nov"
    assert "locked the door" in f.calls[0]["parts"][0]["text"]


def test_tools_rate_limited(ai):
    ai(error=ValueError("down"))
    codes = [client.post("/v1/counter", json={"session_id": "flood-session", "heard": "x"}).status_code for _ in range(42)]
    assert codes[:40] == [200] * 40 and codes[-1] == 429


def test_app_page_served():
    r = client.get("/app")
    assert r.status_code == 200 and "HerEvo" in r.text and r.headers["cache-control"] == "no-cache"


# --- Just tell me (plan), dates from papers, radar -------------------------------------------------------------

def test_plan_saved_understands_family_and_paper(ai):
    ai(error=ValueError("down"))
    r = client.post("/v1/plan", json={"session_id": SID, "lang": "bn",
                                      "text": "আমার স্বামী মারা গেছেন। দুই ছেলে এক মেয়ে আছে। ৮০ শতাংশ জমি। দেবর একটা কাগজে টিপসই দিতে বলছে।"}).json()
    assert r["source"] == "saved" and not r["danger"]
    assert [s["tool"] for s in r["steps"]][:3] == ["share", "lens", "rehearse"]
    assert r["share"]["her_land"] == 10.0 and (r["share"]["num"], r["share"]["den"]) == (1, 8)
    assert r["steps"][2]["scenario"] == "inlaw_land"


def test_plan_saved_danger_first(ai):
    ai(error=ValueError("down"))
    r = client.post("/v1/plan", json={"session_id": SID, "lang": "en",
                                      "text": "My husband is beating me and threatens to kill me"}).json()
    assert r["danger"] and r["steps"][0] == {"tool": "call", "number": "999", "title": "Call 999 now",
                                             "why": "If you are in danger, get safe first."}
    assert "witness" in [s["tool"] for s in r["steps"]]


def test_plan_with_ai_cleans_steps_and_computes_share(ai):
    f = ai({"understood": "Husband died; brother-in-law refuses land.", "say": "You are not alone.", "danger": False,
            "family": {"deceased": "husband", "sons": 1, "daughters": 2, "land": 60},
            "steps": [{"tool": "share", "title": "See your share", "why": "Know it."},
                      {"tool": "rehearse", "title": "Practise", "why": "Be ready.", "scenario": "made_up"},
                      {"tool": "call", "title": "Call", "why": "Help.", "number": "12345"},
                      {"tool": "teleport", "title": "x", "why": "y"}], "ask": ""})
    r = client.post("/v1/plan", json={"session_id": SID, "lang": "en", "text": "my husband died..."}).json()
    assert r["source"] == "ai" and [s["tool"] for s in r["steps"]] == ["share", "rehearse", "call"]
    assert r["steps"][1]["scenario"] == "inlaw_land"  # unknown scenario replaced by the one that fits
    assert r["steps"][2]["number"] == "999"           # a number not in the checked facts is replaced
    assert r["share"]["her_land"] == 7.5              # 1/8 of 60, computed by HerEvo, not the AI
    assert "inlaw_land" in f.calls[0]["system"]


def test_lens_dates_cleaned(ai):
    ai({"is_document": True, "doc_type": "Notice", "title": "t", "summary": "s", "say": "s", "verdict": "keep_safe",
        "facts": [{"label": "a", "value": "b"}], "flags": [], "steps": [],
        "dates": [{"what": "Hearing", "date": "2026-12-15", "time": "10:30"}, {"what": "bad", "date": "15/12/2026"},
                  {"what": "no time", "date": "2027-01-02", "time": "morning"}]})
    r = client.post("/v1/lens", json={"session_id": SID, "image": IMG, "lang": "en"}).json()
    assert r["dates"] == [{"what": "Hearing", "date": "2026-12-15", "time": "10:30"},
                          {"what": "no time", "date": "2027-01-02", "time": None}]


def test_saved_notice_has_hearing_date():
    assert tools.SAMPLE_READINGS["notice"]["en"]["dates"][0]["date"] == "2026-12-15"


def test_radar_counts_and_hides_small_areas():
    for i in range(3):
        r = client.post("/v1/radar", json={"session_id": f"radar-sess-{i:03d}", "country": "BD", "area": "  Namunapur ",
                                           "place": "land_office", "kind": "bribe"})
        assert r.status_code == 200
    client.post("/v1/radar", json={"session_id": "radar-sess-x01", "country": "BD", "area": "Charpara",
                                   "place": "union_parishad", "kind": "pressure"})
    s = client.get("/v1/radar/BD").json()
    assert s["total"] == 4 and s["by_kind"] == {"bribe": 3, "pressure": 1} and s["by_place"]["land_office"] == 3
    assert s["areas"] == [{"area": "namunapur", "place": "land_office", "count": 3, "kinds": {"bribe": 3}}]  # charpara hidden


def test_radar_rejects_unknown_and_floods():
    bad = client.post("/v1/radar", json={"session_id": "radar-sess-bad", "place": "moon", "kind": "bribe"})
    assert bad.status_code == 422
    codes = [client.post("/v1/radar", json={"session_id": "radar-flood-01", "place": "police", "kind": "other"}).status_code
             for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_minutes_with_ai_and_saved(ai):
    lines = ["Chairman: he will pay two thousand taka every month from January.", "Husband: give me five thousand for the paperwork."]
    f = ai({"summary": "The chairman settled maintenance.", "promises": [{"who": "Chairman", "what": "Tk 2,000 a month", "when": "from January"}],
            "warnings": ["Asking money for paperwork may be a bribe."], "next": ["Get it in writing."]})
    r = client.post("/v1/minutes", json={"session_id": SID, "lines": lines, "lang": "en", "place": "union_parishad"}).json()
    assert r["source"] == "ai" and r["promises"][0]["when"] == "from January"
    assert "five thousand" in f.calls[0]["parts"][0]["text"] and "Union Parishad" in f.calls[0]["system"]
    ai(error=ValueError("down"))
    r = client.post("/v1/minutes", json={"session_id": SID, "lines": lines, "lang": "en"}).json()
    assert r["source"] == "saved" and r["lines"] == lines and r["warnings"] and "2 things" in r["summary"]


def test_plan_never_uses_bangladesh_law_for_kenya(ai):
    ai({"understood": "Husband died; clan wants the shamba.", "say": "x", "danger": False,
        "family": {"deceased": "husband", "sons": 3, "land": 2},
        "steps": [{"tool": "share", "title": "Check share", "why": "x"},
                  {"tool": "rehearse", "title": "Practise", "why": "x", "scenario": "inlaw_land"}]})
    r = client.post("/v1/plan", json={"session_id": SID, "lang": "en", "country": "KE", "text": "my husband died, the clan wants the shamba"}).json()
    assert r["share"] is None and [s["tool"] for s in r["steps"]] == ["rehearse"] and r["steps"][0]["scenario"] == "ke_home"
    ai(error=ValueError("down"))
    r = client.post("/v1/plan", json={"session_id": SID, "lang": "en", "country": "KE", "text": "My husband died, 2 sons, his brothers want the land"}).json()
    assert r["share"] is None and "share" not in [s["tool"] for s in r["steps"]]
