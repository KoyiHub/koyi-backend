"""Data access for assessments.

`TeacherScopedAssessmentRepository` is the important one: every read a teacher
makes is filtered to their own school, so a view cannot serve another
tenant's paper even by supplying its id.
"""

from django.db.models import Count, Max, Prefetch, Q, QuerySet

from apps.assessments.models import (
    Assessment,
    AssessmentQuestion,
    AssessmentQuestionContent,
    AssessmentQuestionOption,
    AssessmentSection,
)
from apps.common.repositories import BaseRepository
from apps.curriculum.models import Question, Skill, Subskill


class AssessmentRepository(BaseRepository[Assessment]):
    """Assessments belonging to one school."""

    model = Assessment
    select_related = ("school", "teacher", "session")

    def __init__(self, school) -> None:
        self.school = school

    def get_queryset(self) -> QuerySet[Assessment]:
        return super().get_queryset().filter(school=self.school)

    def with_sections(self) -> QuerySet[Assessment]:
        return self.get_queryset().prefetch_related(
            Prefetch(
                "sections",
                queryset=AssessmentSection.objects.prefetch_related("covers").annotate(
                    question_count=Count("questions", distinct=True)
                ),
            )
        )

    def authored_by(self, teacher) -> QuerySet[Assessment]:
        return self.get_queryset().filter(teacher=teacher)

    def code_taken(self, code: str) -> bool:
        # Unscoped on purpose: the code is what a child types with no school
        # context available, so it has to be unique across every tenant.
        return Assessment.objects.filter(code__iexact=code).exists()

    def by_code(self, code: str) -> Assessment | None:
        return Assessment.objects.filter(code__iexact=code).first()


class SectionRepository(BaseRepository[AssessmentSection]):
    model = AssessmentSection
    select_related = ("assessment",)

    def __init__(self, assessment: Assessment) -> None:
        self.assessment = assessment

    def get_queryset(self) -> QuerySet[AssessmentSection]:
        return super().get_queryset().filter(assessment=self.assessment)

    def next_order(self) -> int:
        highest = self.get_queryset().aggregate(top=Max("order"))["top"]
        return (highest or 0) + 1

    def with_question_counts(self) -> QuerySet[AssessmentSection]:
        return self.get_queryset().annotate(question_count=Count("questions", distinct=True))


class AssessmentQuestionRepository(BaseRepository[AssessmentQuestion]):
    model = AssessmentQuestion
    select_related = ("section", "subskill", "subskill__skill", "skill", "source_question")

    def __init__(self, section: AssessmentSection) -> None:
        self.section = section

    def get_queryset(self) -> QuerySet[AssessmentQuestion]:
        return super().get_queryset().filter(section=self.section)

    def full(self) -> QuerySet[AssessmentQuestion]:
        """Everything the client needs to render a question in one round trip."""
        return self.get_queryset().prefetch_related(
            Prefetch(
                "contents",
                queryset=AssessmentQuestionContent.objects.select_related("media"),
            ),
            Prefetch(
                "options",
                queryset=AssessmentQuestionOption.objects.select_related("media"),
            ),
            "answer",
        )

    def coverage_rows(self) -> list[dict]:
        """Item counts per (subskill, level), for the coverage preview."""
        rows = (
            self.get_queryset()
            .values("subskill_id", "subskill__name", "skill_id", "skill__name", "fln_level")
            .annotate(item_count=Count("id"))
            .order_by("skill__display_order", "subskill__display_order", "fln_level")
        )
        return [dict(row) for row in rows]


class QuestionBankRepository(BaseRepository[Question]):
    """Read-only browsing of the company-authored bank.

    Teachers select from here; nothing in the teacher surface writes to it.
    """

    model = Question
    select_related = ("subskill", "subskill__skill", "question_bank", "image", "audio_description")
    prefetch_related = ("contents", "contents__media", "options", "answer")

    def search(
        self,
        *,
        domain: str = "",
        skill_id=None,
        subskill_id=None,
        fln_level: int | None = None,
        question_type: str = "",
        term: str = "",
    ) -> QuerySet[Question]:
        queryset = self.get_queryset()
        if domain:
            queryset = queryset.filter(subskill__skill__domain=domain)
        if skill_id:
            queryset = queryset.filter(subskill__skill_id=skill_id)
        if subskill_id:
            queryset = queryset.filter(subskill_id=subskill_id)
        if fln_level:
            queryset = queryset.filter(fln_level=fln_level)
        if question_type:
            queryset = queryset.filter(type=question_type)
        if term:
            queryset = queryset.filter(Q(content__icontains=term))
        return queryset


class TaxonomyRepository:
    """The skill tree. Shared reference data, so unscoped."""

    def skills(self, *, domain: str = "") -> QuerySet[Skill]:
        queryset = Skill.objects.prefetch_related("subskills")
        return queryset.filter(domain=domain) if domain else queryset

    def get_subskill(self, pk) -> Subskill | None:
        return Subskill.objects.select_related("skill").filter(pk=pk).first()

    def subskills_by_id(self, ids) -> dict:
        rows = Subskill.objects.select_related("skill").filter(pk__in=set(ids))
        return {subskill.pk: subskill for subskill in rows}
