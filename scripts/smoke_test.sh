#!/usr/bin/env bash
# Smoke test for the complete local speaking-practice workflow.
# Runs the backend in MOCK_MODE against a temporary data directory so no
# Ollama or whisper model download is required. Audio conversion (ffmpeg)
# is real; transcription and feedback are simulated and labeled MOCK.
set -euo pipefail

cd "$(dirname "$0")/.."

source .venv/bin/activate

export VOICE_DATA_DIR
VOICE_DATA_DIR="$(mktemp -d)"
export WHISPER_MODEL_DIR="$VOICE_DATA_DIR/models/small.en"
# Any non-empty Gemma tag works in mock mode; it is never contacted.
export GEMMA_MODEL="gemma3:1b-smoke-test"
export OLLAMA_URL=http://127.0.0.1:11434
export MAX_UPLOAD_MB=30
export MAX_DURATION_SECONDS=180
export WHISPER_CPU_THREADS=4
# Explicitly labeled mock mode: no real model is evaluated.
export MOCK_MODE=true

PORT=$(python -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')

uvicorn backend.app.main:app --host 127.0.0.1 --port "$PORT" >/tmp/voice-smoke-server.log 2>&1 &
SERVER_PID=$!

cleanup() {
  kill "$SERVER_PID" 2>/dev/null || true
  wait "$SERVER_PID" 2>/dev/null || true
  rm -rf "$VOICE_DATA_DIR"
}
trap cleanup EXIT

python - "$PORT" <<'PYEOF'
import json
import math
import os
import struct
import sys
import time
import wave
from pathlib import Path

import httpx

PORT = sys.argv[1]
BASE = f"http://127.0.0.1:{PORT}"
DATA_DIR = Path(os.environ["VOICE_DATA_DIR"])
TIMEOUT = httpx.Timeout(30.0)


def api(method, path, **kwargs):
    return httpx.request(method, f"{BASE}{path}", timeout=TIMEOUT, **kwargs)


def make_wav(path: Path, seconds: float = 2.0, tone_hz: float = 440.0) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = int(16000 * seconds)
        data = b"".join(
            struct.pack("<h", int(9000 * math.sin(2 * math.pi * tone_hz * i / 16000)))
            for i in range(frames)
        )
        w.writeframes(data)


def wait_status(sid, wanted, timeout=30.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = api("GET", f"/api/sessions/{sid}").json()
        if last["status"] not in ("queued", "converting", "transcribing", "analyzing"):
            return last
        time.sleep(0.4)
    raise AssertionError(f"session {sid} stuck at {last}")


# 1. Health: backend reports itself ready in mock mode.
ready = False
for _ in range(50):
    try:
        health = api("GET", "/api/health").json()
        if health.get("ready"):
            ready = True
            break
    except httpx.ConnectError:
        pass
    time.sleep(0.5)
assert ready, health
assert health["mock"] is True
assert health["ffmpeg"] is True
print("health OK:", json.dumps({k: health[k] for k in ("ready", "mock", "ffmpeg", "max_upload_mb", "max_duration_seconds")}))

# 2. Topic of the day.
topic_resp = api("GET", "/api/topics/today")
assert topic_resp.status_code == 200
topic = topic_resp.json()["topic"]
assert isinstance(topic, str) and topic
print("topic OK:", topic)

# 3. Upload a recording (real wav, real ffmpeg conversion in the worker).
wav_path = Path("/tmp/voice-smoke-recording.wav")
make_wav(wav_path, seconds=2.0)
with open(wav_path, "rb") as fh:
    resp = api(
        "POST",
        "/api/sessions",
        data={"topic": topic},
        files={"file": ("recording.wav", fh, "audio/wav")},
    )
assert resp.status_code == 201, resp.text
session = resp.json()
sid = session["id"]
assert session["status"] == "queued"
assert session["mock"] is True
print("upload OK: session", sid)

# 4. Poll until transcription + review.
finished = wait_status(sid, ("review", "complete", "error"))
assert finished["status"] == "review", finished
assert finished["original_transcript"]
assert finished["corrected_transcript"] == finished["original_transcript"]
assert finished["duration"] and finished["duration"] > 0
print("transcription OK: %.1fs recognized" % finished["duration"])

# 5. Playback of the original recording.
audio = api("GET", f"/api/sessions/{sid}/audio")
assert audio.status_code == 200
assert audio.headers["content-type"] == "audio/wav"
print("audio playback endpoint OK")

# 6. Transcript correction.
corrected = (
    finished["original_transcript"]
    + " I am adding this sentence so the corrected transcript has enough words for the feedback step."
)
saved = api(
    "PATCH",
    f"/api/sessions/{sid}/transcript",
    json={"transcript": corrected},
)
assert saved.status_code == 200, saved.text
assert saved.json()["corrected_transcript"] == corrected
assert saved.json()["feedback"] is None
print("transcript correction OK")

# 7. Feedback request (local Gemma in mock mode).
resp = api("POST", f"/api/sessions/{sid}/analyze")
assert resp.status_code == 202, resp.text
finished = wait_status(sid, ("complete", "error"))
assert finished["status"] == "complete", finished
fb = finished["feedback"]
assert fb is not None
assert fb["rubric_version"] == "1.0"
assert set(fb["scores"]) == {"relevance", "organization", "support", "concision"}
assert all(isinstance(d["score"], int) and 1 <= d["score"] <= 5 for d in fb["scores"].values())
assert all(d["explanation"] and d["evidence"] for d in fb["scores"].values())
assert fb["strength"]
assert len(fb["improvements"]) == 2
assert all(i["observation"] and i["evidence"] and i["suggested_action"] for i in fb["improvements"])
assert fb["assessment_limitations"]
assert finished["mock"] is True
print("feedback OK: scores", {k: v["score"] for k, v in fb["scores"].items()})

# Evidence quotes must come from the approved transcript.
for d in fb["scores"].values():
    assert d["evidence"] in corrected, d["evidence"]
for i in fb["improvements"]:
    assert i["evidence"] in corrected, i["evidence"]

# 8. History lists the attempt.
history = api("GET", "/api/sessions").json()
assert any(s2["id"] == sid for s2 in history)
print("history OK:", len(history), "attempt(s)")

# 9. Delete removes the session and its audio directory.
audio_dir = DATA_DIR / "audio" / sid
assert audio_dir.is_dir()
resp = api("DELETE", f"/api/sessions/{sid}")
assert resp.status_code == 204
assert api("GET", f"/api/sessions/{sid}").status_code == 404
assert not audio_dir.exists()
print("delete OK")

print("SMOKE TEST PASSED: topic -> upload -> transcription -> correction -> feedback -> history -> delete")
PYEOF
