"""Login for the school management dashboard.

Authentication itself is stock `rest_framework_simplejwt.JWTAuthentication`
(configured project-wide in settings); what lives here is the *authorisation at
the door* — only a `school`-role user with a `School` profile gets a token.
"""

from django.db import models
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from apps.common.authentication import RoleScopedTokenSerializer
from apps.common.enums import UserRole
from apps.schools.models import Teacher


class SchoolTokenObtainPairSerializer(RoleScopedTokenSerializer):
    allowed_role = UserRole.SCHOOL
    profile_attribute = "school"

    def validate(self, attrs: dict) -> dict:
        data = super().validate(attrs)
        data["verification_required"] = False
        return data

    def get_profile_payload(self) -> dict:
        school = self.user.school
        return {
            "school": {
                "id": str(school.pk),
                "name": school.name,
                "abbreviation": school.abbreviation,
                "class_system": school.class_system,
            }
        }


class TeacherTokenObtainPairSerializer(RoleScopedTokenSerializer):
    """Authenticate a teacher by human-facing id and school identifier."""

    teacher_id = serializers.CharField()
    school_id = serializers.CharField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    allowed_role = UserRole.TEACHER
    profile_attribute = "teacher"

    def validate(self, attrs: dict) -> dict:
        teacher_id = attrs["teacher_id"].strip()
        school_id = attrs["school_id"].strip()
        teacher = (
            Teacher.objects.select_related("user", "school")
            .filter(teacher_id__iexact=teacher_id)
            .filter(
                models.Q(school__pk=school_id) | models.Q(school__abbreviation__iexact=school_id)
            )
            .first()
        )
        if teacher is None or not teacher.user.check_password(attrs["password"]):
            raise AuthenticationFailed(self.invalid_credentials_message, "no_active_account")

        self.user = teacher.user
        token = self.get_token(self.user)
        return {
            "refresh": str(token),
            "access": str(token.access_token),
            "user": {
                "id": str(self.user.pk),
                "email": self.user.email,
                "role": self.user.role,
                "email_verified": self.user.email_verified,
            },
            "school_id": teacher.school.abbreviation,
        }
