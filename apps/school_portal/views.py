"""HTTP layer for the school management dashboard.

Views do three things and no more: check permissions (via the mixins), hand
validated input to a service, and render the result. No view filters by a
school id taken from the request — `SchoolScopedMixin` supplies the tenant.
"""

from typing import Any

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.activities.models import Activity
from apps.assessments.models import Assessment
from apps.common.pagination import TimestampCursorPagination
from apps.common.permissions import acting_school, acting_user
from apps.school_portal.permissions import IsSchoolAdmin, IsVerifiedSchoolAdmin, SchoolScopedMixin
from apps.school_portal.repositories import (
    AssessmentOversightRepository,
    ReferenceDataRepository,
)
from apps.school_portal.serializers import (
    AcceptedSerializer,
    ActivityFilterSerializer,
    ActivitySerializer,
    AssessmentOversightSerializer,
    ChallengeCodeSerializer,
    ClassTransferSerializer,
    ConfirmationCodeSerializer,
    EmailCodeSerializer,
    EmailOnlySerializer,
    GradeSerializer,
    LoginChallengeSerializer,
    LoginStartSerializer,
    PasswordChangeSerializer,
    PasswordResetConfirmSerializer,
    RegistrationStartedSerializer,
    ResetTokenSerializer,
    SchoolClassSerializer,
    SchoolClassWriteSerializer,
    SchoolOverviewSerializer,
    SchoolRegistrationSerializer,
    SchoolSerializer,
    SessionSerializer,
    StudentFLNSerializer,
    StudentSerializer,
    StudentTransferSerializer,
    StudentWriteSerializer,
    TeacherCreateSerializer,
    TeacherSerializer,
    TeacherUpdateSerializer,
    TransferResultSerializer,
)
from apps.school_portal.services import (
    ActivityFeedService,
    SchoolAuthService,
    SchoolClassManagementService,
    SchoolOverviewService,
    SchoolProfileService,
    StudentManagementService,
    TeacherManagementService,
)
from apps.schools.models import SchoolClass, Student, Teacher

SCHOOL_AUTH_TAG = ["school: auth"]
SCHOOL_TAG = ["school"]


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class AuthView(APIView):
    """Base for the unauthenticated half of the school surface.

    Every one of these endpoints takes an email address or a code, so every one
    of them is a place someone could sit and guess. They share a throttle scope
    for that reason - rate limiting is what makes a six-digit code safe, so it
    is load-bearing here rather than hygiene.
    """

    permission_classes = [AllowAny]
    throttle_scope = "school_auth"
    throttle_classes = [ScopedRateThrottle]
    #: Set by each subclass. Declared here so `validated` can find it.
    serializer_class: Any = None

    @property
    def auth(self) -> SchoolAuthService:
        return SchoolAuthService()

    def validated(self, request: Request) -> dict:
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        return dict(serializer.validated_data)

    def get_serializer_class(self):
        return self.serializer_class


@extend_schema(tags=SCHOOL_AUTH_TAG, responses={201: RegistrationStartedSerializer})
class SchoolRegisterView(AuthView):
    """Register a school, then email the code that activates it.

    No tokens come back here. The account exists but cannot be used until the
    code arrives and is returned, which is what keeps a school record from
    being created against an address its owner does not read.
    """

    serializer_class = SchoolRegistrationSerializer

    def post(self, request: Request) -> Response:
        data = self.validated(request)
        data.pop("password_confirm")
        school = self.auth.register(**data)
        return Response(
            RegistrationStartedSerializer(
                {"id": school.pk, "email": school.email, "otp_sent": True}
            ).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=SCHOOL_AUTH_TAG, responses={200: None})
class SchoolRegisterVerifyView(AuthView):
    """Spend the registration code. Returns the token pair."""

    serializer_class = EmailCodeSerializer

    def post(self, request: Request) -> Response:
        data = self.validated(request)
        return Response(self.auth.verify_registration(**data))


