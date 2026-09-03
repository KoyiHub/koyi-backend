"""Login for the teacher dashboard.

Teachers sign in with their teacher id, not an email. The id is what a school
issues them and what they already know; it is globally unique and carries the
school abbreviation as a prefix, so it identifies both the person and their
school without a separate field to fill in.
"""

from django.contrib.auth import authenticate
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.tokens import RefreshToken

from apps.common.enums import UserRole
from apps.schools.models import Teacher

#: Deliberately identical for "no such id", "wrong password" and "disabled", so
#: the endpoint cannot be used to discover which teacher ids exist.
INVALID_CREDENTIALS = "No active account found with these credentials."


class TeacherLoginSerializer(serializers.Serializer):
    teacher_id = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, attrs: dict) -> dict:
        teacher = (
            Teacher.objects.select_related("user", "school")
            .filter(teacher_id__iexact=attrs["teacher_id"].strip())
            .first()
        )
        if teacher is None:
            raise AuthenticationFailed(INVALID_CREDENTIALS, "no_active_account")

        user = authenticate(
            request=self.context.get("request"),
            username=teacher.user.email,
            password=attrs["password"],
        )
        if user is None or user.role != UserRole.TEACHER:
            raise AuthenticationFailed(INVALID_CREDENTIALS, "no_active_account")

        refresh = RefreshToken.for_user(user)
        refresh["role"] = user.role
        refresh["email"] = user.email
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user": {
                "id": str(user.pk),
                "email": user.email,
                "role": user.role,
            },
            "teacher": {
                "id": str(teacher.pk),
                "teacher_id": teacher.teacher_id,
                "full_name": teacher.full_name,
                "school": {"id": str(teacher.school_id), "name": teacher.school.name},
                "school_class": str(teacher.school_class_id) if teacher.school_class_id else None,
            },
        }
