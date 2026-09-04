"""HTTP layer for the teacher dashboard.

Views check permissions, hand validated input to a service, and render what
comes back. No view filters by a school id taken from the request — the
teacher's own school is what scopes every query.
"""

from typing import TYPE_CHECKING, Any

from django.db.models import Prefetch, Q
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.ai.jobs import explain_assessment, explain_student
from apps.assessments.analytics import (
    AssessmentAnalyticsService,
    RosterService,
    StudentBreakdownService,
)
from apps.assessments.dto import (
    AnswerInput,
    ContentBlockInput,
    CreateAssessmentInput,
    CreateSectionInput,
    OptionInput,
    QuestionInput,
    UpdateAssessmentInput,
    UpdateSectionInput,
)
from apps.assessments.models import (
    Assessment,
    AssessmentQuestion,
    AssessmentQuestionResponse,
    AssessmentResult,
)
from apps.assessments.repositories import (
    AssessmentQuestionRepository,
    AssessmentRepository,
    QuestionBankRepository,
    TaxonomyRepository,
)
from apps.assessments.services import (
    AssessmentAssignmentService,
    AssessmentCoverageService,
    AssessmentDraftService,
    AssessmentPublishService,
)
from apps.common.permissions import IsTeacher, acting_teacher
from apps.common.services import NotFoundError
from apps.curriculum.models import Question, Skill
from apps.schools.models import Student
from apps.teacher_portal.authentication import TeacherLoginSerializer
from apps.teacher_portal.serializers import (
    AssessmentAnalyticsSerializer,
    AssessmentCoverageSerializer,
    AssessmentQuestionSerializer,
    AssessmentRosterSerializer,
    AssessmentSerializer,
    AssessmentUpdateSerializer,
    AssessmentWriteSerializer,
    AssignmentSerializer,
    AssignSerializer,
    BankQuestionSerializer,
    QuestionListWriteSerializer,
    ResponseReviewSerializer,
    RosterEntrySerializer,
    SectionSerializer,
    SectionUpdateSerializer,
    SectionWriteSerializer,
    SkillSerializer,
    StudentBreakdownSerializer,
)

TEACHER_AUTH_TAG = ["teacher: auth"]
TEACHER_TAG = ["teacher"]
BANK_TAG = ["teacher: bank"]


class TeacherViewMixin:
    """Supplies the acting teacher and their school to every view below."""

    # Typed loosely for the same reason as the scoped mixins in
    # `apps.common.permissions`: these come from the DRF view this is mixed
    # into, and narrowing them here clashes with APIView further up the MRO.
    permission_classes: Any = [IsTeacher]

    if TYPE_CHECKING:
        request: Any

    @property
    def teacher(self):
        return acting_teacher(self.request)

    @property
    def school(self):
        return self.teacher.school

    @property
    def drafts(self) -> AssessmentDraftService:
        return AssessmentDraftService(self.school, self.teacher)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@extend_schema(tags=TEACHER_AUTH_TAG)
class TeacherLoginView(APIView):
    """Exchange a teacher id and password for a token pair."""

    permission_classes = [AllowAny]
    serializer_class = TeacherLoginSerializer

    @extend_schema(request=TeacherLoginSerializer, responses={200: None})
    def post(self, request: Request) -> Response:
        serializer = TeacherLoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data)


@extend_schema(tags=TEACHER_AUTH_TAG, responses={200: None})
class TeacherMeView(TeacherViewMixin, APIView):
    """The acting teacher's own record."""

    def get(self, request: Request) -> Response:  # noqa: ARG002
        teacher = self.teacher
        return Response(
            {
                "id": str(teacher.pk),
                "teacher_id": teacher.teacher_id,
                "full_name": teacher.full_name,
                "email": teacher.user.email,
                "school": {"id": str(teacher.school_id), "name": teacher.school.name},
            }
        )


