import json
import math
import struct
import tempfile
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app


def make_wav(path: Path, seconds: float = 1.5) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = int(16000 * seconds)
        for i in range(frames):
            value = int(9000 * math.sin(2 * math.pi * 440 * i / 16000))
            w.writeframes(struct.pack("<h", value))


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        tmp_path / "data",
        tmp_path / "models" / "small.en",
        gemma_model="gemma3:1b",
        mock=True,
    )
    app = create_app(settings=settings)
    with TestClient(app) as c:
        yield c, settings


def wait_for(client, sid, target_statuses, timeout=15.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(f"/api/sessions/{sid}").json()
        if last["status"] in target_statuses:
            return last
        time.sleep(0.2)
    return last


def test_health_mock(client):
    c, _ = client
    health = c.get("/api/health").json()
    assert health["mock"] is True
    assert health["ready"] is True
    assert health["ffmpeg"] is True
    assert health["configured_model"] == "gemma3:1b"


def test_full_workflow_mock_mode(client):
    c, settings = client
    topic = "What is one small habit that has made your day better?"

    wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    make_wav(Path(wav.name), seconds=1.5)
    wav.close()

    with open(wav.name, "rb") as fh:
        resp = c.post(
            "/api/sessions",
            data={"topic": topic},
            files={"file": ("recording.wav", fh, "audio/wav")},
        )
    assert resp.status_code == 201, resp.text
    session = resp.json()
    sid = session["id"]
    assert session["status"] == "queued"
    assert session["mock"] is True

    finished = wait_for(c, sid, {"review", "complete", "error"})
    assert finished["status"] == "review", finished
    assert finished["original_transcript"] is not None
    assert finished["corrected_transcript"] == finished["original_transcript"]
    assert finished["duration"] is not None and finished["duration"] > 0

    # Audio playback endpoint works.
    audio = c.get(f"/api/sessions/{sid}/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"

    # Correct the transcript (must stay above the 20-word analysis minimum).
    corrected = (
        "One small habit that has made my day better is taking a short walk before work. "
        "It gives me a quiet moment to plan my priorities. For example, last Monday I "
        "thought through a difficult conversation while walking, and arrived ready to "
        "explain my idea clearly."
    )
    saved = c.patch(
        f"/api/sessions/{sid}/transcript",
        json={"transcript": corrected},
    )
    assert saved.status_code == 200
    assert saved.json()["corrected_transcript"] == corrected
    assert saved.json()["feedback"] is None

    # Request feedback.
    resp = c.post(f"/api/sessions/{sid}/analyze")
    assert resp.status_code == 202, resp.text

    finished = wait_for(c, sid, {"complete", "error"})
    assert finished["status"] == "complete", finished
    feedback = finished["feedback"]
    assert feedback is not None
    assert feedback["rubric_version"] == "1.0"
    assert set(feedback["scores"]) == {"relevance", "organization", "support", "concision"}
    assert all(1 <= dim["score"] <= 5 for dim in feedback["scores"].values())
    assert len(feedback["improvements"]) == 2
    assert finished["mock"] is True

    # History lists the attempt.
    history = c.get("/api/sessions").json()
    assert any(s["id"] == sid for s in history)

    # Delete removes the row and the audio directory.
    audio_dir = settings.data_dir / "audio" / sid
    assert audio_dir.is_dir()
    gone = c.delete(f"/api/sessions/{sid}")
    assert gone.status_code == 204
    assert c.get(f"/api/sessions/{sid}").status_code == 404
    assert not audio_dir.exists()


def test_unsupported_extension_rejected(client):
    c, _ = client
    resp = c.post(
        "/api/sessions",
        data={"topic": "a topic"},
        files={"file": ("recording.flac", b"not audio", "audio/flac")},
    )
    assert resp.status_code == 415


def test_invalid_audio_becomes_error(client):
    c, settings = client
    resp = c.post(
        "/api/sessions",
        data={"topic": "a topic"},
        files={"file": ("noise.wav", b"this is not audio at all" * 50, "audio/wav")},
    )
    assert resp.status_code == 201
    sid = resp.json()["id"]

    finished = wait_for(c, sid, {"error", "complete"}, timeout=20.0)
    assert finished["status"] == "error", finished
    assert "decoded" in finished["error"]

    # Retry puts it back in the queue and fails again (still invalid audio).
    retry = c.post(f"/api/sessions/{sid}/retry")
    assert retry.status_code == 202
    finished = wait_for(c, sid, {"error"}, timeout=20.0)
    assert finished["status"] == "error"

    c.delete(f"/api/sessions/{sid}")


def test_interrupted_jobs_marked_on_restart(tmp_path):
    data_dir = tmp_path / "data"
    settings = Settings(
        data_dir,
        tmp_path / "models" / "small.en",
        gemma_model="gemma3:1b",
        mock=True,
    )
    from backend.app.storage import Store

    store = Store(data_dir)
    sid = "interrupted-test-id"
    with store.connection() as db:
        db.execute(
            "INSERT INTO sessions(id,topic,created_at,status,job_kind,mock,media_type) "
            "VALUES(?,?,?,?,?,?,?)",
            (sid, "a topic", "2026-01-01T00:00:00+00:00", "converting", "transcribe", 1, "audio/wav"),
        )
    assert store.get(sid)["status"] == "converting"

    # A new app instance (simulating a restart) interrupts in-flight jobs in lifespan.
    with TestClient(create_app(settings=settings)) as c:
        assert c.get(f"/api/sessions/{sid}").json()["status"] == "interrupted"
    assert store.get(sid)["status"] == "interrupted"
    assert "interrupted" in store.get(sid)["error"]