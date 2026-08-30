"""HTTP layer for the school management dashboard.

Views do three things and no more: check permissions (via the mixins), hand
validated input to a service, and render the result. No view filters by a
school id taken from the request — `SchoolScopedMixin` supplies the tenant.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.common.enums import UserRole
from apps.common.services import ValidationError
from apps.school_portal.authentication import (
    SchoolTokenObtainPairSerializer,
    TeacherTokenObtainPairSerializer,
)
from apps.school_portal.permissions import IsSchoolAdmin, IsVerifiedSchoolAdmin, SchoolScopedMixin
from apps.school_portal.repositories import (
    AssessmentOversightRepository,
    ReferenceDataRepository,
)
from apps.school_portal.serializers import (
    AssessmentOversightSerializer,
    GradeSerializer,
    SchoolClassSerializer,
    SchoolOverviewSerializer,
    SchoolRegistrationSerializer,
    SchoolSerializer,
    SessionSerializer,
    StudentSerializer,
    StudentWriteSerializer,
    TeacherCreateSerializer,
    TeacherSerializer,
    TeacherUpdateSerializer,
)
from apps.school_portal.services import (
    SchoolOverviewService,
    SchoolProfileService,
    SchoolRegistrationService,
    SchoolVerificationService,
    StudentManagementService,
    TeacherManagementService,
)
from apps.users.models import User

SCHOOL_AUTH_TAG = ["school: auth"]
SCHOOL_TAG = ["school"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@extend_schema(tags=SCHOOL_AUTH_TAG)
class SchoolRegisterView(generics.CreateAPIView):
    """Register a school and its management login."""

    serializer_class = SchoolRegistrationSerializer
    permission_classes = [AllowAny]

    def create(self, request: Request, *_args, **_kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        data.pop("password_confirm")
        school = SchoolRegistrationService().register(**data)
        response_data = SchoolSerializer(school).data
        return Response(response_data, status=status.HTTP_201_CREATED)


@extend_schema(tags=SCHOOL_AUTH_TAG)
class SchoolVerifyEmailView(APIView):
    """Confirm a school email through the signed link sent at registration."""

    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        token = request.query_params.get("token")
        if not token:
            raise ValidationError("Verification token is required.")
        user = SchoolVerificationService.verify_email(token)
        return Response(
            {
                "message": "Email verified successfully.",
                "email": user.email,
                "email_verified": True,
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request: Request) -> Response:
        token = request.data.get("token")
        if not token:
            raise ValidationError("Verification token is required.")
        user = SchoolVerificationService.verify_email(token)
        return Response(
            {
                "message": "Email verified successfully.",
                "email": user.email,
                "email_verified": True,
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=SCHOOL_AUTH_TAG)
class SchoolResendVerificationView(APIView):
    """Resend the school verification email with a short throttle window."""

    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        email = (
            (request.data.get("email") or request.query_params.get("email") or "").strip().lower()
        )
        if not email:
            raise ValidationError("Email address is required.")

        user = User.objects.filter(email__iexact=email).first()
        if user is None or user.role != UserRole.SCHOOL:
            raise ValidationError("No school account was found for that email.")

        SchoolVerificationService.send_verification_email(user)
        return Response(
            {"message": "Verification email sent.", "email": user.email},
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=SCHOOL_AUTH_TAG)
class SchoolLoginView(TokenObtainPairView):
    """Exchange school credentials for a token pair. Teachers are refused here."""

    serializer_class = SchoolTokenObtainPairSerializer
    permission_classes = [AllowAny]


@extend_schema(tags=SCHOOL_AUTH_TAG)
class TeacherLoginView(TokenObtainPairView):
    """Exchange a teacher id, school id, and password for a token pair."""

    serializer_class = TeacherTokenObtainPairSerializer
    permission_classes = [AllowAny]


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@extend_schema(tags=SCHOOL_TAG)
class SchoolProfileView(generics.RetrieveUpdateAPIView):
    """Read or update the acting school's own record."""

    serializer_class = SchoolSerializer
    permission_classes = [IsSchoolAdmin]

    def get_object(self):
        return self.request.user.school

    def perform_update(self, serializer) -> None:
        # The service, not the serializer, owns the write — reattach the saved
        # row so the response renders what actually landed in the database.
        service = SchoolProfileService(self.request.user.school)
        serializer.instance = service.update(**serializer.validated_data)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


@extend_schema(tags=SCHOOL_TAG)
class GradeListView(generics.ListAPIView):
    serializer_class = GradeSerializer
    permission_classes = [IsSchoolAdmin]
    pagination_class = None

    def get_queryset(self):
        return ReferenceDataRepository().grades()


@extend_schema(tags=SCHOOL_TAG)
class SchoolClassListView(generics.ListAPIView):
    serializer_class = SchoolClassSerializer
    permission_classes = [IsSchoolAdmin]
    pagination_class = None

    def get_queryset(self):
        return ReferenceDataRepository().classes()


