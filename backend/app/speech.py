"""Speech in and out, for women who prefer to talk rather than type.

Google Cloud Speech-to-Text (Bangla: bn-BD) and Text-to-Speech, called over REST with a restricted API
key. Audio is processed in memory and never stored."""
from __future__ import annotations

import base64
import logging
from typing import Protocol

import httpx

from .config import get_settings

log = logging.getLogger("herevo.speech")

STT_LANG = {"bn": "bn-BD", "en": "en-US", "sw": "sw-KE", "es": "es-MX"}
TTS_LANG = {"bn": "bn-IN", "en": "en-US", "sw": "sw-KE", "es": "es-US"}


class Speech(Protocol):
    def transcribe(self, audio: bytes, language: str) -> str | None: ...

    def synthesize(self, text: str, language: str) -> bytes | None: ...


class GoogleSpeech:
    STT_URL = "https://speech.googleapis.com/v1/speech:recognize"
    TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

    def __init__(self, api_key: str):
        self.key = api_key

    def transcribe(self, audio: bytes, language: str) -> str | None:
        body = {"config": {"encoding": "OGG_OPUS", "sampleRateHertz": 48000,
                           "languageCode": STT_LANG.get(language, "bn-BD"),
                           "alternativeLanguageCodes": ["en-US"], "enableAutomaticPunctuation": True},
                "audio": {"content": base64.b64encode(audio).decode()}}
        try:
            r = httpx.post(self.STT_URL, params={"key": self.key}, json=body, timeout=30)
            r.raise_for_status()
            parts = [res["alternatives"][0]["transcript"] for res in r.json().get("results", []) if res.get("alternatives")]
            return " ".join(parts).strip() or None
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.error("speech-to-text failed: %s", exc)
            return None

    def synthesize(self, text: str, language: str) -> bytes | None:
        body = {"input": {"text": text[:1500]},
                "voice": {"languageCode": TTS_LANG.get(language, "bn-IN"), "ssmlGender": "FEMALE"},
                "audioConfig": {"audioEncoding": "OGG_OPUS", "speakingRate": 0.95}}
        try:
            r = httpx.post(self.TTS_URL, params={"key": self.key}, json=body, timeout=30)
            r.raise_for_status()
            return base64.b64decode(r.json()["audioContent"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            log.error("text-to-speech failed: %s", exc)
            return None


_speech: Speech | None = None
_checked = False


def get_speech() -> Speech | None:
    global _speech, _checked
    if not _checked:
        key = get_settings().google_api_key
        _speech = GoogleSpeech(key) if key else None
        _checked = True
    return _speech


def set_speech(s: Speech | None) -> None:  # used by tests
    global _speech, _checked
    _speech, _checked = s, True
