"""A child sitting a paper, end to end."""

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.assessments.dto import (
    CreateAssessmentInput,
    CreateSectionInput,
    OptionInput,
    QuestionInput,
)
from apps.assessments.enums import ResultStatus, SectionResultStatus
from apps.assessments.models import AssessmentQuestionResponse
from apps.assessments.services import (
    AssessmentAssignmentService,
    AssessmentDraftService,
    AssessmentPublishService,
)
from apps.common.enums import Domain, QuestionType
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.schools.factories import SchoolClassFactory, StudentFactory, TeacherFactory

pytestmark = pytest.mark.django_db

VERIFY = "v1:student_portal:verify"
OVERVIEW = "v1:student_portal:overview"


@pytest.fixture
def subskill():
    skill = SkillFactory(domain=Domain.LITERACY, min_level=1, max_level=3, code="sit_phonics")
    return SubskillFactory(skill=skill, code="sit_letter_sounds", name="Letter sounds")


@pytest.fixture
def teacher():
    return TeacherFactory()


@pytest.fixture
def student(teacher):
    school_class = SchoolClassFactory(school=teacher.school)
    return StudentFactory(school=teacher.school, school_class=school_class)


def _question(subskill, level=1):
    return QuestionInput(
        subskill_id=subskill.pk,
        fln_level=level,
        question_type=QuestionType.SINGLE_CHOICE,
        text="Which letter makes this sound?",
        options=(
            OptionInput(type="text", value="B", is_correct=True),
            OptionInput(type="text", value="D"),
        ),
    )


@pytest.fixture
def published(teacher, subskill):
    """A two-section paper, published and ready to assign."""
    drafts = AssessmentDraftService(teacher.school, teacher)
    assessment = drafts.create(CreateAssessmentInput(name="Baseline"))
    first = drafts.add_section(
        assessment, CreateSectionInput(domain=Domain.LITERACY, name="Reading")
    )
    second = drafts.add_section(
        assessment, CreateSectionInput(domain=Domain.NUMERACY, name="Numbers")
    )
    drafts.set_questions(first, [_question(subskill), _question(subskill, 2)])

    numeracy = SubskillFactory(
        skill=SkillFactory(domain=Domain.NUMERACY, min_level=1, max_level=2, code="sit_counting"),
        code="sit_numerals",
    )
    drafts.set_questions(second, [_question(numeracy)])
    return AssessmentPublishService(teacher.school, teacher).publish(assessment)


@pytest.fixture
def assignment(published, teacher, student):
    return AssessmentAssignmentService(teacher.school, teacher).assign(
        published, students=[student]
    )[0]


@pytest.fixture
def sitting(api_client, published, assignment, student):
    """A client holding a valid sitting session."""
    response = api_client.post(
        reverse(VERIFY),
        {"code": published.code, "student_id": student.student_id},
        format="json",
    )
    assert response.status_code == 200
    api_client.credentials(HTTP_X_SITTING_SESSION=response.data["session"])
    return api_client


