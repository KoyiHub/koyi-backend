"""Permission classes for the teacher dashboard."""

from apps.common.permissions import IsTeacher, TeacherScopedMixin

__all__ = ["IsTeacher", "TeacherScopedMixin"]
