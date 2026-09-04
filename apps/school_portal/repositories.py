"""Data access for the school management dashboard.

Every repository here takes the acting `School` at construction and scopes each
query to it, so a view cannot accidentally serve another tenant's rows even if
it forgets to filter.
"""

from django.db.models import Count, Q, QuerySet

from apps.activities.models import Activity
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
    select_related = ("teacher", "session")

    def with_result_counts(self) -> QuerySet[Assessment]:
        return self.get_queryset().annotate(
            assigned_count=Count("assignments", distinct=True),
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
    select_related = ("student", "assessment")


class SchoolClassRepository(TenantScopedRepository):
    """Classes belong to a school, so they are scoped like any other tenant row.

    This is the reason `get_class` is not on `ReferenceDataRepository`: a
    school must not be able to reach another school's class by guessing a
    primary key, which an unscoped lookup would allow.
    """

    model = SchoolClass
    select_related = ("grade", "school")

    def for_grade(self, grade: Grade) -> QuerySet[SchoolClass]:
        return self.get_queryset().filter(grade=grade)

    def name_taken(self, grade: Grade, name: str) -> bool:
        return self.exists(grade=grade, name__iexact=name)

    def has_students(self, school_class: SchoolClass) -> bool:
        return school_class.students.exists()


class ActivityRepository(TenantScopedRepository):
    """The school's audit log, newest first.

    Every filter here corresponds to a column on the table rather than a key
    inside `metadata`, which is why those foreign keys exist: a feed you cannot
    narrow to one teacher or one child is a wall of text nobody reads.
    """

    model = Activity
    select_related = ("teacher", "student", "school_class", "school_class__grade", "assessment")

    def feed(
        self,
        *,
        teacher_id=None,
        student_id=None,
        school_class_id=None,
        action: str = "",
        occurred_from=None,
        occurred_to=None,
    ) -> QuerySet[Activity]:
        queryset = self.get_queryset()
        if teacher_id:
            queryset = queryset.filter(teacher_id=teacher_id)
        if student_id:
            queryset = queryset.filter(student_id=student_id)
        if school_class_id:
            queryset = queryset.filter(school_class_id=school_class_id)
        if action:
            queryset = queryset.filter(action=action)
        if occurred_from:
            queryset = queryset.filter(occurred_at__gte=occurred_from)
        if occurred_to:
            queryset = queryset.filter(occurred_at__lte=occurred_to)
        return queryset.order_by("-occurred_at")


class ReferenceDataRepository:
    """Grades and sessions — genuinely shared across tenants, so unscoped.

    Classes used to live here and no longer do: they became tenant-scoped when
    schools started creating their own. See `SchoolClassRepository`.
    """

    def grades(self) -> QuerySet[Grade]:
        return Grade.objects.all()

    def sessions(self) -> QuerySet[AcademicSession]:
        return AcademicSession.objects.all()

    def get_grade(self, pk) -> Grade | None:
        return Grade.objects.filter(pk=pk).first()

    def get_session(self, pk) -> AcademicSession | None:
        return AcademicSession.objects.filter(pk=pk).first()