@extend_schema(tags=SCHOOL_TAG)
class SessionListView(generics.ListAPIView):
    serializer_class = SessionSerializer
    permission_classes = [IsSchoolAdmin]
    pagination_class = None

    def get_queryset(self):
        return ReferenceDataRepository().sessions()


# ---------------------------------------------------------------------------
# Teachers
# ---------------------------------------------------------------------------


@extend_schema(tags=SCHOOL_TAG)
class TeacherListCreateView(generics.ListCreateAPIView):
    serializer_class = TeacherSerializer
    permission_classes = [IsSchoolAdmin]

    def get_permissions(self):
        # Reading staff is harmless; minting a login for one is not.
        if self.request.method == "POST":
            return [IsVerifiedSchoolAdmin()]
        return super().get_permissions()

    @property
    def service(self) -> TeacherManagementService:
        return TeacherManagementService(self.request.user.school)

    def get_queryset(self):
        return self.service.list(
            search=self.request.query_params.get("search", ""),
            school_class_id=self.request.query_params.get("school_class"),
        )

    @extend_schema(request=TeacherCreateSerializer, responses={201: TeacherSerializer})
    def create(self, request: Request, *_args, **_kwargs) -> Response:
        serializer = TeacherCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        teacher = self.service.create(**serializer.validated_data)
        return Response(TeacherSerializer(teacher).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=SCHOOL_TAG)
class TeacherDetailView(APIView):
    permission_classes = [IsSchoolAdmin]
    serializer_class = TeacherSerializer

    @property
    def service(self) -> TeacherManagementService:
        return TeacherManagementService(self.request.user.school)

    @extend_schema(responses={200: TeacherSerializer})
    def get(self, request: Request, pk) -> Response:  # noqa: ARG002
        return Response(TeacherSerializer(self.service.get(pk)).data)

    @extend_schema(request=TeacherUpdateSerializer, responses={200: TeacherSerializer})
    def patch(self, request: Request, pk) -> Response:
        serializer = TeacherUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        teacher = self.service.update(self.service.get(pk), **serializer.validated_data)
        return Response(TeacherSerializer(teacher).data)

    @extend_schema(responses={204: None})
    def delete(self, request: Request, pk) -> Response:  # noqa: ARG002
        # Deactivates rather than deletes: assessments this teacher authored
        # must survive them leaving the school.
        self.service.deactivate(self.service.get(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------


@extend_schema(tags=SCHOOL_TAG)
class StudentListCreateView(generics.ListCreateAPIView):
    serializer_class = StudentSerializer
    permission_classes = [IsSchoolAdmin]

    @property
    def service(self) -> StudentManagementService:
        return StudentManagementService(self.request.user.school)

    def get_queryset(self):
        return self.service.list(
            search=self.request.query_params.get("search", ""),
            school_class_id=self.request.query_params.get("school_class"),
        )

    @extend_schema(request=StudentWriteSerializer, responses={201: StudentSerializer})
    def create(self, request: Request, *_args, **_kwargs) -> Response:
        serializer = StudentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = self.service.create(**serializer.validated_data)
        return Response(StudentSerializer(student).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=SCHOOL_TAG)
class StudentDetailView(APIView):
    permission_classes = [IsSchoolAdmin]
    serializer_class = StudentSerializer

    @property
    def service(self) -> StudentManagementService:
        return StudentManagementService(self.request.user.school)

    @extend_schema(responses={200: StudentSerializer})
    def get(self, request: Request, pk) -> Response:  # noqa: ARG002
        return Response(StudentSerializer(self.service.get(pk)).data)

    @extend_schema(request=StudentWriteSerializer, responses={200: StudentSerializer})
    def patch(self, request: Request, pk) -> Response:
        serializer = StudentWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        student = self.service.update(self.service.get(pk), **serializer.validated_data)
        return Response(StudentSerializer(student).data)

    @extend_schema(responses={204: None})
    def delete(self, request: Request, pk) -> Response:  # noqa: ARG002
        self.service.delete(self.service.get(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Oversight
# ---------------------------------------------------------------------------


@extend_schema(tags=SCHOOL_TAG)
class AssessmentOversightListView(SchoolScopedMixin, generics.ListAPIView):
    """Every assessment in the school, whoever set it."""

    serializer_class = AssessmentOversightSerializer

    def get_queryset(self):
        # Already tenant-scoped by the repository; the mixin's `school`
        # property is what supplies it.
        return AssessmentOversightRepository(self.school).with_result_counts()


@extend_schema(tags=SCHOOL_TAG, responses={200: SchoolOverviewSerializer})
class SchoolOverviewView(APIView):
    permission_classes = [IsSchoolAdmin]
    serializer_class = SchoolOverviewSerializer

    def get(self, request: Request) -> Response:
        summary = SchoolOverviewService(request.user.school).summary()
        return Response(SchoolOverviewSerializer(summary).data)
