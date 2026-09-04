"""What each job is allowed to reply with.

Two things per job: a JSON Schema handed to the provider so generation is
constrained at the decoder, and a parser that turns the reply into something
the domain can use.

The parsers do real work rather than trusting the schema. A schema can say a
field is a string; only the parser can say it must be a subskill that exists,
at a level that subskill is actually assessed at. That check is what keeps a
plausible-sounding invention out of a child's diagnosis.
"""

from dataclasses import dataclass

from apps.assessments.enums import ErrorType

#: Anything below this is not acted on alone - the response goes to a teacher.
DEFAULT_CONFIDENCE_FLOOR = 0.6


class SchemaError(ValueError):
    """The reply satisfied the schema but not the domain."""


# ---------------------------------------------------------------------------
# Marking
# ---------------------------------------------------------------------------

MARKING_SCHEMA = {
    "type": "object",
    "properties": {
        "is_correct": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "error_type": {"type": "string", "enum": [*ErrorType.values, ""]},
        "observation_note": {"type": "string", "maxLength": 400},
    },
    "required": ["is_correct", "confidence", "error_type", "observation_note"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class MarkingVerdict:
    is_correct: bool
    confidence: float
    error_type: str
    observation_note: str

    @property
    def needs_review(self) -> bool:
        return self.confidence < DEFAULT_CONFIDENCE_FLOOR


def parse_marking(payload: dict) -> MarkingVerdict:
    is_correct = payload.get("is_correct")
    if not isinstance(is_correct, bool):
        raise SchemaError("is_correct must be a boolean")

    error_type = (payload.get("error_type") or "").strip()
    if error_type and error_type not in ErrorType.values:
        # A note is worth keeping even when the label is invented; the label
        # is not, because remediation groups on it.
        error_type = ErrorType.OTHER

    # A correct answer with an error type attached is incoherent. Trust the
    # verdict and drop the label rather than storing a contradiction.
    if is_correct:
        error_type = ""

    return MarkingVerdict(
        is_correct=is_correct,
        confidence=_confidence(payload.get("confidence")),
        error_type=error_type,
        observation_note=str(payload.get("observation_note") or "").strip()[:400],
    )


# ---------------------------------------------------------------------------
# Tag suggestion
# ---------------------------------------------------------------------------

TAGGING_SCHEMA = {
    "type": "object",
    "properties": {
        "subskill_code": {"type": "string"},
        "fln_level": {"type": "integer", "minimum": 1, "maximum": 5},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string", "maxLength": 300},
    },
    "required": ["subskill_code", "fln_level", "confidence", "reasoning"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class TagSuggestion:
    subskill_code: str
    fln_level: int
    confidence: float
    reasoning: str

    @property
    def needs_review(self) -> bool:
        return self.confidence < DEFAULT_CONFIDENCE_FLOOR


def parse_tags(payload: dict, *, known_subskills: dict) -> TagSuggestion:
    """Validate against the taxonomy, not just against the schema.

    `known_subskills` maps code to subskill. A code that is not in it is an
    invention, and a level outside the subskill's range is one too - the range
    exists precisely so a mis-tag cannot reach placement.
    """
    code = str(payload.get("subskill_code") or "").strip()
    subskill = known_subskills.get(code)
    if subskill is None:
        raise SchemaError(f"unknown subskill: {code!r}")

    raw_level = payload.get("fln_level")
    try:
        level = int(raw_level)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise SchemaError("fln_level must be an integer") from exc

    if not subskill.covers_level(level):
        low, high = subskill.level_range
        raise SchemaError(f"{subskill.code} is assessed at levels {low} to {high}, not {level}")

    return TagSuggestion(
        subskill_code=code,
        fln_level=level,
        confidence=_confidence(payload.get("confidence")),
        reasoning=str(payload.get("reasoning") or "").strip()[:300],
    )


def _confidence(value) -> float:
    """Clamped rather than rejected.

    A model that reports 1.4 has misunderstood the scale, not failed the task,
    and throwing away an otherwise good verdict over it helps nobody.
    """
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "DEFAULT_CONFIDENCE_FLOOR",
    "MARKING_SCHEMA",
    "TAGGING_SCHEMA",
    "MarkingVerdict",
    "SchemaError",
    "TagSuggestion",
    "parse_marking",
    "parse_tags",
]
