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
        {"assessment_code": published.code, "code": assignment.code},
        format="json",
    )
    assert response.status_code == 200
    api_client.credentials(HTTP_X_SITTING_SESSION=response.data["session"])
    return api_client


class TestVerify:
    def test_two_codes_open_the_paper(self, api_client, published, assignment):
        response = api_client.post(
            reverse(VERIFY),
            {"assessment_code": published.code, "code": assignment.code},
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
            {"assessment_code": published.code.lower(), "code": assignment.code.lower()},
            format="json",
        )
        assert response.status_code == 200

    def test_an_unknown_personal_code_is_refused(self, api_client, published, assignment):
        response = api_client.post(
            reverse(VERIFY),
            {"assessment_code": published.code, "code": "ZZZZZZ"},
            format="json",
        )
        assert response.status_code == 404

    def test_a_student_id_no_longer_opens_a_sitting(
        self, api_client, published, assignment, student
    ):
        # It is printed on a card and known to every classmate; two public
        # facts were never a credential.
        response = api_client.post(
            reverse(VERIFY),
            {"assessment_code": published.code, "code": student.student_id},
            format="json",
        )
        assert response.status_code == 404

    def test_both_kinds_of_wrong_code_read_the_same(self, api_client, published, assignment):
        bad_assessment = api_client.post(
            reverse(VERIFY),
            {"assessment_code": "ZZZZZZ", "code": assignment.code},
            format="json",
        )
        bad_personal = api_client.post(
            reverse(VERIFY),
            {"assessment_code": published.code, "code": "ZZZZZZ"},
            format="json",
        )
        # Otherwise the form becomes a way to discover which codes are real.
        assert bad_assessment.data["error"]["message"] == bad_personal.data["error"]["message"]

    def test_a_closed_assessment_is_refused(self, api_client, published, assignment, student):
        published.closes_at = timezone.now() - timezone.timedelta(hours=1)
        published.save(update_fields=["closes_at"])
        response = api_client.post(
            reverse(VERIFY),
            {"assessment_code": published.code, "code": assignment.code},
            format="json",
        )
        assert response.status_code == 400
        assert "closed" in response.data["error"]["message"]

    def test_a_disabled_student_is_refused(self, api_client, published, assignment, student):
        student.is_active = False
        student.save(update_fields=["is_active"])
        response = api_client.post(
            reverse(VERIFY),
            {"assessment_code": published.code, "code": assignment.code},
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
            {"assessment_code": published.code, "code": assignment.code},
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
            {"assessment_code": published.code, "code": assignment.code},
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
            {"assessment_code": published.code, "code": assignment.code},
            format="json",
        )
        api_client.post(
            reverse(VERIFY),
            {"assessment_code": published.code, "code": assignment.code},
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
            {"assessment_code": published.code, "code": assignment.code},
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


class TestPersonalCode:
    """The code is what makes a sitting the child's own."""

    def test_each_child_gets_a_different_code(self, published, teacher, student):
        other = StudentFactory(school=teacher.school, school_class=student.school_class)
        created = AssessmentAssignmentService(teacher.school, teacher).assign(
            published, students=[student, other]
        )
        codes = {a.code for a in created}
        assert len(codes) == 2
        assert all(code for code in codes)

    def test_one_child_s_code_does_not_open_another_s_sitting(
        self, api_client, published, teacher, student
    ):
        other = StudentFactory(school=teacher.school, school_class=student.school_class)
        _mine, theirs = AssessmentAssignmentService(teacher.school, teacher).assign(
            published, students=[student, other]
        )

        response = api_client.post(
            reverse(VERIFY),
            {"assessment_code": published.code, "code": theirs.code},
            format="json",
        )
        api_client.credentials(HTTP_X_SITTING_SESSION=response.data["session"])
        overview = api_client.get(reverse(OVERVIEW))
        assert overview.data["student_name"] == other.full_name

    def test_the_code_avoids_ambiguous_characters(self, assignment):
        # Read off a printed sheet by a child; O/0 and I/1 cost them a sitting.
        assert not set(assignment.code) & set("OI0125SZ")

    def test_the_code_is_readable_by_the_teacher(self, assignment):
        # Stored in the clear on purpose — a child who has lost theirs needs
        # someone who can tell them what it is.
        assignment.refresh_from_db()
        assert assignment.code

    def test_verifying_is_recorded(self, api_client, published, assignment, student):
        from apps.activities.models import Activity

        api_client.post(
            reverse(VERIFY),
            {"assessment_code": published.code, "code": assignment.code},
            format="json",
        )
        entry = Activity.objects.filter(student=student).first()
        assert entry is not None
        assert entry.metadata["assignment_code"] == assignment.code


class TestDiagnosisOnSubmit:
    """Finishing the paper should place the child, with no further step."""

    def test_the_last_submission_produces_a_placement(
        self, sitting, published, student, subskill, django_capture_on_commit_callbacks
    ):
        from apps.assessments.models import Placement, PlacementRule, SkillLevelResult
        from apps.schools.models import StudentProfile

        for level in (1, 2):
            PlacementRule.objects.update_or_create(
                domain=subskill.skill.domain,
                fln_level=level,
                defaults={"required_skills": 1, "applicable_skills": 1},
            )

        overview = sitting.get(reverse(OVERVIEW)).data
        # Diagnosis is enqueued on_commit, which never fires inside the test's
        # transaction unless the callbacks are captured and run explicitly.
        with django_capture_on_commit_callbacks(execute=True):
            for section in overview["sections"]:
                start = sitting.post(
                    reverse("v1:student_portal:section-start", args=[section["id"]])
                )
                for question in start.data["questions"]:
                    sitting.put(
                        reverse("v1:student_portal:save-response", args=[question["id"]]),
                        {"option_ids": [question["options"][0]["id"]]},
                        format="json",
                    )
                sitting.post(reverse("v1:student_portal:section-submit", args=[section["id"]]))

        # Celery runs eagerly in tests, so the chain has completed by now.
        assert SkillLevelResult.objects.filter(student=student).exists()
        assert Placement.objects.filter(student=student).exists()
        assert StudentProfile.objects.filter(student=student).exists()

    def test_an_unfinished_paper_places_nobody(
        self, sitting, student, django_capture_on_commit_callbacks
    ):
        from apps.assessments.models import Placement

        overview = sitting.get(reverse(OVERVIEW)).data
        first = overview["sections"][0]
        with django_capture_on_commit_callbacks(execute=True):
            sitting.post(reverse("v1:student_portal:section-start", args=[first["id"]]))
            sitting.post(reverse("v1:student_portal:section-submit", args=[first["id"]]))

        # One section in, one to go - nothing to place on yet.
        assert not Placement.objects.filter(student=student).exists()
