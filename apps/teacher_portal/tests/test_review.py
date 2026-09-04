"""Walking one child's paper, question by question."""

import pytest
from django.urls import reverse

from apps.assessments.enums import AssessmentStatus, GradedBy
from apps.assessments.models import (
    Assessment,
    AssessmentQuestion,
    AssessmentQuestionOption,
    AssessmentQuestionResponse,
    AssessmentQuestionResponseOption,
    AssessmentResult,
    AssessmentSection,
)
from apps.common.enums import Domain, QuestionType
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.schools.factories import StudentFactory, TeacherFactory

pytestmark = pytest.mark.django_db

REVIEW = "v1:teacher_portal:response-review"


@pytest.fixture
def teacher():
    return TeacherFactory()


@pytest.fixture
def client(api_client, teacher):
    from rest_framework_simplejwt.tokens import RefreshToken

    token = RefreshToken.for_user(teacher.user).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api_client


@pytest.fixture
def sat_paper(teacher):
    """One choice question answered wrongly, one written answer left pending."""
    skill = SkillFactory(domain=Domain.LITERACY, code="rev_skill", min_level=1, max_level=2)
    subskill = SubskillFactory(skill=skill, code="rev_sub", name="Letter sounds")
    student = StudentFactory(school=teacher.school)
    assessment = Assessment.objects.create(
        school=teacher.school,
        teacher=teacher,
        name="Baseline",
        status=AssessmentStatus.PUBLISHED,
        code="REVIEW",
    )
    AssessmentResult.objects.create(assessment=assessment, student=student)
    section = AssessmentSection.objects.create(
        assessment=assessment, domain=Domain.LITERACY, name="Reading", order=1
    )

    choice = AssessmentQuestion.objects.create(
        section=section,
        assessment=assessment,
        subskill=subskill,
        skill=skill,
        fln_level=1,
        text="Which letter makes this sound?",
        question_type=QuestionType.SINGLE_CHOICE,
        order=1,
        point=1,
    )
    right = AssessmentQuestionOption.objects.create(
        assessment_question=choice, type="text", value="B", is_correct=True
    )
    wrong = AssessmentQuestionOption.objects.create(
        assessment_question=choice, type="text", value="D", is_correct=False
    )
    response = AssessmentQuestionResponse.objects.create(
        assessment_question=choice,
        student=student,
        assessment=assessment,
        type=QuestionType.SINGLE_CHOICE,
        is_correct=False,
        awarded_points=0,
        graded_by=GradedBy.AUTO,
        error_type="substitution",
        observation_note="Chose the visually similar letter.",
    )
    AssessmentQuestionResponseOption.objects.create(
        assessment_question_response=response, assessment_question_option=wrong
    )

    written = AssessmentQuestion.objects.create(
        section=section,
        assessment=assessment,
        subskill=subskill,
        skill=skill,
        fln_level=2,
        text="Write the word",
        question_type=QuestionType.TEXT,
        order=2,
        point=1,
    )
    AssessmentQuestionResponse.objects.create(
        assessment_question=written,
        student=student,
        assessment=assessment,
        type=QuestionType.TEXT,
        text_value="ct",
    )
    return assessment, student, right, wrong


class TestReview:
    def test_options_carry_both_correctness_and_selection(self, client, sat_paper):
        """So the client renders green and red without a second lookup."""
        assessment, student, right, wrong = sat_paper
        response = client.get(reverse(REVIEW, args=[assessment.pk, student.pk]))

        assert response.status_code == 200
        options = {o["id"]: o for o in response.data["questions"][0]["options"]}
        assert options[str(right.pk)]["is_correct"] is True
        assert options[str(right.pk)]["was_selected"] is False
        assert options[str(wrong.pk)]["is_correct"] is False
        assert options[str(wrong.pk)]["was_selected"] is True

    def test_the_marking_detail_comes_through(self, client, sat_paper):
        assessment, student, *_ = sat_paper
        response = client.get(reverse(REVIEW, args=[assessment.pk, student.pk]))

        first = response.data["questions"][0]["response"]
        assert first["is_correct"] is False
        assert first["error_type"] == "substitution"
        assert "visually similar" in first["observation_note"]

    def test_a_pending_answer_is_null_not_wrong(self, client, sat_paper):
        """The UI should offer a teacher the decision, not render an error."""
        assessment, student, *_ = sat_paper
        response = client.get(reverse(REVIEW, args=[assessment.pk, student.pk]))

        written = response.data["questions"][1]["response"]
        assert written["is_correct"] is None
        assert written["text_value"] == "ct"
        assert response.data["pending"] == 1

    def test_the_totals_count_only_what_was_marked(self, client, sat_paper):
        assessment, student, *_ = sat_paper
        response = client.get(reverse(REVIEW, args=[assessment.pk, student.pk]))

        assert response.data["items_attempted"] == 1
        assert response.data["items_correct"] == 0

    def test_questions_come_back_in_sitting_order(self, client, sat_paper):
        assessment, student, *_ = sat_paper
        response = client.get(reverse(REVIEW, args=[assessment.pk, student.pk]))
        assert [q["order"] for q in response.data["questions"]] == [1, 2]

    def test_each_question_carries_its_subskill_and_level(self, client, sat_paper):
        assessment, student, *_ = sat_paper
        response = client.get(reverse(REVIEW, args=[assessment.pk, student.pk]))
        first = response.data["questions"][0]
        assert first["subskill_name"] == "Letter sounds"
        assert first["fln_level"] == 1

    def test_another_school_s_child_is_not_visible(self, client, sat_paper):
        assessment, *_ = sat_paper
        outsider = StudentFactory()
        response = client.get(reverse(REVIEW, args=[assessment.pk, outsider.pk]))
        assert response.status_code == 404

    def test_another_school_s_paper_is_not_visible(self, api_client, sat_paper):
        from rest_framework_simplejwt.tokens import RefreshToken

        assessment, student, *_ = sat_paper
        outsider = TeacherFactory()
        token = RefreshToken.for_user(outsider.user).access_token
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = api_client.get(reverse(REVIEW, args=[assessment.pk, student.pk]))
        assert response.status_code == 404
