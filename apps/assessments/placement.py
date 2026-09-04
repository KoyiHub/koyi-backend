"""Marking, diagnosis and placement.

Deterministic from end to end. No model call touches this path, because a
placement has to be reproducible, testable, explainable to a head teacher, and
free to compute. The AI layer explains what these rules decided; it never
decides anything itself.

The ladder, bottom to top:

    items      -> a (subskill x level) cell passes at 70% correct
    cells      -> a skill passes at a level when 80% of its probed subskills do
    skills     -> a level passes when enough core skills do (PlacementRule)
    levels     -> placement is the lowest probed level not passed

Only *probed* levels count at every rung. A paper covering levels 3 to 5 says
nothing about level 1, and treating silence as failure would place every child
who sat it at the bottom.
"""

from dataclasses import dataclass
from decimal import Decimal

from django.db import transaction
from django.db.models import Case, Count, IntegerField, Sum, When
from django.utils import timezone

from apps.assessments.enums import CellOutcome, GradedBy, ResultStatus
from apps.assessments.models import (
    Assessment,
    AssessmentQuestionResponse,
    AssessmentResult,
    Placement,
    PlacementRule,
    SkillLevelResult,
)
from apps.common.enums import Domain, QuestionType, SkillStateStatus
from apps.common.services import BaseService
from apps.schools.models import StudentProfile, StudentSkillState

#: Correct answers needed for one (subskill x level) cell to pass.
CELL_PASS_RATIO = Decimal("0.70")

#: Subskills of a skill that must pass at a level for the skill to pass there.
SKILL_PASS_RATIO = Decimal("0.80")

#: Cells backing a skill state before mastery is claimed. One good day is not
#: evidence; this is what makes `MASTERED` answerable.
MASTERY_EVIDENCE = 2


class MarkingService(BaseService):
    """Marks what can be marked without a model.

    Choice and number items are decided here and now. Free text, audio and
    uploads are left with `is_correct` null - "pending", not "wrong" - for the
    AI marker to pick up. Everything downstream ignores unmarked responses, so
    a partially marked paper produces a partial diagnosis rather than a wrong
    one.
    """

    #: Types this service can settle on its own.
    OBJECTIVE = {
        QuestionType.SINGLE_CHOICE,
        QuestionType.MULTIPLE_CHOICE,
        QuestionType.TRUE_FALSE,
        QuestionType.NUMBER,
    }

    @transaction.atomic
    def mark(self, assessment: Assessment, student) -> int:
        """Mark every objective response. Returns how many were settled."""
        responses = (
            AssessmentQuestionResponse.objects.filter(assessment=assessment, student=student)
            .select_related("assessment_question", "assessment_question__answer")
            .prefetch_related("selected_options", "assessment_question__options")
        )

        marked = 0
        for response in responses:
            question = response.assessment_question
            if question.question_type not in self.OBJECTIVE:
                continue
            is_correct = self._is_correct(response, question)
            if is_correct is None:
                # No answer key to mark against. That is an authoring fault, and
                # counting it against the child would put a data problem into
                # their diagnosis. Leave it pending so it is excluded instead.
                continue
            response.is_correct = is_correct
            response.awarded_points = question.point if is_correct else Decimal("0")
            response.graded_by = GradedBy.AUTO
            response.save(update_fields=["is_correct", "awarded_points", "graded_by", "updated_at"])
            marked += 1
        return marked

    def _is_correct(self, response, question) -> bool | None:
        """True, False, or None when there is nothing to mark against."""
        if question.question_type == QuestionType.NUMBER:
            return self._number_matches(response, question)
        return self._selection_matches(response, question)

    def _selection_matches(self, response, question) -> bool | None:
        """Exact set match, so a multiple-choice answer is right or it is not.

        Partial credit would need a rule about which halves count, and the
        matrix reads a boolean per item - there is nowhere for a half to go.
        """
        expected = {option.pk for option in question.options.all() if option.is_correct}
        if not expected:
            return None
        given = {row.assessment_question_option_id for row in response.selected_options.all()}
        return expected == given

    def _number_matches(self, response, question) -> bool | None:
        answer = getattr(question, "answer", None)
        if answer is None or not answer.value.strip():
            return None
        try:
            return Decimal(response.text_value.strip()) == Decimal(answer.value.strip())
        except (ArithmeticError, ValueError):
            # A child typing "seven" is wrong, not an error worth raising.
            return False


