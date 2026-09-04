"""The teacher authoring flow, over HTTP."""

import pytest
from django.urls import reverse

from apps.assessments.enums import AssessmentStatus
from apps.common.enums import Domain, QuestionType
from apps.curriculum.factories import QuestionFactory, SkillFactory, SubskillFactory
from apps.schools.factories import SchoolFactory, TeacherFactory
from apps.users.factories import DEFAULT_PASSWORD

pytestmark = pytest.mark.django_db


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
    skill = SkillFactory(domain=Domain.LITERACY, min_level=1, max_level=3, code="phonics_api")
    return SubskillFactory(skill=skill, code="letter_sounds_api", name="Letter sounds")


def question_payload(subskill, level=1):
    return {
        "subskill_id": str(subskill.pk),
        "fln_level": level,
        "question_type": QuestionType.SINGLE_CHOICE,
        "text": "Which letter makes this sound?",
        "options": [
            {"type": "text", "value": "B", "is_correct": True},
            {"type": "text", "value": "D"},
        ],
    }


class TestLogin:
    def test_teacher_signs_in_with_their_teacher_id(self, api_client, teacher):
        response = api_client.post(
            reverse("v1:teacher_portal:login"),
            {"teacher_id": teacher.teacher_id, "password": DEFAULT_PASSWORD},
        )
        assert response.status_code == 200
        assert response.data["teacher"]["teacher_id"] == teacher.teacher_id
        assert "access" in response.data

    def test_teacher_id_is_case_insensitive(self, api_client, teacher):
        response = api_client.post(
            reverse("v1:teacher_portal:login"),
            {"teacher_id": teacher.teacher_id.lower(), "password": DEFAULT_PASSWORD},
        )
        assert response.status_code == 200

    def test_wrong_password_is_refused(self, api_client, teacher):
        response = api_client.post(
            reverse("v1:teacher_portal:login"),
            {"teacher_id": teacher.teacher_id, "password": "wrong"},
        )
        assert response.status_code == 401

    def test_unknown_id_gives_the_same_message_as_a_wrong_password(self, api_client, teacher):
        unknown = api_client.post(
            reverse("v1:teacher_portal:login"),
            {"teacher_id": "NOPE-000", "password": DEFAULT_PASSWORD},
        )
        wrong = api_client.post(
            reverse("v1:teacher_portal:login"),
            {"teacher_id": teacher.teacher_id, "password": "wrong"},
        )
        # Identical, so the endpoint cannot be used to discover teacher ids.
        assert unknown.data["error"]["message"] == wrong.data["error"]["message"]

    def test_a_school_account_cannot_use_the_teacher_login(self, api_client):
        school = SchoolFactory()
        response = api_client.post(
            reverse("v1:teacher_portal:login"),
            {"teacher_id": school.abbreviation, "password": DEFAULT_PASSWORD},
        )
        assert response.status_code == 401


