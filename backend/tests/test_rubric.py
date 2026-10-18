import json
import pytest

from backend.app.rubric import Feedback, validate_feedback, word_count

TRANSCRIPT = (
    "One small habit that has made my day better is taking a short walk before work. "
    "It gives me a quiet moment to plan my priorities. For example, last Monday I "
    "thought through a difficult conversation while walking, and arrived ready to "
    "explain my idea clearly. A walk takes only ten minutes."
)

EVIDENCE = "A walk takes only ten minutes."


def valid_payload(extra=None, omit=None, **overrides):
    payload = {
        "rubric_version": "1.0",
        "scores": {
            dim: {
                "score": 4,
                "explanation": "You stayed on the topic throughout.",
                "evidence": EVIDENCE,
            }
            for dim in ("relevance", "organization", "support", "concision")
        },
        "strength": "A clear central idea runs through the response.",
        "improvements": [
            {
                "observation": "The opening could state the main point sooner.",
                "evidence": EVIDENCE,
                "suggested_action": "Begin with your main point in one sentence.",
            },
            {
                "observation": "Add one concrete detail about what happened.",
                "evidence": EVIDENCE,
                "suggested_action": "Name the situation and why it mattered.",
            },
        ],
        "assessment_limitations": "AI practice assessment, not a validated measurement.",
    }
    if omit:
        payload.pop(omit)
    payload.update(overrides or {})
    if extra:
        payload.update(extra)
    return json.dumps(payload)


def test_valid_feedback_passes():
    feedback = validate_feedback(valid_payload(), TRANSCRIPT)
    assert isinstance(feedback, Feedback)
    assert feedback.rubric_version == "1.0"
    assert feedback.scores.relevance.score == 4
    assert len(feedback.improvements) == 2


def test_evidence_must_be_exact_substring_of_transcript():
    bad = valid_payload()
    data = json.loads(bad)
    data["scores"]["relevance"]["evidence"] = "This sentence was never spoken."
    with pytest.raises(ValueError, match="Evidence must be an exact"):
        validate_feedback(json.dumps(data), TRANSCRIPT)


def test_empty_evidence_rejected():
    bad = valid_payload()
    data = json.loads(bad)
    data["scores"]["relevance"]["evidence"] = "   "
    with pytest.raises(ValueError):
        validate_feedback(json.dumps(data), TRANSCRIPT)


def test_score_out_of_range_rejected():
    for bad_score in (0, 6):
        data = json.loads(valid_payload())
        data["scores"] = {
            dim: {"score": bad_score, "explanation": "x", "evidence": EVIDENCE}
            for dim in ("relevance", "organization", "support", "concision")
        }
        with pytest.raises(Exception):
            validate_feedback(json.dumps(data), TRANSCRIPT)


def test_exactly_two_improvements_required():
    base = json.loads(valid_payload())
    for count in (1, 3):
        base["improvements"] = [
            {"observation": f"o{i}", "evidence": EVIDENCE, "suggested_action": f"a{i}"}
            for i in range(count)
        ]
        with pytest.raises(Exception):
            validate_feedback(json.dumps(base), TRANSCRIPT)


def test_extra_fields_forbidden():
    data = json.loads(valid_payload())
    data["unexpected_field"] = "nope"
    with pytest.raises(Exception):
        validate_feedback(json.dumps(data), TRANSCRIPT)


def test_wrong_rubric_version_rejected():
    data = json.loads(valid_payload())
    data["rubric_version"] = "0.9"
    with pytest.raises(Exception):
        validate_feedback(json.dumps(data), TRANSCRIPT)


def test_malformed_json_raises_value_error():
    with pytest.raises(ValueError):
        validate_feedback("{ not json", TRANSCRIPT)


def test_missing_required_field_rejected():
    data = json.loads(valid_payload())
    data.pop("assessment_limitations")
    with pytest.raises(Exception):
        validate_feedback(json.dumps(data), TRANSCRIPT)


def test_word_count():
    assert word_count("One two three.") == 3
    assert word_count("It's a state-of-the-art test—done.") == 5
    assert word_count("") == 0