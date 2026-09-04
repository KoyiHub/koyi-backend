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


# ---------------------------------------------------------------------------
# Narratives
# ---------------------------------------------------------------------------

NARRATIVE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "maxLength": 900},
        "attention": {"type": "string", "maxLength": 80},
        "strength": {"type": "string", "maxLength": 80},
    },
    "required": ["summary", "attention", "strength"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Narrative:
    """Prose over figures that were already computed."""

    summary: str
    attention: str
    strength: str


def parse_narrative(payload: dict) -> Narrative:
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        # An empty summary is a failed generation wearing a valid shape.
        raise SchemaError("summary is empty")
    return Narrative(
        summary=summary[:900],
        attention=str(payload.get("attention") or "").strip()[:80],
        strength=str(payload.get("strength") or "").strip()[:80],
    )


# ---------------------------------------------------------------------------
# Lesson plans
# ---------------------------------------------------------------------------

LESSON_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string", "maxLength": 300},
        "duration_minutes": {"type": "integer", "minimum": 5, "maximum": 120},
        "materials": {"type": "array", "items": {"type": "string", "maxLength": 120}},
        "steps": {
            "type": "array",
            "minItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "teacher_does": {"type": "string", "maxLength": 500},
                    "children_do": {"type": "string", "maxLength": 500},
                    "minutes": {"type": "integer", "minimum": 1, "maximum": 60},
                },
                "required": ["teacher_does", "children_do", "minutes"],
                "additionalProperties": False,
            },
        },
        "checks": {"type": "array", "items": {"type": "string", "maxLength": 300}},
        "common_errors": {"type": "array", "items": {"type": "string", "maxLength": 300}},
        "success_criteria": {"type": "array", "items": {"type": "string", "maxLength": 300}},
        "note": {"type": "string", "maxLength": 500},
    },
    "required": ["objective", "duration_minutes", "materials", "steps", "checks"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class LessonPlanContent:
    objective: str
    duration_minutes: int
    materials: tuple[str, ...]
    steps: tuple[dict, ...]
    checks: tuple[str, ...]
    common_errors: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "objective": self.objective,
            "duration_minutes": self.duration_minutes,
            "materials": list(self.materials),
            "steps": [dict(step) for step in self.steps],
            "checks": list(self.checks),
            "common_errors": list(self.common_errors),
            "success_criteria": list(self.success_criteria),
            "note": self.note,
        }


def parse_lesson_plan(payload: dict, *, allowed_materials: set | None = None) -> LessonPlanContent:
    """Validate the plan against what the classroom actually has.

    The schema can say materials is a list of strings. Only this can say the
    room has no printer. A plan naming materials a teacher does not have is
    worse than no plan, because they find out mid-lesson.
    """
    objective = str(payload.get("objective") or "").strip()
    if not objective:
        raise SchemaError("objective is empty")

    steps = payload.get("steps") or []
    if len(steps) < 2:
        raise SchemaError("a plan needs at least two steps")

    materials = [str(m).strip() for m in (payload.get("materials") or []) if str(m).strip()]
    if allowed_materials is not None:
        unavailable = [m for m in materials if m.lower() not in allowed_materials]
        if unavailable:
            raise SchemaError(
                "plan needs materials this classroom does not have: " + ", ".join(unavailable)
            )

    return LessonPlanContent(
        objective=objective[:300],
        duration_minutes=int(payload.get("duration_minutes") or 30),
        materials=tuple(materials),
        steps=tuple(
            {
                "teacher_does": str(step.get("teacher_does") or "").strip(),
                "children_do": str(step.get("children_do") or "").strip(),
                "minutes": int(step.get("minutes") or 5),
            }
            for step in steps
        ),
        checks=tuple(str(c).strip() for c in (payload.get("checks") or []) if str(c).strip()),
        common_errors=tuple(
            str(c).strip() for c in (payload.get("common_errors") or []) if str(c).strip()
        ),
        success_criteria=tuple(
            str(c).strip() for c in (payload.get("success_criteria") or []) if str(c).strip()
        ),
        note=str(payload.get("note") or "").strip()[:500],
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
    "LESSON_PLAN_SCHEMA",
    "MARKING_SCHEMA",
    "NARRATIVE_SCHEMA",
    "TAGGING_SCHEMA",
    "LessonPlanContent",
    "MarkingVerdict",
    "Narrative",
    "SchemaError",
    "TagSuggestion",
    "parse_lesson_plan",
    "parse_marking",
    "parse_narrative",
    "parse_tags",
]
