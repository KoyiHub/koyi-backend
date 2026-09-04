"""Putting a published paper in front of a class."""

import pytest
from django.urls import reverse

from apps.assessments.dto import (
    CreateAssessmentInput,
    CreateSectionInput,
    OptionInput,
    QuestionInput,
)
from apps.assessments.enums import ResultStatus, SectionResultStatus
from apps.assessments.models import AssessmentResult, AssessmentSectionResult
from apps.assessments.services import AssessmentDraftService, AssessmentPublishService
from apps.common.enums import Domain, QuestionType
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.schools.factories import SchoolClassFactory, StudentFactory, TeacherFactory

pytestmark = pytest.mark.django_db

LIST_URL = "v1:teacher_portal:assignment-list"


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
def subskill():
    skill = SkillFactory(domain=Domain.LITERACY, min_level=1, max_level=3, code="asg_phonics")
    return SubskillFactory(skill=skill, code="asg_letter_sounds")


@pytest.fixture
def draft(teacher, subskill):
    drafts = AssessmentDraftService(teacher.school, teacher)
    assessment = drafts.create(CreateAssessmentInput(name="Baseline"))
    section = drafts.add_section(
        assessment, CreateSectionInput(domain=Domain.LITERACY, name="Reading")
    )
    drafts.set_questions(
        section,
        [
            QuestionInput(
                subskill_id=subskill.pk,
                fln_level=1,
                question_type=QuestionType.SINGLE_CHOICE,
                text="Which letter?",
                options=(
                    OptionInput(type="text", value="B", is_correct=True),
                    OptionInput(type="text", value="D"),
                ),
            )
        ],
    )
    return assessment


@pytest.fixture
def published(teacher, draft):
    return AssessmentPublishService(teacher.school, teacher).publish(draft)


class TestAssign:
    def test_a_draft_cannot_be_assigned(self, client, draft, teacher):
        student = StudentFactory(school=teacher.school)
        response = client.post(
            reverse(LIST_URL, args=[draft.pk]),
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        assert response.status_code == 400
        assert "Publish" in response.data["error"]["message"]

    def test_assigning_creates_the_whole_shell(self, client, published, teacher):
        student = StudentFactory(school=teacher.school)
        response = client.post(
            reverse(LIST_URL, args=[published.pk]),
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        assert response.status_code == 201

        # "Not started" is a real row rather than an absence.
        result = AssessmentResult.objects.get(assessment=published, student=student)
        assert result.status == ResultStatus.NOT_STARTED
        rows = AssessmentSectionResult.objects.filter(result=result)
        assert rows.count() == 1
        assert rows.first().status == SectionResultStatus.UNLOCKED

    def test_a_whole_class_can_be_assigned_at_once(self, client, published, teacher):
        school_class = SchoolClassFactory(school=teacher.school)
        for _ in range(3):
            StudentFactory(school=teacher.school, school_class=school_class)

        response = client.post(
            reverse(LIST_URL, args=[published.pk]),
            {"class_ids": [str(school_class.pk)]},
            format="json",
        )
        assert response.status_code == 201
        assert len(response.data) == 3

    def test_assigning_twice_is_a_no_op(self, client, published, teacher):
        student = StudentFactory(school=teacher.school)
        url = reverse(LIST_URL, args=[published.pk])
        payload = {"student_ids": [str(student.pk)]}

        client.post(url, payload, format="json")
        second = client.post(url, payload, format="json")
        # Adding a latecomer to an already-assigned class is normal.
        assert second.status_code == 201
        assert second.data == []
        assert published.assignments.count() == 1

    def test_another_school_s_student_cannot_be_assigned(self, client, published):
        stranger = StudentFactory()
        response = client.post(
            reverse(LIST_URL, args=[published.pk]),
            {"student_ids": [str(stranger.pk)]},
            format="json",
        )
        assert response.status_code == 201
        assert response.data == []
        assert published.assignments.count() == 0

    def test_a_disabled_student_is_skipped(self, client, published, teacher):
        student = StudentFactory(school=teacher.school, is_active=False)
        response = client.post(
            reverse(LIST_URL, args=[published.pk]),
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        assert response.data == []

    def test_empty_payload_is_rejected(self, client, published):
        response = client.post(reverse(LIST_URL, args=[published.pk]), {}, format="json")
        assert response.status_code == 400


class TestRevoke:
    def test_an_unstarted_assignment_can_be_withdrawn(self, client, published, teacher):
        student = StudentFactory(school=teacher.school)
        created = client.post(
            reverse(LIST_URL, args=[published.pk]),
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        assignment_id = created.data[0]["id"]

        response = client.delete(
            reverse("v1:teacher_portal:assignment-detail", args=[published.pk, assignment_id])
        )
        assert response.status_code == 204
        assert published.assignments.count() == 0
        assert not AssessmentResult.objects.filter(assessment=published, student=student).exists()

    def test_a_started_assignment_is_protected(self, client, published, teacher):
        student = StudentFactory(school=teacher.school)
        created = client.post(
            reverse(LIST_URL, args=[published.pk]),
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        assignment = published.assignments.first()
        assignment.status = ResultStatus.IN_PROGRESS
        assignment.save(update_fields=["status"])

        response = client.delete(
            reverse(
                "v1:teacher_portal:assignment-detail",
                args=[published.pk, created.data[0]["id"]],
            )
        )
        # Their work would go with it.
        assert response.status_code == 400


class TestCodes:
    def test_the_assignment_list_carries_each_child_s_code(self, client, published, teacher):
        student = StudentFactory(school=teacher.school)
        client.post(
            reverse(LIST_URL, args=[published.pk]),
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        listed = client.get(reverse(LIST_URL, args=[published.pk]))
        assert listed.status_code == 200
        assert listed.data[0]["code"]

    def test_the_roster_is_the_printable_code_sheet(self, client, published, teacher):
        school_class = SchoolClassFactory(school=teacher.school)
        for _ in range(3):
            StudentFactory(school=teacher.school, school_class=school_class)
        client.post(
            reverse(LIST_URL, args=[published.pk]),
            {"class_ids": [str(school_class.pk)]},
            format="json",
        )

        roster = client.get(reverse("v1:teacher_portal:assignment-roster", args=[published.pk]))
        assert roster.status_code == 200
        assert roster.data["assessment_code"] == published.code
        assert len(roster.data["rows"]) == 3
        # One code per child, all distinct — there is no single code to write
        # on a board any more.
        codes = {row["code"] for row in roster.data["rows"]}
        assert len(codes) == 3

    def test_another_school_cannot_read_the_roster(self, api_client, published):
        from rest_framework_simplejwt.tokens import RefreshToken

        outsider = TeacherFactory()
        token = RefreshToken.for_user(outsider.user).access_token
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = api_client.get(
            reverse("v1:teacher_portal:assignment-roster", args=[published.pk])
        )
        assert response.status_code == 404