@extend_schema(tags=SCHOOL_AUTH_TAG, responses={200: AcceptedSerializer})
class SchoolResendCodeView(AuthView):
    """Send the registration code again.

    Answers the same way whether or not the address is registered, so the form
    cannot be used to find out which schools use Koyi. The copy on it should
    say "if that address is registered, a code is on its way".
    """

    serializer_class = EmailOnlySerializer

    def post(self, request: Request) -> Response:
        self.auth.resend_registration_code(**self.validated(request))
        return Response(
            AcceptedSerializer(
                {"detail": "If that address is registered, a code is on its way."}
            ).data
        )


@extend_schema(tags=SCHOOL_AUTH_TAG, responses={200: LoginChallengeSerializer})
class SchoolLoginView(AuthView):
    """Step one of signing in: the password, and a code to the address on file.

    Teachers are refused here, and refused with the same message as a wrong
    password - a teacher who posts to the school door must not learn that they
    got the credentials right.
    """

    serializer_class = LoginStartSerializer

    def post(self, request: Request) -> Response:
        issued = self.auth.start_login(**self.validated(request))
        return Response(
            LoginChallengeSerializer(
                {
                    "otp_required": True,
                    "challenge": issued.challenge,
                    "expires_at": issued.expires_at,
                }
            ).data
        )


@extend_schema(tags=SCHOOL_AUTH_TAG, responses={200: None})
class SchoolLoginVerifyView(AuthView):
    """Step two: the code, and the token pair."""

    serializer_class = ChallengeCodeSerializer

    def post(self, request: Request) -> Response:
        return Response(self.auth.complete_login(**self.validated(request)))


@extend_schema(tags=SCHOOL_AUTH_TAG, responses={200: AcceptedSerializer})
class PasswordResetRequestView(AuthView):
    """Ask for a reset code. Always reports success."""

    serializer_class = EmailOnlySerializer

    def post(self, request: Request) -> Response:
        self.auth.request_password_reset(**self.validated(request))
        return Response(
            AcceptedSerializer(
                {"detail": "If that address is registered, a code is on its way."}
            ).data
        )


@extend_schema(tags=SCHOOL_AUTH_TAG, responses={200: ResetTokenSerializer})
class PasswordResetVerifyView(AuthView):
    """Spend the code for a token that sets a password.

    Two steps rather than one because the six digits arrive in a notification
    anyone glancing at the phone can read, and the token that follows does not.
    """

    serializer_class = EmailCodeSerializer

    def post(self, request: Request) -> Response:
        issued = self.auth.verify_password_reset(**self.validated(request))
        return Response(
            ResetTokenSerializer(
                {"reset_token": issued.challenge, "expires_at": issued.expires_at}
            ).data
        )


@extend_schema(tags=SCHOOL_AUTH_TAG, responses={204: None})
class PasswordResetConfirmView(AuthView):
    serializer_class = PasswordResetConfirmSerializer

    def post(self, request: Request) -> Response:
        data = self.validated(request)
        self.auth.confirm_password_reset(reset_token=data["reset_token"], password=data["password"])
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=SCHOOL_TAG, responses={204: None})
class SchoolPasswordChangeView(APIView):
    """Change your own password while signed in."""

    permission_classes = [IsSchoolAdmin]
    serializer_class = PasswordChangeSerializer

    def post(self, request: Request) -> Response:
        serializer = PasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        SchoolAuthService().change_password(user=acting_user(request), **serializer.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)


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
class SchoolClassListCreateView(generics.ListCreateAPIView):
    """The acting school's own classes.

    Grades are ours and classes are theirs: a school picks a grade from our
    list and names its own arm within it, so the picker is a grade dropdown
    plus a free-text arm rather than one flat list.
    """

    serializer_class = SchoolClassSerializer
    # Declared so schema generation can read the model without executing
    # `get_queryset`, which needs an authenticated school.
    queryset = SchoolClass.objects.none()
    permission_classes = [IsSchoolAdmin]
    pagination_class = None

    @property
    def service(self) -> SchoolClassManagementService:
        return SchoolClassManagementService(acting_school(self.request))

    def get_queryset(self):
        return self.service.list()

    @extend_schema(request=SchoolClassWriteSerializer, responses={201: SchoolClassSerializer})
    def create(self, request: Request, *_args, **_kwargs) -> Response:
        serializer = SchoolClassWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        school_class = self.service.create(grade_id=data["grade"].pk, name=data["name"])
        return Response(SchoolClassSerializer(school_class).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=SCHOOL_TAG, responses={204: None})
