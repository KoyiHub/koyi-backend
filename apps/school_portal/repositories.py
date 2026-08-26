"""Data access for the school management dashboard.

Every repository here takes the acting `School` at construction and scopes each
query to it, so a view cannot accidentally serve another tenant's rows even if
it forgets to filter.
"""

from django.db.models import Count, Q, QuerySet

from apps.assessments.enums import ResultStatus
from apps.assessments.models import Assessment, AssessmentResult
from apps.common.repositories import BaseRepository
from apps.schools.models import AcademicSession, Grade, School, SchoolClass, Student, Teacher


class SchoolRepository(BaseRepository[School]):
    model = School
    select_related = ("user", "logo", "current_session")

    def get_by_user(self, user) -> School | None:
        return self.get_or_none(user=user)

    def abbreviation_taken(self, abbreviation: str) -> bool:
        return self.exists(abbreviation__iexact=abbreviation)


class TenantScopedRepository(BaseRepository):
    """Base for repositories that only ever see one school's rows."""

    #: ORM path from `model` to `schools.School`.
    school_path = "school"

    def __init__(self, school: School) -> None:
        self.school = school

    def get_queryset(self) -> QuerySet:
        return super().get_queryset().filter(**{self.school_path: self.school})


class TeacherRepository(TenantScopedRepository):
    model = Teacher
    select_related = ("user", "school", "school_class", "school_class__grade")

    def search(self, term: str) -> QuerySet[Teacher]:
        if not term:
            return self.all()
        return self.filter(
            Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(teacher_id__icontains=term)
            | Q(user__email__icontains=term)
        )

    def for_class(self, school_class: SchoolClass) -> QuerySet[Teacher]:
        return self.get_queryset().filter(school_class=school_class)

    def teacher_ids_in_use(self) -> set[str]:
        return set(self.get_queryset().values_list("teacher_id", flat=True))


class StudentRepository(TenantScopedRepository):
    model = Student
    select_related = ("school", "school_class", "school_class__grade")

    def search(self, term: str) -> QuerySet[Student]:
        if not term:
            return self.all()
        return self.filter(
            Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(student_id__icontains=term)
            | Q(guardian_name__icontains=term)
        )

    def for_class(self, school_class: SchoolClass) -> QuerySet[Student]:
        return self.get_queryset().filter(school_class=school_class)

    def counts_by_class(self) -> dict[str, int]:
        rows = self.get_queryset().values("school_class_id").annotate(total=Count("id"))
        return {str(row["school_class_id"]): row["total"] for row in rows}


class AssessmentOversightRepository(TenantScopedRepository):
    """Read-only view of the school's assessments for management reporting."""

    model = Assessment
    select_related = ("teacher", "subject", "school_class", "school_class__grade", "session")

    def with_result_counts(self) -> QuerySet[Assessment]:
        return self.get_queryset().annotate(
            assigned_count=Count("assigned_students", distinct=True),
            graded_count=Count(
                "results", filter=Q(results__status=ResultStatus.GRADED), distinct=True
            ),
        )

    def status_breakdown(self) -> dict[str, int]:
        rows = self.get_queryset().values("status").annotate(total=Count("id"))
        return {row["status"]: row["total"] for row in rows}


class ResultOversightRepository(TenantScopedRepository):
    model = AssessmentResult
    school_path = "assessment__school"
    select_related = ("student", "assessment", "assessment__subject")


class ReferenceDataRepository:
    """Grades, classes and sessions — shared across tenants, so unscoped."""

    def grades(self) -> QuerySet[Grade]:
        return Grade.objects.prefetch_related("classes").all()

    def classes(self) -> QuerySet[SchoolClass]:
        return SchoolClass.objects.select_related("grade").all()

    def sessions(self) -> QuerySet[AcademicSession]:
        return AcademicSession.objects.all()

    def get_class(self, pk) -> SchoolClass | None:
        return SchoolClass.objects.select_related("grade").filter(pk=pk).first()

    def get_session(self, pk) -> AcademicSession | None:
        return AcademicSession.objects.filter(pk=pk).first()
