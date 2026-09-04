"""What a paper says about a class, and about one child.

Two layers, and the order matters. Everything numeric here is computed from
the stored matrix by ordinary queries; the AI narrative in `apps.ai` is laid
over the top and explains those numbers. It never produces them.

The headline is **level distribution**, not an average score. This is a
Teaching-at-the-Right-Level product: how many children sit at each level is
what a teacher acts on, and an average across two independent domains
describes neither of them.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from apps.assessments.enums import CellOutcome, ResultStatus, SectionResultStatus
from apps.assessments.models import (
    Assessment,
    AssessmentQuestionResponse,
    AssessmentResult,
    AssessmentSectionResult,
    Placement,
    SkillLevelResult,
)
from apps.common.enums import FLN_LEVELS, Domain
from apps.common.services import BaseService


@dataclass(frozen=True, slots=True)
class MarkingStatus:
    """How much of the paper has actually been marked.

    Free-form answers are marked asynchronously, so a teacher opening this
    early sees numbers still settling. Surfacing that is not a nicety - without
    it they read a partial result as a final one.
    """

    total: int
    marked: int
    pending: int

    @property
    def complete(self) -> bool:
        return self.pending == 0


@dataclass(frozen=True, slots=True)
class SkillCell:
    skill_id: str
    skill_name: str
    domain: str
    fln_level: int
    passed: int
    total: int

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 3) if self.total else 0.0


@dataclass(frozen=True, slots=True)
class MissedSubskill:
    subskill_id: str
    subskill_name: str
    skill_name: str
    domain: str
    fln_level: int
    failed: int
    total: int

    @property
    def failed_pct(self) -> int:
        return round(self.failed / self.total * 100) if self.total else 0


@dataclass(frozen=True, slots=True)
class RosterEntry:
    """One child, and what they need. The answer to "who needs help"."""

    student_id: str
    full_name: str
    school_class: str | None
    literacy_level: int | None
    numeracy_level: int | None
    weak_subskills: tuple[str, ...] = ()
    status: str = ""


@dataclass(frozen=True, slots=True)
class AssessmentAnalytics:
    assessment_id: str
    name: str
    code: str
    marking_status: MarkingStatus
    level_distribution: dict
    participation: dict
    section_completion: tuple[dict, ...] = ()
    skill_matrix: tuple[SkillCell, ...] = ()
    most_missed: tuple[MissedSubskill, ...] = ()
    average_percentage: Decimal | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)


class AssessmentAnalyticsService(BaseService):
    """Class-level aggregates for one paper."""

    #: Beyond this the list stops being a place to start and becomes a wall.
    MOST_MISSED_LIMIT = 8

    def __init__(self, assessment: Assessment) -> None:
        self.assessment = assessment

    def build(self) -> AssessmentAnalytics:
        marking = self._marking_status()
        return AssessmentAnalytics(
            assessment_id=str(self.assessment.pk),
            name=self.assessment.name,
            code=self.assessment.code,
            marking_status=marking,
            level_distribution=self._level_distribution(),
            participation=self._participation(),
            section_completion=self._section_completion(),
            skill_matrix=self._skill_matrix(),
            most_missed=self._most_missed(),
            average_percentage=self._average_percentage(),
            warnings=self._warnings(marking),
        )

    def _marking_status(self) -> MarkingStatus:
        responses = AssessmentQuestionResponse.objects.filter(assessment=self.assessment)
        total = responses.count()
        pending = responses.filter(is_correct__isnull=True).count()
        return MarkingStatus(total=total, marked=total - pending, pending=pending)

    def _level_distribution(self) -> dict:
        """How many children landed at each level, per domain.

        Both domains are always present with every level keyed, even at zero.
        A chart that silently omits empty levels misreads as a narrower spread
        than the class actually has.
        """
        counts: dict = {
            domain: dict.fromkeys((str(level) for level in FLN_LEVELS), 0)
            for domain in Domain.values
        }
        rows = Placement.objects.filter(assessment=self.assessment).values_list("domain", "level")
        for domain, level in rows:
            if level is not None:
                counts[domain][str(level)] += 1
        return counts

    def _participation(self) -> dict:
        assignments = self.assessment.assignments.all()
        return {
            "assigned": assignments.count(),
            "started": assignments.exclude(status=ResultStatus.NOT_STARTED).count(),
            "submitted": assignments.filter(
                status__in=[ResultStatus.FINISHED, ResultStatus.GRADED]
            ).count(),
        }

    def _section_completion(self) -> tuple[dict, ...]:
        """Sections are separate sittings, so who still owes one is operational."""
        rows = AssessmentSectionResult.objects.filter(
            result__assessment=self.assessment
        ).select_related("section")

        by_section: dict = defaultdict(lambda: {"submitted": 0, "in_progress": 0, "waiting": 0})
        for row in rows:
            bucket = by_section[row.section]
            if row.status == SectionResultStatus.SUBMITTED:
                bucket["submitted"] += 1
            elif row.status == SectionResultStatus.IN_PROGRESS:
                bucket["in_progress"] += 1
            else:
                bucket["waiting"] += 1

        return tuple(
            {
                "section_id": str(section.pk),
                "name": section.name,
                "domain": section.domain,
                "order": section.order,
                **counts,
            }
            for section, counts in sorted(by_section.items(), key=lambda pair: pair[0].order)
        )

    def _skill_matrix(self) -> tuple[SkillCell, ...]:
        """Pass rates per skill per level.

        A flat percentage per skill would say far less: 95% on phonics at
        level 1 and at level 4 describe different children.
        """
        rows = (
            SkillLevelResult.objects.filter(assessment=self.assessment)
            .select_related("skill")
            .values("skill_id", "skill__name", "skill__domain", "fln_level", "outcome")
        )
        tally: dict = defaultdict(lambda: {"passed": 0, "total": 0})
        for row in rows:
            key = (
                row["skill_id"],
                row["skill__name"],
                row["skill__domain"],
                row["fln_level"],
            )
            tally[key]["total"] += 1
            if row["outcome"] == CellOutcome.PASS:
                tally[key]["passed"] += 1

        return tuple(
            SkillCell(
                skill_id=str(skill_id),
                skill_name=name,
                domain=domain,
                fln_level=level,
                passed=counts["passed"],
                total=counts["total"],
            )
            for (skill_id, name, domain, level), counts in sorted(
                tally.items(), key=lambda pair: (pair[0][2], pair[0][1], pair[0][3])
            )
        )

    def _most_missed(self) -> tuple[MissedSubskill, ...]:
        """Subskills at a level, not individual questions.

        "Q12 was missed by 68%" tells a teacher which item was hard. "Simple
        inference at level 3 was missed by 68%" tells them what to teach.
        """
        rows = (
            SkillLevelResult.objects.filter(assessment=self.assessment)
            .select_related("subskill", "skill")
            .values(
                "subskill_id",
                "subskill__name",
                "skill__name",
                "skill__domain",
                "fln_level",
                "outcome",
            )
        )
        tally: dict = defaultdict(lambda: {"failed": 0, "total": 0})
        for row in rows:
            key = (
                row["subskill_id"],
                row["subskill__name"],
                row["skill__name"],
                row["skill__domain"],
                row["fln_level"],
            )
            tally[key]["total"] += 1
            if row["outcome"] == CellOutcome.FAIL:
                tally[key]["failed"] += 1

        missed = [
            MissedSubskill(
                subskill_id=str(subskill_id),
                subskill_name=subskill_name,
                skill_name=skill_name,
                domain=domain,
                fln_level=level,
                failed=counts["failed"],
                total=counts["total"],
            )
            for (subskill_id, subskill_name, skill_name, domain, level), counts in tally.items()
            if counts["failed"]
        ]
        missed.sort(key=lambda cell: (-cell.failed_pct, -cell.failed))
        return tuple(missed[: self.MOST_MISSED_LIMIT])

    def _average_percentage(self) -> Decimal | None:
        """Kept, but not the headline. Never the largest number on the page."""
        from django.db.models import Avg

        return AssessmentResult.objects.filter(
            assessment=self.assessment, percentage__isnull=False
        ).aggregate(value=Avg("percentage"))["value"]

    def _warnings(self, marking: MarkingStatus) -> tuple[str, ...]:
        warnings: list[str] = []
        if marking.pending:
            warnings.append(
                f"{marking.pending} of {marking.total} answers are still being marked. "
                "These figures will change."
            )
        placed = Placement.objects.filter(assessment=self.assessment).count()
        participation = self._participation()
        if participation["submitted"] and not placed:
            warnings.append("Nobody has been placed yet - marking may still be running.")
        if participation["assigned"] and not participation["started"]:
            warnings.append("No child has opened this assessment yet.")
        return tuple(warnings)


class RosterService(BaseService):
    """Who needs help, and with what.

    The first question in the PRD's teacher list, and the one the aggregates
    cannot answer: a distribution says how many children are at Level 2, this
    says which ones and which subskills to start with.
    """

    #: More than this per child stops being a starting point.
    WEAK_LIMIT = 5

    def __init__(self, assessment: Assessment) -> None:
        self.assessment = assessment

    def build(self, *, domain: str = "", level: int | None = None) -> tuple[RosterEntry, ...]:
        placements = (
            Placement.objects.filter(assessment=self.assessment)
            .select_related("student", "student__school_class__grade")
            .order_by("student__last_name", "student__first_name")
        )
        by_student: dict = defaultdict(dict)
        for placement in placements:
            by_student[placement.student][placement.domain] = placement.level

        weak = self._weak_subskills(domain=domain)
        entries = []
        for student, levels in by_student.items():
            if domain and domain not in levels:
                continue
            if level is not None and levels.get(domain or Domain.LITERACY) != level:
                continue
            entries.append(
                RosterEntry(
                    student_id=str(student.pk),
                    full_name=student.full_name,
                    school_class=str(student.school_class) if student.school_class else None,
                    literacy_level=levels.get(Domain.LITERACY),
                    numeracy_level=levels.get(Domain.NUMERACY),
                    weak_subskills=weak.get(student.pk, ())[: self.WEAK_LIMIT],
                )
            )
        return tuple(entries)

    def _weak_subskills(self, *, domain: str = "") -> dict:
        rows = SkillLevelResult.objects.filter(
            assessment=self.assessment, outcome=CellOutcome.FAIL
        ).select_related("subskill", "skill")
        if domain:
            rows = rows.filter(skill__domain=domain)

        weak: dict = defaultdict(list)
        for row in rows.order_by("fln_level"):
            label = f"{row.subskill.name} (L{row.fln_level})"
            if label not in weak[row.student_id]:
                weak[row.student_id].append(label)
        return {student_id: tuple(labels) for student_id, labels in weak.items()}


@dataclass(frozen=True, slots=True)
class SkillStanding:
    """One skill, and where the child broke down in it.

    A percentage on its own would be close to meaningless: 95% on phonics at
    level 1 and at level 4 describe entirely different children. So this
    carries the level a child last passed and the level they did not.
    """

    skill_id: str
    skill_name: str
    domain: str
    highest_level_passed: int | None
    broke_down_at: int | None
    weak_subskills: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LevelMovement:
    domain: str
    previous: int | None
    current: int | None

    @property
    def direction(self) -> str:
        if self.previous is None or self.current is None:
            return "new"
        if self.current > self.previous:
            return "up"
        if self.current < self.previous:
            return "down"
        return "same"


@dataclass(frozen=True, slots=True)
class StudentBreakdown:
    student_id: str
    full_name: str
    school_class: str | None
    literacy_level: int | None
    numeracy_level: int | None
    last_assessed_at: object | None
    skills: tuple[SkillStanding, ...] = ()
    movement: tuple[LevelMovement, ...] = ()


class StudentBreakdownService(BaseService):
    """One child's standing, broken out by skill.

    By skill rather than subskill: skills are what a person reads, subskills
    are what a lesson plan targets, and they appear here only as the weak
    detail hanging under their parent.
    """

    def __init__(self, student) -> None:
        self.student = student

    def build(self, *, assessment: Assessment | None = None) -> StudentBreakdown:
        placements = list(Placement.objects.filter(student=self.student).order_by("-computed_at"))
        latest = self._latest_by_domain(placements)
        source = assessment or (
            latest[Domain.LITERACY].assessment if latest.get(Domain.LITERACY) else None
        )

        profile = getattr(self.student, "profile", None)
        return StudentBreakdown(
            student_id=str(self.student.pk),
            full_name=self.student.full_name,
            school_class=(str(self.student.school_class) if self.student.school_class else None),
            literacy_level=getattr(profile, "literacy_level", None),
            numeracy_level=getattr(profile, "numeracy_level", None),
            last_assessed_at=getattr(profile, "last_assessed_at", None),
            skills=self._skills(source),
            movement=self._movement(placements),
        )

    def _latest_by_domain(self, placements: list) -> dict:
        latest: dict = {}
        for placement in placements:
            latest.setdefault(placement.domain, placement)
        return latest

    def _skills(self, assessment: Assessment | None) -> tuple[SkillStanding, ...]:
        if assessment is None:
            return ()
        rows = (
            SkillLevelResult.objects.filter(student=self.student, assessment=assessment)
            .select_related("skill", "subskill")
            .order_by("skill__display_order", "fln_level")
        )

        passed: dict = defaultdict(list)
        failed: dict = defaultdict(list)
        weak: dict = defaultdict(list)
        skills: dict = {}
        for row in rows:
            skills[row.skill_id] = row.skill
            if row.outcome == CellOutcome.PASS:
                passed[row.skill_id].append(row.fln_level)
            else:
                failed[row.skill_id].append(row.fln_level)
                label = f"{row.subskill.name} (L{row.fln_level})"
                if label not in weak[row.skill_id]:
                    weak[row.skill_id].append(label)

        return tuple(
            SkillStanding(
                skill_id=str(skill_id),
                skill_name=skill.name,
                domain=skill.domain,
                highest_level_passed=max(passed[skill_id]) if passed[skill_id] else None,
                broke_down_at=min(failed[skill_id]) if failed[skill_id] else None,
                weak_subskills=tuple(weak[skill_id]),
            )
            for skill_id, skill in skills.items()
        )

    def _movement(self, placements: list) -> tuple[LevelMovement, ...]:
        """The diff between the last two readings, per domain.

        Whether a child is improving is the question a teacher actually has,
        and it is the reason the matrix is stored rather than recomputed.
        """
        by_domain: dict = defaultdict(list)
        for placement in placements:
            by_domain[placement.domain].append(placement)

        movements = []
        for domain, rows in by_domain.items():
            current = rows[0].level
            previous = rows[1].level if len(rows) > 1 else None
            movements.append(LevelMovement(domain=domain, previous=previous, current=current))
        return tuple(movements)


__all__ = [
    "AssessmentAnalytics",
    "AssessmentAnalyticsService",
    "MarkingStatus",
    "MissedSubskill",
    "RosterEntry",
    "RosterService",
    "SkillCell",
    "StudentBreakdown",
    "StudentBreakdownService",
]
