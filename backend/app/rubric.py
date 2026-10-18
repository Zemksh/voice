import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

RUBRIC_VERSION = '1.0'
RUBRIC = {
    'relevance': [
        'Does not address the topic.',
        'Touches the topic but mostly drifts away.',
        'Addresses the topic with some tangents or an unclear position.',
        'Clear response to the topic with only minor tangents.',
        'Direct, consistently focused response with a clear central point.',
    ],
    'organization': [
        'Ideas are disconnected; no discernible progression.',
        'Some related ideas, but their order is difficult to follow.',
        'Recognizable beginning and progression with some abrupt transitions.',
        'Clear beginning, logical progression, and a closing point.',
        'Purposeful opening, connected development, and an effective conclusion.',
    ],
    'support': [
        'Assertions have no supporting reasons or examples.',
        'Support is vague or only loosely connected to the point.',
        'At least one relevant reason or example, with limited detail.',
        'Specific reasons or examples clearly support the main point.',
        'Well-chosen concrete detail explains why the main point matters.',
    ],
    'concision': [
        'Repetition or unrelated material obscures the main point.',
        'Frequent repetition or detours dilute the response.',
        'Mostly economical, with noticeable repetition or unnecessary detail.',
        'Focused wording with little unnecessary repetition or detail.',
        'Each idea advances the response, with enough detail and no substantial redundancy.',
    ],
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


Text = Annotated[str, Field(min_length=1, max_length=2000)]


class Score(StrictModel):
    score: Annotated[int, Field(ge=1, le=5)]
    explanation: Text
    evidence: Text


class Scores(StrictModel):
    relevance: Score
    organization: Score
    support: Score
    concision: Score


class Improvement(StrictModel):
    observation: Text
    evidence: Text
    suggested_action: Text


class Feedback(StrictModel):
    rubric_version: Literal['1.0']
    scores: Scores
    strength: Text
    improvements: Annotated[list[Improvement], Field(min_length=2, max_length=2)]
    assessment_limitations: Text


def validate_feedback(raw: str, transcript: str) -> Feedback:
    feedback = Feedback.model_validate_json(raw)
    quotes = [s.evidence for s in feedback.scores.__dict__.values()]
    quotes += [i.evidence for i in feedback.improvements]
    if any(not q.strip() or q not in transcript for q in quotes):
        raise ValueError('Evidence must be an exact, nonempty quote from the approved transcript.')
    return feedback


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w]+(?:['’-][\w]+)*\b", text))