# ---------------------------------------------------------------------------
# Taxonomy and question bank — read-only
# ---------------------------------------------------------------------------


@extend_schema(tags=BANK_TAG)
class SkillListView(TeacherViewMixin, generics.ListAPIView):
    """The skill tree, with each subskill's level range."""

    serializer_class = SkillSerializer
    pagination_class = None
    queryset = Skill.objects.none()

    def get_queryset(self):
        return TaxonomyRepository().skills(domain=self.request.query_params.get("domain", ""))


@extend_schema(tags=BANK_TAG)
class BankQuestionListView(TeacherViewMixin, generics.ListAPIView):
    """Browse the company-authored bank. Read-only by design."""

    serializer_class = BankQuestionSerializer
    queryset = Question.objects.none()

    def get_queryset(self):
        params = self.request.query_params
        return QuestionBankRepository().search(
            domain=params.get("domain", ""),
            skill_id=params.get("skill"),
            subskill_id=params.get("subskill"),
            fln_level=params.get("fln_level") or None,
            question_type=params.get("type", ""),
            term=params.get("search", ""),
        )


@extend_schema(tags=BANK_TAG)
class BankQuestionDetailView(TeacherViewMixin, generics.RetrieveAPIView):
    serializer_class = BankQuestionSerializer
    queryset = Question.objects.none()

    def get_queryset(self):
        return QuestionBankRepository().all()


# ---------------------------------------------------------------------------
# Assessments — draft, then publish
# ---------------------------------------------------------------------------


@extend_schema(tags=TEACHER_TAG)
class AssessmentListCreateView(TeacherViewMixin, generics.ListCreateAPIView):
    serializer_class = AssessmentSerializer
    queryset = Assessment.objects.none()

    def get_queryset(self):
        return AssessmentRepository(self.school).with_sections()

    @extend_schema(request=AssessmentWriteSerializer, responses={201: AssessmentSerializer})
    def create(self, request: Request, *_args, **_kwargs) -> Response:
        serializer = AssessmentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assessment = self.drafts.create(CreateAssessmentInput(**serializer.validated_data))
        return Response(AssessmentSerializer(assessment).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=TEACHER_TAG)
