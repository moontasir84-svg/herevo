"""Tests run against a real Postgres + pgvector database (TEST_DATABASE_URL), with fake Claude,
fake Telegram and the offline hash embedder, so they need no API keys."""
import os

import pytest

os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql://herevo:herevo@localhost:5432/herevo_test")
os.environ["EMBEDDINGS_PROVIDER"] = "hash"
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
os.environ["TELEGRAM_WEBHOOK_SECRET"] = "s3cret"
os.environ["HELPER_CHAT_ID"] = "999000"

from app import alerts, db, llm, packs, speech  # noqa: E402
from app.channels import telegram  # noqa: E402


class FakeLLM:
    model = "fake-claude"

    def __init__(self):
        self.calls = []
        self.next = None  # override the next response (dict) or exception

    def respond(self, language, passages, message, history=None, note=None):
        self.calls.append({"language": language, "passages": passages, "message": message, "history": history or [],
                           "note": note})
        if self.next is not None:
            nxt, self.next = self.next, None
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        ids = [p["id"] for p in passages]
        if "বিক্রি" in message:
            return {"intent": "EARN", "confidence": 0.95, "safety": "OK", "covered": True,
                    "answer": "লিখে রাখলাম: ৪০ কেজি ধান, ১,২০০ টাকা।", "answer_en": "Recorded: 40 kg rice, 1,200 taka.",
                    "cited_ids": [], "sale": {"product": "rice", "quantity": 40, "unit": "kg", "amount": 1200, "currency": "BDT"}}
        return {"intent": "RIGHTS", "confidence": 0.9, "safety": "OK", "covered": True,
                "answer": "সন্তান থাকলে স্ত্রী আট ভাগের এক ভাগ পান।", "answer_en": "With children, the wife gets one-eighth.",
                "cited_ids": ids[:1] + ["made-up-id"]}


class Recorder:
    def __init__(self):
        self.sent = []

    def notify_helper(self, text, reply_to=None):
        self.sent.append(("helper", text))
        self.threaded = getattr(self, "threaded", []) + [reply_to]
        return True

    def send(self, chat_id, text):
        self.sent.append((str(chat_id), text))

    def send_voice(self, chat_id, audio):
        self.sent.append((str(chat_id), ("VOICE", audio)))

    def download(self, file_id):
        return b"OggS-fake-audio-" + file_id.encode()


class FakeSpeech:
    def __init__(self, transcript="স্বামী মারা যাওয়ার পর দেবর বলছে জমি আমার না।"):
        self.transcript = transcript
        self.heard = []

    def transcribe(self, audio, language):
        self.heard.append(audio)
        return self.transcript

    def synthesize(self, text, language):
        return b"OggS-tts-" + language.encode()


@pytest.fixture(scope="session", autouse=True)
def database():
    db.init_schema()
    for cc in packs.available_countries():
        packs.ingest(cc)
    yield
    db.close()


@pytest.fixture(autouse=True)
def clean(database):
    with db.pool().connection() as conn:
        conn.execute("TRUNCATE users, sales, cases, escalations, turns, radar RESTART IDENTITY CASCADE")
    from app import main
    main._hits.clear()
    yield


@pytest.fixture
def fake_llm():
    f = FakeLLM()
    llm.set_llm(f)
    yield f
    llm.set_llm(None)


@pytest.fixture
def recorder():
    r = Recorder()
    alerts.set_notifier(r)
    telegram.set_sender(r)
    yield r
    alerts.set_notifier(None)
    telegram.set_sender(None)


@pytest.fixture
def no_speech():
    speech.set_speech(None)
    yield
    speech.set_speech(None)


@pytest.fixture
def fake_speech():
    f = FakeSpeech()
    speech.set_speech(f)
    yield f
    speech.set_speech(None)
