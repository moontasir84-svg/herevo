"""Speech in and out, for women who prefer to talk rather than type.

Live: Google Cloud Speech-to-Text (Bangla: bn-BD) and Text-to-Speech over REST with a restricted API key.
Test: Gemini free-tier transcription (made-up voice notes only). Audio is processed in memory, never stored."""
from __future__ import annotations

import base64
import re
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


class GeminiSpeech:
    """Free-tier transcription for DATA_MODE=test only (made-up voice notes). No voice replies."""

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str):
        self.key = api_key.strip()
        self.model = model

    def transcribe(self, audio: bytes, language: str) -> str | None:
        body = {"contents": [{"role": "user", "parts": [
            {"text": "Transcribe this voice message exactly, in the language it is spoken in (usually Bangla). "
                     "Return only the transcript, nothing else."},
            {"inline_data": {"mime_type": "audio/ogg", "data": base64.b64encode(audio).decode()}}]}],
            "generationConfig": {"temperature": 0}}
        try:
            r = httpx.post(self.URL.format(model=self.model), headers={"x-goog-api-key": self.key}, json=body, timeout=30)
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            return " ".join(p.get("text", "") for p in parts).strip() or None
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.error("gemini transcription failed: %s", exc)
            return None

    def synthesize(self, text: str, language: str) -> bytes | None:
        return None  # Telegram voice notes need OGG; in test mode they are answered with text

    # ---- Read-aloud for the web chat (WAV), with Gemini's text-to-speech models ----
    _tts_model: str | None = None
    TTS_FALLBACKS = ("gemini-2.5-flash-preview-tts", "gemini-2.5-flash-tts", "gemini-2.5-pro-preview-tts")

    def _tts_models(self) -> list[str]:
        """Ask Google which text-to-speech models this key can use (names change over time)."""
        if self._tts_model:
            return [self._tts_model]
        names: list[str] = []
        try:
            r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 200},
                          headers={"x-goog-api-key": self.key}, timeout=15)
            if r.status_code == 200:
                names = [m["name"].split("/", 1)[-1] for m in r.json().get("models", []) if "tts" in m.get("name", "")]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("could not list TTS models: %s", exc)
        names.sort(key=lambda n: ("flash" not in n, "preview" in n, n))  # flash first, stable before preview
        return names + [m for m in self.TTS_FALLBACKS if m not in names]

    def speak_wav(self, text: str, language: str) -> bytes | None:
        body = {"contents": [{"parts": [{"text": text}]}],
                "generationConfig": {"responseModalities": ["AUDIO"],
                                     "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Kore"}}}}}
        for model in self._tts_models()[:4]:
            try:
                r = httpx.post(self.URL.format(model=model), headers={"x-goog-api-key": self.key}, json=body, timeout=45)
            except httpx.HTTPError as exc:
                log.warning("TTS %s network %s", model, type(exc).__name__)
                continue
            if r.status_code != 200:
                log.warning("TTS %s HTTP %s %s", model, r.status_code, r.text[:200])
                continue
            try:
                part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                pcm = base64.b64decode(part["data"])
            except (KeyError, IndexError, ValueError):
                continue
            self._tts_model = model
            rate = 24000
            m = re.search(r"rate=(\d+)", part.get("mimeType", ""))
            if m:
                rate = int(m.group(1))
            return pcm_to_wav(pcm, rate)
        return None


def pcm_to_wav(pcm: bytes, rate: int = 24000) -> bytes:
    import io
    import wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


_speech: Speech | None = None
_checked = False


def get_speech() -> Speech | None:
    global _speech, _checked
    if not _checked:
        st = get_settings()
        if st.speech_provider == "gemini" and st.gemini_api_key:
            _speech = GeminiSpeech(st.gemini_api_key, st.gemini_model)
        elif st.speech_provider == "google" and st.google_api_key:
            _speech = GoogleSpeech(st.google_api_key)
        else:
            _speech = None
        _checked = True
    return _speech


def set_speech(s: Speech | None) -> None:  # used by tests
    global _speech, _checked
    _speech, _checked = s, True