class AssessmentDetailView(TeacherViewMixin, APIView):
    serializer_class = AssessmentSerializer

    @extend_schema(responses={200: AssessmentSerializer})
    def get(self, request: Request, pk) -> Response:  # noqa: ARG002
        return Response(AssessmentSerializer(self.drafts.get(pk)).data)

    @extend_schema(request=AssessmentUpdateSerializer, responses={200: AssessmentSerializer})
    def patch(self, request: Request, pk) -> Response:
        serializer = AssessmentUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        assessment = self.drafts.update(
            self.drafts.get(pk), UpdateAssessmentInput(**serializer.validated_data)
        )
        return Response(AssessmentSerializer(assessment).data)

    @extend_schema(responses={204: None})
    def delete(self, request: Request, pk) -> Response:  # noqa: ARG002
        self.drafts.delete(self.drafts.get(pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=TEACHER_TAG)
class SectionListCreateView(TeacherViewMixin, APIView):
    serializer_class = SectionSerializer

    @extend_schema(responses={200: SectionSerializer(many=True)})
    def get(self, request: Request, pk) -> Response:  # noqa: ARG002
        assessment = self.drafts.get(pk)
        sections = assessment.sections.prefetch_related("covers").all()
        return Response(SectionSerializer(sections, many=True).data)

    @extend_schema(request=SectionWriteSerializer, responses={201: SectionSerializer})
    def post(self, request: Request, pk) -> Response:
        serializer = SectionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        section = self.drafts.add_section(
            self.drafts.get(pk),
            CreateSectionInput(covers=tuple(data.pop("covers", [])), **data),
        )
        return Response(SectionSerializer(section).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=TEACHER_TAG)
class SectionDetailView(TeacherViewMixin, APIView):
    serializer_class = SectionSerializer

    @extend_schema(request=SectionUpdateSerializer, responses={200: SectionSerializer})
    def patch(self, request: Request, pk, section_id) -> Response:
        serializer = SectionUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        assessment = self.drafts.get(pk)
        data = dict(serializer.validated_data)
        covers = data.pop("covers", None)
        section = self.drafts.update_section(
            self.drafts.get_section(assessment, section_id),
            UpdateSectionInput(covers=tuple(covers) if covers is not None else None, **data),
        )
        return Response(SectionSerializer(section).data)

    @extend_schema(responses={204: None})
    def delete(self, request: Request, pk, section_id) -> Response:  # noqa: ARG002
        assessment = self.drafts.get(pk)
        self.drafts.delete_section(self.drafts.get_section(assessment, section_id))
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=TEACHER_TAG)
class SectionQuestionsView(TeacherViewMixin, APIView):
    """The section's questions, replaced wholesale.

    A PUT rather than a POST because the client owns the ordered list: it
    posts the whole thing, so a retry after a dropped connection cannot leave
    the section holding duplicates.
    """

    serializer_class = AssessmentQuestionSerializer

    @extend_schema(responses={200: AssessmentQuestionSerializer(many=True)})
    def get(self, request: Request, pk, section_id) -> Response:  # noqa: ARG002
        section = self.drafts.get_section(self.drafts.get(pk), section_id)
        questions = AssessmentQuestionRepository(section).full()
        return Response(AssessmentQuestionSerializer(questions, many=True).data)

    @extend_schema(
        request=QuestionListWriteSerializer,
        responses={200: AssessmentQuestionSerializer(many=True)},
    )
    def put(self, request: Request, pk, section_id) -> Response:
        serializer = QuestionListWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        section = self.drafts.get_section(self.drafts.get(pk), section_id)
        self.drafts.set_questions(
            section, [_question_input(row) for row in serializer.validated_data["questions"]]
        )
        questions = AssessmentQuestionRepository(section).full()
        return Response(AssessmentQuestionSerializer(questions, many=True).data)


@extend_schema(tags=TEACHER_TAG, responses={200: AssessmentCoverageSerializer})
class AssessmentCoverageView(TeacherViewMixin, APIView):
    """Which (skill, level) cells this paper probes, and what it misses."""

    serializer_class = AssessmentCoverageSerializer

    def get(self, request: Request, pk) -> Response:  # noqa: ARG002
        coverage = AssessmentCoverageService(self.drafts.get(pk)).build()
        return Response(AssessmentCoverageSerializer(coverage).data)


@extend_schema(tags=TEACHER_TAG, responses={200: AssessmentSerializer})
class AssessmentPublishView(TeacherViewMixin, APIView):
    """Validate, mint the code, and lock the paper."""

    serializer_class = AssessmentSerializer

    def post(self, request: Request, pk) -> Response:  # noqa: ARG002
        assessment = AssessmentPublishService(self.school, self.teacher).publish(
            self.drafts.get(pk)
        )
        return Response(AssessmentSerializer(assessment).data)


def _question_input(row: dict) -> QuestionInput:
    """Turn one validated question payload into the service's input type."""
    answer = row.get("answer")
    return QuestionInput(
        subskill_id=row["subskill_id"],
        fln_level=row["fln_level"],
        question_type=row["question_type"],
        text=row["text"],
        layout=row.get("layout", ""),
        description=row.get("description", ""),
        point=row.get("point", 1),
        source_question_id=row.get("source_question_id"),
        contents=tuple(ContentBlockInput(**block) for block in row.get("contents", [])),
        options=tuple(OptionInput(**option) for option in row.get("options", [])),
        answer=AnswerInput(**answer) if answer else None,
    )


