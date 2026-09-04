"""The assessment runner.

A child types the paper's code and their own student id once, gets back a
session, and works through the sections. Every view below reads the assignment
off that session — none of them accept an assessment or student id from the
client, so a session cannot be pointed at another child's paper.
"""

from functools import cached_property

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.assessments.models import AssessmentQuestion
from apps.assessments.services import AssessmentAccessService, AssessmentSittingService
from apps.common.services import NotFoundError
from apps.media_assets.models import MediaAsset
from apps.student_portal.serializers import (
    SaveResponseSerializer,
    SectionProgressSerializer,
    SittingOverviewSerializer,
    SittingQuestionSerializer,
    VerifySerializer,
)
from apps.student_portal.sessions import (
    SESSION_HEADER,
    SESSION_HEADER_NAME,
    open_session,
    resolve_session,
)

STUDENT_TAG = ["student: assessment"]


class SittingView(APIView):
    """Base for every view inside a sitting.

    There is no authentication class and no permission class, because there is
    no user to authenticate. The session code names one assignment; holding it
    is the whole authorisation, and nothing in a request body can widen it.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get_authenticate_header(self, request: Request) -> str:  # noqa: ARG002
        """Makes a missing session a 401 rather than a 403.

        DRF downgrades to 403 when no authenticator can offer a challenge, and
        with no authentication classes there is none. The distinction matters
        to the client: "type the code again" is not the same as "you may not
        do this".
        """
        return SESSION_HEADER_NAME

    @cached_property
    def assignment(self):
        assignment = resolve_session(self.request.META.get(SESSION_HEADER, ""))
        if assignment is None:
            # Unknown, expired and withdrawn all look the same from outside.
            raise NotAuthenticated("Start the assessment again to continue.")
        return assignment

    @property
    def sitting(self) -> AssessmentSittingService:
        return AssessmentSittingService(self.assignment)

    def overview(self) -> dict:
        assignment = self.assignment
        return {
            "assessment_id": assignment.assessment_id,
            "name": assignment.assessment.name,
            "instructions": assignment.assessment.instructions,
            "code": assignment.assessment.code,
            "student_name": assignment.student.full_name,
            "status": assignment.status,
            "sections": SectionProgressSerializer(self.sitting.section_results(), many=True).data,
        }


@extend_schema(tags=STUDENT_TAG)
class VerifyView(APIView):
    """Open a paper with its code and the child's personal one.

    Throttled, and that matters: this is the only unauthenticated way into a
    sitting, and the personal code is short enough for a child to type. The
    rate limit is what makes that length safe.

    Every failure returns the same message, so the form cannot be used to
    discover which codes are real.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_scope = "sitting_verify"
    throttle_classes = [ScopedRateThrottle]
    serializer_class = VerifySerializer

    @extend_schema(request=VerifySerializer, responses={200: SittingOverviewSerializer})
    def post(self, request: Request) -> Response:
        serializer = VerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assignment = AssessmentAccessService().verify(**serializer.validated_data)
        session = open_session(assignment)

        sitting = AssessmentSittingService(assignment)
        return Response(
            {
                "session": session.code,
                "expires_at": session.expires_at,
                "assessment": {
                    "assessment_id": str(assignment.assessment_id),
                    "name": assignment.assessment.name,
                    "instructions": assignment.assessment.instructions,
                    "code": assignment.assessment.code,
                    "student_name": assignment.student.full_name,
                    "status": assignment.status,
                    "sections": SectionProgressSerializer(
                        sitting.section_results(), many=True
                    ).data,
                },
            }
        )


@extend_schema(tags=STUDENT_TAG, responses={200: SittingOverviewSerializer})
class OverviewView(SittingView):
    """The instruction page: sections in order, and which one is open."""

    serializer_class = SittingOverviewSerializer

    def get(self, request: Request) -> Response:  # noqa: ARG002
        return Response(self.overview())


@extend_schema(tags=STUDENT_TAG)
class StartSectionView(SittingView):
    """Open a section and hand back its questions."""

    serializer_class = SittingQuestionSerializer

    @extend_schema(responses={200: SittingQuestionSerializer(many=True)})
    def post(self, request: Request, section_id) -> Response:  # noqa: ARG002
        row = self.sitting.start_section(section_id)
        questions = (
            AssessmentQuestion.objects.filter(section_id=section_id)
            .select_related("subskill")
            .prefetch_related("contents__media", "options__media")
            .order_by("order")
        )
        return Response(
            {
                "section": SectionProgressSerializer(row).data,
                "questions": SittingQuestionSerializer(questions, many=True).data,
            }
        )


@extend_schema(tags=STUDENT_TAG)
class SaveResponseView(SittingView):
    """Save one answer. Sent on every change, so it upserts."""

    serializer_class = SaveResponseSerializer

    @extend_schema(request=SaveResponseSerializer, responses={200: None})
    def put(self, request: Request, question_id) -> Response:
        serializer = SaveResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        question = AssessmentQuestion.objects.filter(
            pk=question_id, assessment_id=self.assignment.assessment_id
        ).first()
        if question is None:
            raise NotFoundError("No such question on this assessment.")

        media = None
        if data["media_id"]:
            media = MediaAsset.objects.filter(pk=data["media_id"]).first()
            if media is None:
                raise NotFoundError("No such media asset.")

        response = self.sitting.save_response(
            question=question,
            text_value=data["text_value"],
            media=media,
            option_ids=data["option_ids"],
        )
        return Response({"id": str(response.pk), "saved": True})


@extend_schema(tags=STUDENT_TAG, responses={200: SittingOverviewSerializer})
class SubmitSectionView(SittingView):
    """Finish a section.

    Submitting the last one finalises the paper on its own — there is no
    further button to press.
    """

    serializer_class = SittingOverviewSerializer

    def post(self, request: Request, section_id) -> Response:  # noqa: ARG002
        self.sitting.submit_section(section_id)
        self.assignment.refresh_from_db()
        return Response(self.overview(), status=status.HTTP_200_OK)
