"""Inputs and outputs for the assessment services.

Frozen dataclasses, per the convention in `apps.common.dto`: serializers stop
at the view, services speak these. No Django objects on the way in — ids only,
so the service resolves them through a repository and tenant scoping is
enforced in one place rather than assumed by the caller.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class CreateAssessmentInput:
    name: str
    instructions: str = ""
    session_id: UUID | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UpdateAssessmentInput:
    """Every field optional: this backs a PATCH on a draft."""

    name: str | None = None
    instructions: str | None = None
    session_id: UUID | None = None
    opens_at: datetime | None = None
    closes_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CreateSectionInput:
    domain: str
    name: str
    instructions: str = ""
    timer: timedelta | None = None
    covers: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class UpdateSectionInput:
    name: str | None = None
    instructions: str | None = None
    timer: timedelta | None = None
    order: int | None = None
    covers: tuple[UUID, ...] | None = None


@dataclass(frozen=True, slots=True)
class ContentBlockInput:
    type: str
    display_order: int
    text_content: str = ""
    media_id: UUID | None = None
    alt_text: str = ""
    caption: str = ""


@dataclass(frozen=True, slots=True)
class OptionInput:
    type: str
    value: str = ""
    media_id: UUID | None = None
    is_correct: bool = False


@dataclass(frozen=True, slots=True)
class AnswerInput:
    value: str = ""
    media_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class QuestionInput:
    """One question as posted by the client.

    Always complete content, whether the teacher picked it out of the bank or
    wrote it: bank selection is a client-side prefill, so the payload looks the
    same either way. `source_question_id` records which it was.
    """

    subskill_id: UUID
    fln_level: int
    question_type: str
    text: str
    layout: str = ""
    description: str = ""
    point: Decimal = Decimal("1")
    source_question_id: UUID | None = None
    contents: tuple[ContentBlockInput, ...] = ()
    options: tuple[OptionInput, ...] = ()
    answer: AnswerInput | None = None


@dataclass(frozen=True, slots=True)
class CoverageCell:
    """One (subskill, level) pair and how many items probe it."""

    subskill_id: UUID
    subskill_name: str
    skill_id: UUID
    skill_name: str
    domain: str
    fln_level: int
    item_count: int


@dataclass(frozen=True, slots=True)
class SectionCoverage:
    section_id: UUID
    section_name: str
    domain: str
    question_count: int
    cells: tuple[CoverageCell, ...] = ()
    #: Subskills the section declared it covers but carries no items for.
    gaps: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AssessmentCoverage:
    """What the paper can actually establish about a child.

    `levels_probed` is the headline: placement needs evidence across levels,
    so a paper that only probes level 2 cannot place anyone.
    """

    assessment_id: UUID
    question_count: int
    sections: tuple[SectionCoverage, ...] = ()
    domains: tuple[str, ...] = ()
    levels_probed: tuple[int, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)