@dataclass(frozen=True, slots=True)
class LevelVerdict:
    level: int
    skills_passed: int
    skills_probed: int
    required: int
    passed: bool


class MatrixService(BaseService):
    """Builds the persisted (subskill x level) grid for one child."""

    @transaction.atomic
    def build(self, assessment: Assessment, student) -> list[SkillLevelResult]:
        rows = (
            AssessmentQuestionResponse.objects.filter(
                assessment=assessment, student=student, is_correct__isnull=False
            )
            .values(
                "assessment_question__subskill_id",
                "assessment_question__skill_id",
                "assessment_question__fln_level",
            )
            .annotate(
                # One aggregate per cell rather than a scan over every response.
                attempted=Count("id"),
                correct=Sum(
                    Case(When(is_correct=True, then=1), default=0, output_field=IntegerField())
                ),
            )
            .order_by()
        )

        SkillLevelResult.objects.filter(assessment=assessment, student=student).delete()
        results = [
            SkillLevelResult(
                assessment=assessment,
                student=student,
                subskill_id=row["assessment_question__subskill_id"],
                skill_id=row["assessment_question__skill_id"],
                fln_level=row["assessment_question__fln_level"],
                items_attempted=row["attempted"],
                items_correct=row["correct"],
                outcome=(
                    CellOutcome.PASS
                    if _ratio(row["correct"], row["attempted"]) >= CELL_PASS_RATIO
                    else CellOutcome.FAIL
                ),
            )
            for row in rows
        ]
        return SkillLevelResult.objects.bulk_create(results)


class PlacementService(BaseService):
    """Turns the grid into a level per domain."""

    @transaction.atomic
    def place(self, assessment: Assessment, student) -> list[Placement]:
        cells = list(
            SkillLevelResult.objects.filter(assessment=assessment, student=student).select_related(
                "skill", "subskill"
            )
        )
        now = timezone.now()
        placements = []
        for domain in Domain.values:
            domain_cells = [c for c in cells if c.skill.domain == domain]
            if not domain_cells:
                # Nothing probed in this domain; say nothing about it.
                continue
            placements.append(self._place_domain(assessment, student, domain, domain_cells, now))

        self._update_profile(student, placements, now)
        self._update_skill_states(cells, student, now)
        return placements

    def _place_domain(self, assessment, student, domain, cells, now) -> Placement:
        probed = sorted({c.fln_level for c in cells})
        verdicts = [self._judge_level(domain, level, cells) for level in probed]

        # The lowest probed level they did not pass is what they need taught.
        # If they passed everything probed, the paper found no ceiling and the
        # highest level it reached is the best answer available.
        failed = [v for v in verdicts if not v.passed]
        level = failed[0].level if failed else probed[-1]

        placement, _ = Placement.objects.update_or_create(
            student=student,
            assessment=assessment,
            domain=domain,
            defaults={"level": level, "levels_probed": probed, "computed_at": now},
        )
        return placement

    def _judge_level(self, domain: str, level: int, cells) -> LevelVerdict:
        at_level = [c for c in cells if c.fln_level == level]
        by_skill: dict = {}
        for cell in at_level:
            by_skill.setdefault(cell.skill, []).append(cell)

        # Only core skills gate a level; enrichment must never hold a child back.
        core = {skill: rows for skill, rows in by_skill.items() if skill.is_core}
        passed = sum(1 for rows in core.values() if self._skill_passes(rows))

        rule = PlacementRule.objects.filter(domain=domain, fln_level=level).first()
        required = rule.required_skills if rule else _default_required(len(core))
        return LevelVerdict(
            level=level,
            skills_passed=passed,
            skills_probed=len(core),
            required=required,
            passed=passed >= required,
        )

    def _skill_passes(self, cells) -> bool:
        """80% of the subskills actually probed for this skill at this level.

        Measured against what was probed, not against every subskill the skill
        has: a paper that tests two of five subskills is thin evidence, but it
        is not evidence of failure on the three it never asked about.
        """
        passing = sum(1 for c in cells if c.outcome == CellOutcome.PASS)
        return _ratio(passing, len(cells)) >= SKILL_PASS_RATIO

    def _update_profile(self, student, placements, now) -> None:
        profile, _ = StudentProfile.objects.get_or_create(student=student)
        for placement in placements:
            # Absolute: the newest reading replaces the last, up or down.
            if placement.domain == Domain.LITERACY:
                profile.literacy_level = placement.level
            else:
                profile.numeracy_level = placement.level
        profile.last_assessed_at = now
        profile.save(
            update_fields=[
                "literacy_level",
                "numeracy_level",
                "last_assessed_at",
                "updated_at",
            ]
        )

    def _update_skill_states(self, cells, student, now) -> None:
        for cell in cells:
            state, _ = StudentSkillState.objects.get_or_create(
                student=student, subskill=cell.subskill
            )
            if cell.outcome == CellOutcome.PASS:
                state.evidence_count += 1
                if (
                    state.highest_level_passed is None
                    or cell.fln_level > state.highest_level_passed
                ):
                    state.highest_level_passed = cell.fln_level
                state.status = (
                    SkillStateStatus.MASTERED
                    if state.evidence_count >= MASTERY_EVIDENCE
                    else SkillStateStatus.DEVELOPING
                )
            else:
                state.status = SkillStateStatus.WEAK
            state.last_observed_at = now
            state.save(
                update_fields=[
                    "status",
                    "highest_level_passed",
                    "evidence_count",
                    "last_observed_at",
                    "updated_at",
                ]
            )


