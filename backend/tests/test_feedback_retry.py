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

def test_generation_constrains_evidence_to_transcript(tmp_path, monkeypatch):
    client = CapturingClient(replies=['valid'])
    _patch_httpx(monkeypatch, client)
    analyze({'topic': 'a topic', 'corrected_transcript': TRANSCRIPT, 'duration': 63}, settings(tmp_path))
    payload = client.calls[0]
    for name in ('Score', 'Improvement'):
        choices = payload['format']['$defs'][name]['properties']['evidence']['enum']
        assert choices
        assert all(quote.strip() and quote in TRANSCRIPT for quote in choices)
    assert json.loads(payload['messages'][1]['content'])['evidence_choices'] == choices


def test_retry_includes_previous_response_and_validation_error(tmp_path, monkeypatch):
    client = CapturingClient(replies=['invalid', 'valid'])
    _patch_httpx(monkeypatch, client)
    analyze({'topic': 'a topic', 'corrected_transcript': TRANSCRIPT, 'duration': 63}, settings(tmp_path))
    messages = client.calls[1]['messages']
    assert messages[-2] == {'role': 'assistant', 'content': 'not valid json'}
    assert 'json_invalid' in messages[-1]['content']


def test_evidence_choices_preserve_exact_spans():
    from backend.app.inference import evidence_choices
    transcript = '  First sentence!\nNext\tline has “quotes” and   spaces. ' + 'word ' * 500 + 'x' * 2100
    choices = evidence_choices(transcript)
    assert choices
    assert all(quote.strip() and quote in transcript and len(quote) <= 400 for quote in choices)


def test_evidence_failure_retries_and_never_saves_invalid_scores(tmp_path, monkeypatch):
    class BadEvidenceClient(CapturingClient):
        def _handle(self, request):
            if request.url.path == '/api/chat':
                self.calls.append(json.loads(request.content.decode()))
                feedback = json.loads(json.dumps(GOOD_FEEDBACK))
                feedback['scores']['relevance']['evidence'] = 'Invented private quote'
                return httpx.Response(200, json={'message': {'content': json.dumps(feedback)}})
            return super()._handle(request)

    client = BadEvidenceClient()
    _patch_httpx(monkeypatch, client)
    with pytest.raises(PracticeError, match='Evidence must be an exact') as caught:
        analyze({'topic': 'a topic', 'corrected_transcript': TRANSCRIPT, 'duration': 63}, settings(tmp_path))
    assert len(client.calls) == 2
    assert 'evidence_choices' in client.calls[1]['messages'][-1]['content']
    assert 'Invented private quote' not in str(caught.value)