@extend_schema(tags=TEACHER_TAG)
class AssignmentListCreateView(TeacherViewMixin, APIView):
    """Who is sitting this paper."""

    serializer_class = AssignmentSerializer

    @extend_schema(responses={200: AssignmentSerializer(many=True)})
    def get(self, request: Request, pk) -> Response:  # noqa: ARG002
        assessment = self.drafts.get(pk)
        assignments = assessment.assignments.select_related(
            "student", "student__school_class__grade"
        ).order_by("student__last_name", "student__first_name")
        return Response(AssignmentSerializer(assignments, many=True).data)

    @extend_schema(request=AssignSerializer, responses={201: AssignmentSerializer(many=True)})
    def post(self, request: Request, pk) -> Response:
        serializer = AssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        assessment = self.drafts.get(pk)

        students = self._resolve_students(serializer.validated_data)
        created = AssessmentAssignmentService(self.school, self.teacher).assign(
            assessment, students=students
        )
        return Response(
            AssignmentSerializer(created, many=True).data, status=status.HTTP_201_CREATED
        )

    def _resolve_students(self, data: dict):
        """Only ever the teacher's own school, whatever the client asked for."""
        queryset = Student.objects.filter(school=self.school, is_active=True)
        if data["all_my_students"]:
            return list(queryset.select_related("school_class"))

        filters = Q()
        if data["student_ids"]:
            filters |= Q(pk__in=data["student_ids"])
        if data["class_ids"]:
            filters |= Q(school_class_id__in=data["class_ids"])
        return list(queryset.filter(filters).select_related("school_class"))


@extend_schema(tags=TEACHER_TAG)
class AssignmentDetailView(TeacherViewMixin, APIView):
    """Withdraw an assignment, while it is still safe to do so."""

    serializer_class = AssignmentSerializer

    @extend_schema(responses={204: None})
    def delete(self, request: Request, pk, assignment_id) -> Response:  # noqa: ARG002
        assessment = self.drafts.get(pk)
        assignment = assessment.assignments.filter(pk=assignment_id).first()
        if assignment is None:
            raise NotFoundError("No such assignment on this assessment.")
        AssessmentAssignmentService(self.school, self.teacher).revoke(assessment, assignment)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=TEACHER_TAG, responses={200: AssessmentRosterSerializer})
class AssignmentRosterView(TeacherViewMixin, APIView):
    """The printable code sheet for one paper.

    Every child's personal code in one place, so the classroom path still
    works now that there is no single code to write on a board.
    """

    serializer_class = AssessmentRosterSerializer

    def get(self, request: Request, pk) -> Response:  # noqa: ARG002
        assessment = self.drafts.get(pk)
        assignments = assessment.assignments.select_related(
            "student", "student__school_class__grade"
        ).order_by("student__last_name", "student__first_name")

        return Response(
            AssessmentRosterSerializer(
                {
                    "assessment_id": assessment.pk,
                    "assessment_name": assessment.name,
                    "assessment_code": assessment.code,
                    "opens_at": assessment.opens_at,
                    "closes_at": assessment.closes_at,
                    "rows": [
                        {
                            "student_name": a.student.full_name,
                            "student_id": a.student.student_id,
                            "school_class": (
                                str(a.student.school_class) if a.student.school_class else None
                            ),
                            "code": a.code,
                            "status": a.status,
                        }
                        for a in assignments
                    ],
                }
            ).data
        )


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@extend_schema(tags=TEACHER_TAG, responses={200: AssessmentAnalyticsSerializer})
class AssessmentAnalyticsView(TeacherViewMixin, APIView):
    """What a paper says about a class.

    The numbers are computed here and now; the narrative is laid over them and
    only appears once it has been generated. `?narrative=false` skips it, which
    is what a dashboard tile should do.
    """

    serializer_class = AssessmentAnalyticsSerializer

    def get(self, request: Request, pk) -> Response:
        assessment = self.drafts.get(pk)
        analytics = AssessmentAnalyticsService(assessment).build()
        payload = AssessmentAnalyticsSerializer(analytics).data

        if request.query_params.get("narrative", "true").lower() != "false":
            payload["narrative"] = _narrative(explain_assessment, analytics)
        return Response(payload)