class DiagnosisService(BaseService):
    """Marks, builds the grid, places, and rolls up the result.

    Idempotent by construction: the grid is rebuilt rather than appended to and
    placements are upserted, so re-running after a threshold change is safe and
    is the intended way to apply one.
    """

    @transaction.atomic
    def run(self, assessment: Assessment, student) -> list[Placement]:
        MarkingService().mark(assessment, student)
        MatrixService().build(assessment, student)
        placements = PlacementService().place(assessment, student)
        self._roll_up(assessment, student)
        return placements

    def _roll_up(self, assessment: Assessment, student) -> None:
        responses = AssessmentQuestionResponse.objects.filter(
            assessment=assessment, student=student, is_correct__isnull=False
        )
        attempted = responses.count()
        correct = responses.filter(is_correct=True).count()

        result = AssessmentResult.objects.filter(assessment=assessment, student=student).first()
        if result is None:
            return
        result.items_attempted = attempted
        result.items_correct = correct
        result.percentage = (
            (Decimal(correct) / Decimal(attempted) * 100).quantize(Decimal("0.01"))
            if attempted
            else None
        )
        result.score = Decimal(correct)
        result.status = ResultStatus.GRADED
        result.marked_at = timezone.now()
        result.save(
            update_fields=[
                "items_attempted",
                "items_correct",
                "percentage",
                "score",
                "status",
                "marked_at",
                "updated_at",
            ]
        )


def _ratio(part: int, whole: int) -> Decimal:
    return Decimal(part) / Decimal(whole) if whole else Decimal(0)


def _default_required(applicable: int) -> int:
    """Three quarters, rounded up — the fallback when no rule is seeded."""
    if applicable == 0:
        return 1
    return -(-applicable * 3 // 4)


__all__ = [
    "CELL_PASS_RATIO",
    "MASTERY_EVIDENCE",
    "SKILL_PASS_RATIO",
    "DiagnosisService",
    "MarkingService",
    "MatrixService",
    "PlacementService",
]
