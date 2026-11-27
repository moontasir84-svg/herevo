"""Records HerEvo's fixed sentences once with HerEvo's own voice (the live /v1/tts), as small MP3s in web/voice,
so the app plays them instantly. Only sentences not recorded yet are fetched; run again to fill any gaps."""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://herevo.onrender.com"
VOICE = Path("web/voice")


def key(lang: str, text: str) -> str:
    """Same FNV-1a as voiceKey() in web/app.html."""
    h = 0x811C9DC5
    for b in (lang + "|" + " ".join(text.split())).encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def tts(text: str, lang: str) -> bytes | None:
    body = json.dumps({"text": text, "lang": lang, "session_id": "voicebuild-0001"}).encode()
    req = urllib.request.Request(BASE + "/v1/tts", data=body, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read() if r.status == 200 else None
    except Exception as exc:  # noqa: BLE001
        print("  failed:", type(exc).__name__, getattr(exc, "code", ""))
        return None


def main() -> int:
    phrases = json.loads((VOICE / "phrases.json").read_text(encoding="utf-8"))
    manifest_path = VOICE / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    urllib.request.urlopen(BASE + "/health", timeout=120).read()  # wake the server
    made = missing = 0
    for p in phrases:
        k = key(p["lang"], p["text"])
        if k in manifest and (VOICE / manifest[k]).exists():
            continue
        print(f"{k}: {p['text'][:60]}")
        wav = tts(p["text"], p["lang"])
        if wav is None:
            time.sleep(20)
            wav = tts(p["text"], p["lang"])
        if wav is None:
            missing += 1
            continue
        tmp = Path("/tmp/voice.wav")
        tmp.write_bytes(wav)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp), "-ac", "1", "-ar", "24000",
                        "-codec:a", "libmp3lame", "-b:a", "40k", str(VOICE / f"{k}.mp3")], check=True)
        manifest[k] = f"{k}.mp3"
        made += 1
        manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
        time.sleep(4)
    print(f"recorded {made}, still missing {missing}, total {len(manifest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
