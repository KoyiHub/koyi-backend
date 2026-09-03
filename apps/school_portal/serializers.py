"""Request/response shapes for the school management dashboard.

Serializers validate and render only. Anything that decides *whether* an action
is allowed, or that writes across more than one table, belongs in
`apps.school_portal.services`.
"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from apps.assessments.models import Assessment
from apps.media_assets.models import MediaAsset
from apps.schools.enums import ClassSystem
from apps.schools.models import (
    ABBREVIATION_VALIDATOR,
    AcademicSession,
    Grade,
    School,
    SchoolClass,
    Student,
    Teacher,
)


def _validate_password_strength(value: str, user=None) -> str:
    try:
        validate_password(value, user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError(list(exc.messages)) from exc
    return value


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


class SessionSerializer(serializers.ModelSerializer):
    # See the note on SchoolClassSerializer.label below.
    label = serializers.CharField(read_only=True)  # type: ignore[assignment]

    class Meta:
        model = AcademicSession
        fields = ["id", "start_year", "end_year", "label"]


class SchoolClassSerializer(serializers.ModelSerializer):
    grade_name = serializers.CharField(source="grade.name", read_only=True)
    # django-stubs reads a declared field named `label` as an override of
    # `Field.label`; at runtime DRF collects it as an ordinary field.
    label = serializers.CharField(source="__str__", read_only=True)  # type: ignore[assignment]

    class Meta:
        model = SchoolClass
        fields = ["id", "grade", "grade_name", "name", "label"]


class GradeSerializer(serializers.ModelSerializer):
    """Grades are shared reference data. Classes are not nested here: they
    belong to a school, and nesting them would serve every tenant's."""

    class Meta:
        model = Grade
        fields = ["id", "name"]


class MediaAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaAsset
        fields = [
            "id",
            "type",
            "url",
            "mime_type",
            "original_filename",
            "size_bytes",
            "duration_seconds",
        ]


# ---------------------------------------------------------------------------
# Auth + profile
# ---------------------------------------------------------------------------


class SchoolRegistrationSerializer(serializers.Serializer):
    """Signup. Creates the login account and the tenant together."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})
    name = serializers.CharField(max_length=255)
    abbreviation = serializers.CharField(max_length=12, validators=[ABBREVIATION_VALIDATOR])
    class_system = serializers.ChoiceField(choices=ClassSystem.choices, default=ClassSystem.GRADE)
    current_session = serializers.PrimaryKeyRelatedField(
        queryset=AcademicSession.objects.all(), required=False, allow_null=True
    )
    logo = serializers.PrimaryKeyRelatedField(
        queryset=MediaAsset.objects.all(), required=False, allow_null=True
    )

    def validate_email(self, value: str) -> str:
        return value.lower().strip()

    def validate_abbreviation(self, value: str) -> str:
        return value.upper().strip()

    def validate(self, attrs: dict) -> dict:
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        _validate_password_strength(attrs["password"])
        return attrs


class SchoolSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(read_only=True)
    email_verified = serializers.BooleanField(read_only=True)
    logo = MediaAssetSerializer(read_only=True)
    logo_id = serializers.PrimaryKeyRelatedField(
        queryset=MediaAsset.objects.all(),
        source="logo",
        write_only=True,
        required=False,
        allow_null=True,
    )
    current_session = SessionSerializer(read_only=True)
    current_session_id = serializers.PrimaryKeyRelatedField(
        queryset=AcademicSession.objects.all(),
        source="current_session",
        write_only=True,
        required=False,
        allow_null=True,
    )

    class Meta:
        model = School
        fields = [
            "id",
            "name",
            "abbreviation",
            "email",
            "email_verified",
            "class_system",
            "logo",
            "logo_id",
            "current_session",
            "current_session_id",
            "created_at",
            "updated_at",
        ]
        # The abbreviation prefixes every issued id, so it is set once at signup.
        read_only_fields = ["id", "abbreviation", "created_at", "updated_at"]


# ---------------------------------------------------------------------------
# Teachers
# ---------------------------------------------------------------------------


class TeacherSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    is_active = serializers.BooleanField(source="user.is_active", read_only=True)
    full_name = serializers.CharField(read_only=True)
    school_class = SchoolClassSerializer(read_only=True)

    class Meta:
        model = Teacher
        fields = [
            "id",
            "teacher_id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "school_class",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class TeacherCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})
    first_name = serializers.CharField(max_length=150)
    last_name = serializers.CharField(max_length=150)
    school_class = serializers.PrimaryKeyRelatedField(
        queryset=SchoolClass.objects.all(), required=False, allow_null=True
    )

    def validate_email(self, value: str) -> str:
        return value.lower().strip()

    def validate_password(self, value: str) -> str:
        return _validate_password_strength(value)


class TeacherUpdateSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False)
    school_class = serializers.PrimaryKeyRelatedField(
        queryset=SchoolClass.objects.all(), required=False, allow_null=True
    )


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------


class StudentSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    school_class = SchoolClassSerializer(read_only=True)

    class Meta:
        model = Student
        fields = [
            "id",
            "student_id",
            "first_name",
            "last_name",
            "full_name",
            "date_of_birth",
            "gender",
            "school_class",
            "guardian_name",
            "guardian_phone_number",
            "guardian_relationship",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "student_id", "created_at", "updated_at"]


class StudentWriteSerializer(serializers.ModelSerializer):
    """`student_id` and `school` are assigned by the service, never by the client."""

    class Meta:
        model = Student
        fields = [
            "first_name",
            "last_name",
            "date_of_birth",
            "gender",
            "school_class",
            "guardian_name",
            "guardian_phone_number",
            "guardian_relationship",
        ]


# ---------------------------------------------------------------------------
# Oversight
# ---------------------------------------------------------------------------


class AssessmentOversightSerializer(serializers.ModelSerializer):
    teacher_name = serializers.CharField(source="teacher.full_name", read_only=True, default=None)
    assigned_count = serializers.IntegerField(read_only=True, default=0)
    graded_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Assessment
        fields = [
            "id",
            "name",
            "teacher_name",
            "code",
            "status",
            "opens_at",
            "closes_at",
            "assigned_count",
            "graded_count",
            "created_at",
        ]
        read_only_fields = fields


class SchoolOverviewSerializer(serializers.Serializer):
    """Read-only dashboard payload; declared for the OpenAPI schema."""

    students = serializers.IntegerField()
    teachers = serializers.IntegerField()
    assessments = serializers.IntegerField()
    active_assessments = serializers.IntegerField()
    assessment_status_breakdown = serializers.DictField(child=serializers.IntegerField())
    average_graded_score = serializers.DecimalField(max_digits=7, decimal_places=2, allow_null=True)
    current_session = serializers.CharField(allow_null=True)
