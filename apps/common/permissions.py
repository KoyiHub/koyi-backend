"""Permission primitives and view mixins for the three product surfaces.

The rule the whole API leans on: *a request is scoped by its principal, not by
the ids in its URL.* A school user can only ever see rows belonging to their
own school, a teacher only their own school (and, where relevant, their own
class), a student only their own assignments. Views get that for free by
mixing in one of the `*ScopedMixin` classes below and never filtering by a
client-supplied school/teacher/student id.
"""

from typing import Any, ClassVar

from django.db.models import QuerySet
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from apps.common.enums import UserRole


class RoleRequired(BasePermission):
    """Grants access only to an authenticated `User` holding `required_role`.

    Role lives on the identity, not on the token, so revoking a role takes
    effect on the next request rather than on the next token refresh.
    """

    required_role: ClassVar[str] = ""
    message = "This endpoint is not available to your account type."

    def has_permission(self, request: Request, view: Any) -> bool:  # noqa: ARG002
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return False
        return getattr(user, "role", None) == self.required_role


class IsSchoolAdmin(RoleRequired):
    """The school management dashboard."""

    required_role = UserRole.SCHOOL

    def has_permission(self, request: Request, view: Any) -> bool:
        # A `School` row is what makes the identity useful; an orphaned school
        # user (mid-signup, or profile deleted) must not reach tenant data.
        return super().has_permission(request, view) and hasattr(request.user, "school")


class IsTeacher(RoleRequired):
    """The teacher dashboard."""

    required_role = UserRole.TEACHER

    def has_permission(self, request: Request, view: Any) -> bool:
        return super().has_permission(request, view) and hasattr(request.user, "teacher")


class IsStudent(BasePermission):
    """The student assessment runner.

    Students authenticate through `StudentJWTAuthentication`, which installs a
    `StudentPrincipal` rather than a `User`; checking for that attribute is
    what distinguishes them from a staff token replayed at a student URL.
    """

    message = "A student assessment session is required."

    def has_permission(self, request: Request, view: Any) -> bool:  # noqa: ARG002
        principal = getattr(request, "user", None)
        return bool(principal is not None and getattr(principal, "is_student", False))


class IsVerifiedSchoolAdmin(IsSchoolAdmin):
    """Actions that create real-world artefacts (teachers, assessments) and so
    should wait until the school has proven it owns its email address."""

    message = "Verify your school email address before using this feature."

    def has_permission(self, request: Request, view: Any) -> bool:
        return super().has_permission(request, view) and request.user.email_verified


# ---------------------------------------------------------------------------
# View mixins
# ---------------------------------------------------------------------------


class SchoolScopedMixin:
    """Locks a view to the acting school and exposes it as `self.school`."""

    permission_classes = [IsSchoolAdmin]

    #: ORM path from this view's model to `schools.School`, e.g. "school" or
    #: "assessment__school". `None` means the model *is* the school.
    school_lookup: ClassVar[str | None] = "school"

    @property
    def school(self):
        return self.request.user.school

    def filter_to_tenant(self, queryset: QuerySet) -> QuerySet:
        if self.school_lookup is None:
            return queryset.filter(pk=self.school.pk)
        return queryset.filter(**{self.school_lookup: self.school})

    def get_queryset(self) -> QuerySet:
        return self.filter_to_tenant(super().get_queryset())


class TeacherScopedMixin:
    """Locks a view to the acting teacher's school, and optionally to rows the
    teacher personally owns."""

    permission_classes = [IsTeacher]

    school_lookup: ClassVar[str | None] = "school"
    #: Set to an ORM path (e.g. "teacher") to narrow further than the school —
    #: use it for anything a teacher authored and colleagues should not edit.
    teacher_lookup: ClassVar[str | None] = None

    @property
    def teacher(self):
        return self.request.user.teacher

    @property
    def school(self):
        return self.teacher.school

    def filter_to_tenant(self, queryset: QuerySet) -> QuerySet:
        if self.school_lookup is not None:
            queryset = queryset.filter(**{self.school_lookup: self.school})
        if self.teacher_lookup is not None:
            queryset = queryset.filter(**{self.teacher_lookup: self.teacher})
        return queryset

    def get_queryset(self) -> QuerySet:
        return self.filter_to_tenant(super().get_queryset())


class StudentScopedMixin:
    """Locks a view to the acting student."""

    permission_classes = [IsStudent]

    #: ORM path from this view's model to `schools.Student`.
    student_lookup: ClassVar[str | None] = "student"

    @property
    def student(self):
        return self.request.user.student

    def filter_to_tenant(self, queryset: QuerySet) -> QuerySet:
        if self.student_lookup is None:
            return queryset.filter(pk=self.student.pk)
        return queryset.filter(**{self.student_lookup: self.student})

    def get_queryset(self) -> QuerySet:
        return self.filter_to_tenant(super().get_queryset())