class SchoolClassDetailView(APIView):
    """Deleting a class is refused while anyone is still in it.

    The dashboard should offer "transfer these students first" rather than a
    delete button that fails.
    """

    permission_classes = [IsSchoolAdmin]
    serializer_class = SchoolClassSerializer

    @property
    def service(self) -> SchoolClassManagementService:
        return SchoolClassManagementService(acting_school(self.request))

    def delete(self, request: Request, pk) -> Response:  # noqa: ARG002
        self.service.delete(self.service.get(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


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


@extend_schema(tags=SCHOOL_TAG, responses={200: TeacherSerializer})
class TeacherActiveView(APIView):
    """Revoke or restore a teacher's login.

    Almost always what "remove a teacher" should mean: nothing they authored
    moves, and the day they come back it is one click to undo.
    """

    permission_classes = [IsSchoolAdmin]
    serializer_class = TeacherSerializer
    active = True

    def post(self, request: Request, pk) -> Response:
        service = TeacherManagementService(acting_school(request))
        teacher = service.set_active(
            service.get(pk), active=self.active, actor=acting_user(request)
        )
        return Response(TeacherSerializer(teacher).data)


@extend_schema(tags=SCHOOL_TAG, responses={200: AcceptedSerializer})
class TeacherPasswordResetView(APIView):
    """Email a teacher a code they can set a new password with."""

    permission_classes = [IsSchoolAdmin]
    serializer_class = AcceptedSerializer

    def post(self, request: Request, pk) -> Response:
        service = TeacherManagementService(acting_school(request))
        service.send_password_reset(service.get(pk))
        return Response(AcceptedSerializer({"detail": "A reset code is on its way."}).data)


@extend_schema(tags=SCHOOL_TAG, responses={200: AcceptedSerializer})
class TeacherDeleteRequestView(APIView):
    """Start a deletion by sending a code to the administrator's own address."""

    permission_classes = [IsSchoolAdmin]
    serializer_class = AcceptedSerializer

    def post(self, request: Request, pk) -> Response:
        service = TeacherManagementService(acting_school(request))
        service.request_delete(service.get(pk), actor=acting_user(request))
        return Response(
            AcceptedSerializer({"detail": "A confirmation code has been emailed to you."}).data
        )


@extend_schema(tags=SCHOOL_TAG, request=ConfirmationCodeSerializer, responses={204: None})
class TeacherDeleteConfirmView(APIView):
    permission_classes = [IsSchoolAdmin]
    serializer_class = ConfirmationCodeSerializer
    # Guessing a deletion code is guessing six digits from inside a signed-in
    # session, which the user throttle alone is far too generous for.
    throttle_scope = "school_auth"
    throttle_classes = [ScopedRateThrottle]

    def post(self, request: Request, pk) -> Response:
        serializer = ConfirmationCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service = TeacherManagementService(acting_school(request))
        service.delete(
            service.get(pk), actor=acting_user(request), code=serializer.validated_data["code"]
        )
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


@extend_schema(tags=SCHOOL_TAG, responses={200: StudentSerializer})
class StudentActiveView(APIView):
    """Take a child off the active roll, or put them back on it.

    A child holds no login, so this is a flag rather than a revoked
    credential. Re-enabling needs a class: an active student always has one.
    """

    permission_classes = [IsSchoolAdmin]
    serializer_class = StudentSerializer
    active = True

    def post(self, request: Request, pk) -> Response:
        service = StudentManagementService(acting_school(request))
        student = service.set_active(
            service.get(pk), active=self.active, actor=acting_user(request)
        )
        return Response(StudentSerializer(student).data)


@extend_schema(tags=SCHOOL_TAG, responses={200: StudentFLNSerializer})
class StudentFLNView(APIView):
    """Levels and scores for one child.

    Not the diagnostic breakdown - which subskills are weak, and what to do
    about them, is the teacher's view of the same child.
    """

    permission_classes = [IsSchoolAdmin]
    serializer_class = StudentFLNSerializer

    def get(self, request: Request, pk) -> Response:
        service = StudentManagementService(acting_school(request))
        return Response(StudentFLNSerializer(service.fln(service.get(pk))).data)


@extend_schema(tags=SCHOOL_TAG, responses={200: AcceptedSerializer})
class StudentDeleteRequestView(APIView):
    permission_classes = [IsSchoolAdmin]
    serializer_class = AcceptedSerializer

    def post(self, request: Request, pk) -> Response:
        service = StudentManagementService(acting_school(request))
        service.request_delete(service.get(pk), actor=acting_user(request))
        return Response(
            AcceptedSerializer({"detail": "A confirmation code has been emailed to you."}).data
        )


@extend_schema(tags=SCHOOL_TAG, request=ConfirmationCodeSerializer, responses={204: None})
class StudentDeleteConfirmView(APIView):
    permission_classes = [IsSchoolAdmin]
    serializer_class = ConfirmationCodeSerializer
    # Guessing a deletion code is guessing six digits from inside a signed-in
    # session, which the user throttle alone is far too generous for.
    throttle_scope = "school_auth"
    throttle_classes = [ScopedRateThrottle]

    def post(self, request: Request, pk) -> Response:
        serializer = ConfirmationCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        service = StudentManagementService(acting_school(request))
        service.delete(
            service.get(pk), actor=acting_user(request), code=serializer.validated_data["code"]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=SCHOOL_TAG, request=StudentTransferSerializer, responses={200: TransferResultSerializer}
)
class StudentTransferView(APIView):
    """Move named children into one class."""

    permission_classes = [IsSchoolAdmin]
    serializer_class = StudentTransferSerializer

    def post(self, request: Request) -> Response:
        serializer = StudentTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        service = StudentManagementService(acting_school(request))
        moved = service.transfer(
            student_ids=data["student_ids"], to_class=data["to_class"], actor=acting_user(request)
        )
        return Response(
            TransferResultSerializer({"moved": len(moved), "to_class": data["to_class"]}).data
        )


@extend_schema(
    tags=SCHOOL_TAG, request=ClassTransferSerializer, responses={200: TransferResultSerializer}
)
class ClassTransferView(APIView):
    """Move a whole class at once - the end-of-year move.

    Recorded as one entry in the activity feed rather than one per child: forty
    rows saying the same thing would bury everything else, and what happened
    really was a single act.
    """

    permission_classes = [IsSchoolAdmin]
    serializer_class = ClassTransferSerializer

    def post(self, request: Request) -> Response:
        serializer = ClassTransferSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        service = StudentManagementService(acting_school(request))
        moved = service.transfer_class(
            from_class=data["from_class"], to_class=data["to_class"], actor=acting_user(request)
        )
        return Response(
            TransferResultSerializer({"moved": moved, "to_class": data["to_class"]}).data
        )


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------


class ActivityCursorPagination(TimestampCursorPagination):
    """Ordered by when the thing happened, not by when the row was written.

    Those differ for anything recorded after the fact, and a feed that claims
    to be chronological should be ordered by the clock a person recognises.
    """

    ordering = "-occurred_at"


@extend_schema(
    tags=SCHOOL_TAG,
    parameters=[
        OpenApiParameter("teacher", str),
        OpenApiParameter("student", str),
        OpenApiParameter("school_class", str),
        OpenApiParameter("action", str),
        OpenApiParameter("occurred_from", str),
        OpenApiParameter("occurred_to", str),
    ],
)
class ActivityFeedView(SchoolScopedMixin, generics.ListAPIView):
    """Every core action in the school, newest first.

    Cursor-paginated rather than page-numbered: rows land in this table while
    someone is reading it, and an offset would quietly skip or repeat entries
    as the feed grows underneath them.
    """

    serializer_class = ActivitySerializer
    queryset = Activity.objects.none()
    pagination_class = ActivityCursorPagination

    def get_queryset(self):
        filters = ActivityFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        data = filters.validated_data
        # Already tenant-scoped by the repository the service composes.
        return ActivityFeedService(self.school).feed(
            teacher_id=data.get("teacher"),
            student_id=data.get("student"),
            school_class_id=data.get("school_class"),
            action=data.get("action", ""),
            occurred_from=data.get("occurred_from"),
            occurred_to=data.get("occurred_to"),
        )


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