class TestAuthoringFlow:
    def test_draft_to_published(self, client, subskill):
        create = client.post(
            reverse("v1:teacher_portal:assessment-list"), {"name": "Term 1 baseline"}, format="json"
        )
        assert create.status_code == 201
        assert create.data["status"] == AssessmentStatus.DRAFT
        assert create.data["code"] == ""
        assessment_id = create.data["id"]

        section = client.post(
            reverse("v1:teacher_portal:section-list", args=[assessment_id]),
            {"domain": Domain.LITERACY, "name": "Reading", "covers": [str(subskill.pk)]},
            format="json",
        )
        assert section.status_code == 201
        section_id = section.data["id"]

        questions = client.put(
            reverse("v1:teacher_portal:section-questions", args=[assessment_id, section_id]),
            {"questions": [question_payload(subskill, 1), question_payload(subskill, 3)]},
            format="json",
        )
        assert questions.status_code == 200
        assert [q["order"] for q in questions.data] == [1, 2]

        coverage = client.get(
            reverse("v1:teacher_portal:assessment-coverage", args=[assessment_id])
        )
        assert coverage.status_code == 200
        assert coverage.data["levels_probed"] == [1, 3]
        assert coverage.data["question_count"] == 2

        published = client.post(
            reverse("v1:teacher_portal:assessment-publish", args=[assessment_id])
        )
        assert published.status_code == 200
        assert published.data["status"] == AssessmentStatus.PUBLISHED
        assert len(published.data["code"]) == 6

    def test_editing_a_published_paper_is_refused(self, client, subskill):
        create = client.post(
            reverse("v1:teacher_portal:assessment-list"), {"name": "Baseline"}, format="json"
        )
        assessment_id = create.data["id"]
        section = client.post(
            reverse("v1:teacher_portal:section-list", args=[assessment_id]),
            {"domain": Domain.LITERACY, "name": "Reading"},
            format="json",
        )
        client.put(
            reverse(
                "v1:teacher_portal:section-questions", args=[assessment_id, section.data["id"]]
            ),
            {"questions": [question_payload(subskill)]},
            format="json",
        )
        client.post(reverse("v1:teacher_portal:assessment-publish", args=[assessment_id]))

        response = client.patch(
            reverse("v1:teacher_portal:assessment-detail", args=[assessment_id]),
            {"name": "Renamed"},
            format="json",
        )
        assert response.status_code == 400

    def test_a_level_outside_the_subskill_range_is_rejected(self, client, subskill):
        create = client.post(
            reverse("v1:teacher_portal:assessment-list"), {"name": "Baseline"}, format="json"
        )
        section = client.post(
            reverse("v1:teacher_portal:section-list", args=[create.data["id"]]),
            {"domain": Domain.LITERACY, "name": "Reading"},
            format="json",
        )
        response = client.put(
            reverse(
                "v1:teacher_portal:section-questions",
                args=[create.data["id"], section.data["id"]],
            ),
            {"questions": [question_payload(subskill, level=5)]},
            format="json",
        )
        assert response.status_code == 400
        assert "only assessed at levels 1 to 3" in response.data["error"]["message"]

    def test_an_option_question_needs_a_correct_answer(self, client, subskill):
        create = client.post(
            reverse("v1:teacher_portal:assessment-list"), {"name": "Baseline"}, format="json"
        )
        section = client.post(
            reverse("v1:teacher_portal:section-list", args=[create.data["id"]]),
            {"domain": Domain.LITERACY, "name": "Reading"},
            format="json",
        )
        payload = question_payload(subskill)
        for option in payload["options"]:
            option["is_correct"] = False
        response = client.put(
            reverse(
                "v1:teacher_portal:section-questions",
                args=[create.data["id"], section.data["id"]],
            ),
            {"questions": [payload]},
            format="json",
        )
        assert response.status_code == 400


class TestTenantIsolation:
    def test_a_teacher_cannot_reach_another_school_s_assessment(self, client, subskill):
        other_teacher = TeacherFactory()
        from apps.assessments.dto import CreateAssessmentInput
        from apps.assessments.services import AssessmentDraftService

        foreign = AssessmentDraftService(other_teacher.school, other_teacher).create(
            CreateAssessmentInput(name="Someone else's paper")
        )
        response = client.get(reverse("v1:teacher_portal:assessment-detail", args=[foreign.pk]))
        assert response.status_code == 404

    def test_the_list_only_shows_the_teacher_s_own_school(self, client, teacher):
        from apps.assessments.dto import CreateAssessmentInput
        from apps.assessments.services import AssessmentDraftService

        AssessmentDraftService(teacher.school, teacher).create(CreateAssessmentInput(name="Mine"))
        other = TeacherFactory()
        AssessmentDraftService(other.school, other).create(CreateAssessmentInput(name="Theirs"))

        response = client.get(reverse("v1:teacher_portal:assessment-list"))
        names = [row["name"] for row in response.data["results"]]
        assert names == ["Mine"]


class TestBankBrowsing:
    def test_bank_is_filterable_by_subskill_and_level(self, client, subskill):
        QuestionFactory(subskill=subskill, fln_level=1)
        QuestionFactory(subskill=subskill, fln_level=3)

        response = client.get(
            reverse("v1:teacher_portal:bank-question-list"),
            {"subskill": str(subskill.pk), "fln_level": 3},
        )
        assert response.status_code == 200
        assert len(response.data["results"]) == 1
        assert response.data["results"][0]["fln_level"] == 3

    def test_skill_tree_exposes_each_subskill_s_level_range(self, client, subskill):
        response = client.get(reverse("v1:teacher_portal:skill-list"), {"domain": Domain.LITERACY})
        assert response.status_code == 200
        skills = {row["code"]: row for row in response.data}
        subskills = skills["phonics_api"]["subskills"]
        assert subskills[0]["level_range"] == [1, 3]


class TestPermissions:
    def test_anonymous_is_refused(self, api_client):
        response = api_client.get(reverse("v1:teacher_portal:assessment-list"))
        assert response.status_code == 401

    def test_a_school_admin_token_is_refused(self, api_client):
        from rest_framework_simplejwt.tokens import RefreshToken

        school = SchoolFactory()
        token = RefreshToken.for_user(school.user).access_token
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        response = api_client.get(reverse("v1:teacher_portal:assessment-list"))
        assert response.status_code == 403