@extend_schema(tags=TEACHER_TAG, responses={200: RosterEntrySerializer(many=True)})
class AssessmentRosterView(TeacherViewMixin, APIView):
    """Who needs help, and with what.

    The aggregates say how many children are at a level; this says which ones.
    Filterable by `?domain=` and `?level=`.
    """

    serializer_class = RosterEntrySerializer

    def get(self, request: Request, pk) -> Response:
        assessment = self.drafts.get(pk)
        level = request.query_params.get("level")
        entries = RosterService(assessment).build(
            domain=request.query_params.get("domain", ""),
            level=int(level) if level and level.isdigit() else None,
        )
        return Response(RosterEntrySerializer(entries, many=True).data)


@extend_schema(tags=TEACHER_TAG, responses={200: StudentBreakdownSerializer})
class StudentBreakdownView(TeacherViewMixin, APIView):
    """One child's standing, by skill, with level context."""

    serializer_class = StudentBreakdownSerializer

    def get(self, request: Request, pk) -> Response:
        student = Student.objects.filter(pk=pk, school=self.school).first()
        if student is None:
            raise NotFoundError("No such student in this school.")

        breakdown = StudentBreakdownService(student).build()
        payload = StudentBreakdownSerializer(breakdown).data
        if request.query_params.get("narrative", "true").lower() != "false":
            payload["narrative"] = _narrative(explain_student, breakdown)
        return Response(payload)


def _narrative(job, subject) -> dict | None:
    """Run a narrative job, and let the page render without it if it fails.

    A model being down must not take the numbers with it. The figures are the
    diagnosis; the prose is a convenience over them.
    """
    outcome = job(subject)
    if outcome.value is None:
        return None
    return {
        "summary": outcome.value.summary,
        "attention": outcome.value.attention,
        "strength": outcome.value.strength,
    }


@extend_schema(tags=TEACHER_TAG, responses={200: ResponseReviewSerializer})
class ResponseReviewView(TeacherViewMixin, APIView):
    """One child's paper, question by question, as they saw it.

    Prefetched so the whole review is a handful of queries rather than one per
    question — a paper can run to fifty items and this is opened per child.
    """

    serializer_class = ResponseReviewSerializer

    def get(self, request: Request, pk, student_id) -> Response:  # noqa: ARG002
        assessment = self.drafts.get(pk)
        student = Student.objects.filter(pk=student_id, school=self.school).first()
        if student is None:
            raise NotFoundError("No such student in this school.")

        responses = (
            AssessmentQuestionResponse.objects.filter(student=student)
            .prefetch_related("selected_options")
            .select_related("media_value")
        )

        questions = (
            AssessmentQuestion.objects.filter(assessment=assessment)
            .select_related("subskill", "skill", "section")
            .prefetch_related(
                "contents__media",
                "options__media",
                Prefetch("responses", queryset=responses, to_attr="student_responses"),
            )
            .order_by("section__order", "order")
        )

        result = AssessmentResult.objects.filter(assessment=assessment, student=student).first()
        marked = [
            q.student_responses[0]
            for q in questions
            if q.student_responses and q.student_responses[0].is_correct is not None
        ]

        return Response(
            ResponseReviewSerializer(
                {
                    "student_id": str(student.pk),
                    "full_name": student.full_name,
                    "assessment_id": str(assessment.pk),
                    "assessment_name": assessment.name,
                    "status": result.status if result else "",
                    "items_attempted": len(marked),
                    "items_correct": sum(1 for r in marked if r.is_correct),
                    "pending": sum(
                        1
                        for q in questions
                        if q.student_responses and q.student_responses[0].is_correct is None
                    ),
                    "percentage": result.percentage if result else None,
                    "questions": questions,
                }
            ).data
        )
