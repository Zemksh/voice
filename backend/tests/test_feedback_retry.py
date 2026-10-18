import json
from pathlib import Path

import httpx
import pytest

from backend.app.config import Settings
from backend.app.inference import PracticeError, analyze

TRANSCRIPT = (
    "One small habit that has made my day better is taking a short walk before work. "
    "A walk takes only ten minutes, but it helps me begin the day with a plan."
)
EVIDENCE = "taking a short walk before work"

GOOD_FEEDBACK = {
    "rubric_version": "1.0",
    "scores": {
        dim: {"score": 3, "explanation": "Fine.", "evidence": EVIDENCE}
        for dim in ("relevance", "organization", "support", "concision")
    },
    "strength": "Clear central idea.",
    "improvements": [
        {"observation": "Obs one.", "evidence": EVIDENCE, "suggested_action": "Act one."},
        {"observation": "Obs two.", "evidence": EVIDENCE, "suggested_action": "Act two."},
    ],
    "assessment_limitations": "AI practice assessment.",
}


class CapturingClient:
    """Stand-in for httpx.Client with a controllable /api/chat reply queue."""

    def __init__(self, replies=()):
        self.queue = list(replies)
        self.calls = []
        self._inner = httpx.Client(
            base_url="http://127.0.0.1:11434",
            transport=httpx.MockTransport(self._handle),
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._inner.__exit__(*exc)

    def get(self, path, **kwargs):
        return self._inner.get(path, **kwargs)

    def post(self, path, json=None, **kwargs):
        return self._inner.post(path, json=json, **kwargs)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "gemma3:1b"}]})
        if path == "/api/show":
            return httpx.Response(200, json={"details": {"families": ["gemma"]}})
        if path == "/api/chat":
            self.calls.append(json.loads(request.content.decode()))
            reply = self.queue.pop(0) if self.queue else "invalid"
            if reply == "valid":
                return httpx.Response(200, json={"message": {"content": json.dumps(GOOD_FEEDBACK)}})
            return httpx.Response(200, json={"message": {"content": "not valid json"}})
        return httpx.Response(404)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        tmp_path / "data",
        tmp_path / "models" / "small.en",
        gemma_model="gemma3:1b",
        mock=False,
    )


def _patch_httpx(monkeypatch, client):
    import backend.app.inference as inference

    monkeypatch.setattr(
        inference,
        "httpx",
        type("H", (), {"Client": lambda *a, **k: client, "HTTPError": httpx.HTTPError})(),
    )


def test_invalid_output_retried_once_then_error(tmp_path, monkeypatch):
    client = CapturingClient(replies=["invalid", "invalid"])
    _patch_httpx(monkeypatch, client)
    session = {"topic": "a topic", "corrected_transcript": TRANSCRIPT, "duration": 100}

    with pytest.raises(PracticeError, match="invalid feedback twice"):
        analyze(session, settings(tmp_path))

    # Original attempt plus exactly one retry.
    assert len(client.calls) == 2


def test_invalid_output_then_valid_recovers(tmp_path, monkeypatch):
    client = CapturingClient(replies=["invalid", "valid"])
    _patch_httpx(monkeypatch, client)
    session = {"topic": "a topic", "corrected_transcript": TRANSCRIPT, "duration": 100}

    result = analyze(session, settings(tmp_path))
    feedback = json.loads(result)
    assert feedback["rubric_version"] == "1.0"
    assert feedback["scores"]["relevance"]["score"] == 3
    assert len(feedback["improvements"]) == 2
    assert len(client.calls) == 2