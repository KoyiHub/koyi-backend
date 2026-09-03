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

from apps.assessments.models import Assessment
from apps.common.permissions import acting_school
from apps.school_portal.authentication import SchoolTokenObtainPairSerializer
from apps.school_portal.permissions import IsSchoolAdmin, IsVerifiedSchoolAdmin, SchoolScopedMixin
from apps.school_portal.repositories import (
    AssessmentOversightRepository,
    ReferenceDataRepository,
    SchoolClassRepository,
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
    StudentManagementService,
    TeacherManagementService,
)
from apps.schools.models import SchoolClass, Student, Teacher

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
        return Response(SchoolSerializer(school).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=SCHOOL_AUTH_TAG)
class SchoolLoginView(TokenObtainPairView):
    """Exchange school credentials for a token pair. Teachers are refused here."""

    serializer_class = SchoolTokenObtainPairSerializer
    # `TokenViewBase` types this as an empty tuple; overriding it is the
    # documented way to open the endpoint.
    permission_classes = (AllowAny,)  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@extend_schema(tags=SCHOOL_TAG)
class SchoolProfileView(generics.RetrieveUpdateAPIView):
    """Read or update the acting school's own record."""

    serializer_class = SchoolSerializer
    permission_classes = [IsSchoolAdmin]

    def get_object(self):
        return acting_school(self.request)

    def perform_update(self, serializer) -> None:
        # The service, not the serializer, owns the write — reattach the saved
        # row so the response renders what actually landed in the database.
        service = SchoolProfileService(acting_school(self.request))
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
    """The acting school's own classes."""

    serializer_class = SchoolClassSerializer
    # Declared so schema generation can read the model without executing
    # `get_queryset`, which needs an authenticated school.
    queryset = SchoolClass.objects.none()
    permission_classes = [IsSchoolAdmin]
    pagination_class = None

    def get_queryset(self):
        return SchoolClassRepository(acting_school(self.request)).all()


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
    queryset = Teacher.objects.none()
    permission_classes = [IsSchoolAdmin]

    def get_permissions(self):
        # Reading staff is harmless; minting a login for one is not.
        if self.request.method == "POST":
            return [IsVerifiedSchoolAdmin()]
        return super().get_permissions()

    @property
    def service(self) -> TeacherManagementService:
        return TeacherManagementService(acting_school(self.request))

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
        return TeacherManagementService(acting_school(self.request))

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
    queryset = Student.objects.none()
    permission_classes = [IsSchoolAdmin]

    @property
    def service(self) -> StudentManagementService:
        return StudentManagementService(acting_school(self.request))

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
        return StudentManagementService(acting_school(self.request))

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
    queryset = Assessment.objects.none()

    def get_queryset(self):
        # Already tenant-scoped by the repository; the mixin's `school`
        # property is what supplies it.
        return AssessmentOversightRepository(self.school).with_result_counts()


@extend_schema(tags=SCHOOL_TAG, responses={200: SchoolOverviewSerializer})
class SchoolOverviewView(APIView):
    permission_classes = [IsSchoolAdmin]
    serializer_class = SchoolOverviewSerializer

    def get(self, request: Request) -> Response:
        summary = SchoolOverviewService(acting_school(request)).summary()
        return Response(SchoolOverviewSerializer(summary).data)