class TestVerify:
    def test_code_and_student_id_open_the_paper(self, api_client, published, assignment, student):
        response = api_client.post(
            reverse(VERIFY),
            {"code": published.code, "student_id": student.student_id},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["assessment"]["name"] == "Baseline"
        sections = response.data["assessment"]["sections"]
        # Only the first section is reachable to begin with.
        assert [s["status"] for s in sections] == [
            SectionResultStatus.UNLOCKED,
            SectionResultStatus.LOCKED,
        ]

    def test_code_is_case_insensitive(self, api_client, published, assignment, student):
        response = api_client.post(
            reverse(VERIFY),
            {"code": published.code.lower(), "student_id": student.student_id},
            format="json",
        )
        assert response.status_code == 200

    def test_an_unassigned_child_is_refused(self, api_client, published, assignment):
        stranger = StudentFactory()
        response = api_client.post(
            reverse(VERIFY),
            {"code": published.code, "student_id": stranger.student_id},
            format="json",
        )
        assert response.status_code == 404

    def test_a_wrong_code_and_a_wrong_id_are_indistinguishable(
        self, api_client, published, assignment, student
    ):
        bad_code = api_client.post(
            reverse(VERIFY),
            {"code": "ZZZZZZ", "student_id": student.student_id},
            format="json",
        )
        bad_id = api_client.post(
            reverse(VERIFY), {"code": published.code, "student_id": "NOPE"}, format="json"
        )
        # Otherwise the form becomes a way to discover real codes and ids.
        assert bad_code.data["error"]["message"] == bad_id.data["error"]["message"]

    def test_a_closed_assessment_is_refused(self, api_client, published, assignment, student):
        published.closes_at = timezone.now() - timezone.timedelta(hours=1)
        published.save(update_fields=["closes_at"])
        response = api_client.post(
            reverse(VERIFY),
            {"code": published.code, "student_id": student.student_id},
            format="json",
        )
        assert response.status_code == 400
        assert "closed" in response.data["error"]["message"]

    def test_a_disabled_student_is_refused(self, api_client, published, assignment, student):
        student.is_active = False
        student.save(update_fields=["is_active"])
        response = api_client.post(
            reverse(VERIFY),
            {"code": published.code, "student_id": student.student_id},
            format="json",
        )
        assert response.status_code == 404


class TestSittingFlow:
    def test_sections_unlock_in_order_and_the_paper_finalises_itself(
        self, sitting, published, assignment
    ):
        overview = sitting.get(reverse(OVERVIEW)).data
        first, second = overview["sections"]
        assert second["status"] == SectionResultStatus.LOCKED

        # The second section cannot be jumped to.
        blocked = sitting.post(reverse("v1:student_portal:section-start", args=[second["id"]]))
        assert blocked.status_code == 400

        start = sitting.post(reverse("v1:student_portal:section-start", args=[first["id"]]))
        assert start.status_code == 200
        assert len(start.data["questions"]) == 2

        question = start.data["questions"][0]
        saved = sitting.put(
            reverse("v1:student_portal:save-response", args=[question["id"]]),
            {"option_ids": [question["options"][0]["id"]]},
            format="json",
        )
        assert saved.status_code == 200

        submitted = sitting.post(reverse("v1:student_portal:section-submit", args=[first["id"]]))
        assert submitted.status_code == 200
        statuses = [s["status"] for s in submitted.data["sections"]]
        assert statuses == [SectionResultStatus.SUBMITTED, SectionResultStatus.UNLOCKED]

        # Finishing the last section closes the paper with no further step.
        sitting.post(reverse("v1:student_portal:section-start", args=[second["id"]]))
        final = sitting.post(reverse("v1:student_portal:section-submit", args=[second["id"]]))
        assert final.data["status"] == ResultStatus.FINISHED

        assignment.refresh_from_db()
        assert assignment.status == ResultStatus.FINISHED
        assert assignment.submitted_at is not None

    def test_answering_before_starting_is_refused(self, sitting):
        overview = sitting.get(reverse(OVERVIEW)).data
        section_id = overview["sections"][0]["id"]
        start = sitting.post(reverse("v1:student_portal:section-start", args=[section_id]))
        question_id = start.data["questions"][0]["id"]

        sitting.post(reverse("v1:student_portal:section-submit", args=[section_id]))
        response = sitting.put(
            reverse("v1:student_portal:save-response", args=[question_id]),
            {"text_value": "late"},
            format="json",
        )
        assert response.status_code == 400

    def test_saving_twice_updates_rather_than_duplicates(self, sitting, student):
        overview = sitting.get(reverse(OVERVIEW)).data
        section_id = overview["sections"][0]["id"]
        start = sitting.post(reverse("v1:student_portal:section-start", args=[section_id]))
        question = start.data["questions"][0]
        url = reverse("v1:student_portal:save-response", args=[question["id"]])

        sitting.put(url, {"option_ids": [question["options"][0]["id"]]}, format="json")
        sitting.put(url, {"option_ids": [question["options"][1]["id"]]}, format="json")

        responses = AssessmentQuestionResponse.objects.filter(student=student)
        assert responses.count() == 1
        assert responses.first().selected_options.count() == 1

    def test_the_runner_never_ships_the_answer_key(self, sitting):
        overview = sitting.get(reverse(OVERVIEW)).data
        start = sitting.post(
            reverse("v1:student_portal:section-start", args=[overview["sections"][0]["id"]])
        )
        for option in start.data["questions"][0]["options"]:
            assert "is_correct" not in option


class TestSessionScope:
    def test_a_session_names_one_assignment_and_nothing_else(
        self, api_client, published, assignment, student, teacher
    ):
        other = StudentFactory(school=teacher.school, school_class=student.school_class)
        AssessmentAssignmentService(teacher.school, teacher).assign(published, students=[other])

        first = api_client.post(
            reverse(VERIFY),
            {"code": published.code, "student_id": student.student_id},
            format="json",
        )
        api_client.credentials(HTTP_X_SITTING_SESSION=first.data["session"])
        overview = api_client.get(reverse(OVERVIEW))
        # Nothing in the request names a student, so there is no id to swap.
        assert overview.data["student_name"] == student.full_name

    def test_a_teacher_token_does_not_open_a_sitting(self, api_client, teacher, assignment):
        from rest_framework_simplejwt.tokens import RefreshToken

        token = RefreshToken.for_user(teacher.user).access_token
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        assert api_client.get(reverse(OVERVIEW)).status_code == 401

    def test_a_session_code_is_not_a_staff_credential(
        self, api_client, published, assignment, student
    ):
        response = api_client.post(
            reverse(VERIFY),
            {"code": published.code, "student_id": student.student_id},
            format="json",
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['session']}")
        blocked = api_client.get(reverse("v1:teacher_portal:assessment-list"))
        assert blocked.status_code == 401

    def test_no_session_is_refused(self, api_client):
        assert api_client.get(reverse(OVERVIEW)).status_code == 401

    def test_an_unknown_session_is_refused(self, api_client):
        api_client.credentials(HTTP_X_SITTING_SESSION="not-a-real-session")
        assert api_client.get(reverse(OVERVIEW)).status_code == 401

    def test_an_expired_session_is_refused(self, api_client, sitting, assignment):
        from apps.assessments.models import AssessmentAssignment

        AssessmentAssignment.objects.filter(pk=assignment.pk).update(
            session_expires_at=timezone.now() - timezone.timedelta(minutes=1)
        )
        assert sitting.get(reverse(OVERVIEW)).status_code == 401

    def test_verifying_again_ends_the_previous_session(
        self, api_client, published, assignment, student
    ):
        first = api_client.post(
            reverse(VERIFY),
            {"code": published.code, "student_id": student.student_id},
            format="json",
        )
        api_client.post(
            reverse(VERIFY),
            {"code": published.code, "student_id": student.student_id},
            format="json",
        )
        # Moving to another tablet mid-paper should not leave the old one live.
        api_client.credentials(HTTP_X_SITTING_SESSION=first.data["session"])
        assert api_client.get(reverse(OVERVIEW)).status_code == 401

    def test_the_session_code_is_not_stored_in_the_clear(
        self, api_client, published, assignment, student
    ):
        response = api_client.post(
            reverse(VERIFY),
            {"code": published.code, "student_id": student.student_id},
            format="json",
        )
        assignment.refresh_from_db()
        assert assignment.session_hash
        assert assignment.session_hash != response.data["session"]


class TestTimer:
    def test_a_section_past_its_timer_stops_accepting_answers(self, sitting, student):
        from apps.assessments.models import AssessmentSectionResult

        overview = sitting.get(reverse(OVERVIEW)).data
        section_id = overview["sections"][0]["id"]
        start = sitting.post(reverse("v1:student_portal:section-start", args=[section_id]))
        question_id = start.data["questions"][0]["id"]

        AssessmentSectionResult.objects.filter(section_id=section_id).update(
            expires_at=timezone.now() - timezone.timedelta(minutes=1)
        )
        response = sitting.put(
            reverse("v1:student_portal:save-response", args=[question_id]),
            {"text_value": "too late"},
            format="json",
        )
        assert response.status_code == 400
        assert "Time is up" in response.data["error"]["message"]

    def test_the_timer_starts_when_the_section_is_opened(self, sitting, published):
        from datetime import timedelta

        from apps.assessments.models import AssessmentSection, AssessmentSectionResult

        section = AssessmentSection.objects.filter(assessment=published).first()
        AssessmentSection.objects.filter(pk=section.pk).update(timer=timedelta(minutes=20))

        sitting.post(reverse("v1:student_portal:section-start", args=[section.pk]))
        row = AssessmentSectionResult.objects.get(section=section)
        # Bounded from when the child opened it, not from when it was assigned.
        assert row.expires_at is not None
        assert row.started_at is not None
        assert (row.expires_at - row.started_at) == timedelta(minutes=20)
