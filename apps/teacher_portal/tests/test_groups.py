"""Groups and plans, over HTTP."""

import pytest
from django.urls import reverse

from apps.ai.client import ScriptedClient, register_client, reset_client
from apps.ai.enums import JobType
from apps.common.enums import Domain, SkillStateStatus
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.instruction.enums import CriterionType, PlanStatus
from apps.instruction.models import Group, LessonPlan
from apps.schools.factories import SchoolClassFactory, StudentFactory, TeacherFactory
from apps.schools.models import StudentProfile, StudentSkillState

pytestmark = pytest.mark.django_db

LIST = "v1:teacher_portal:group-list"


@pytest.fixture(autouse=True)
def _isolate_ai():
    reset_client()
    yield
    reset_client()


@pytest.fixture
def teacher():
    teacher = TeacherFactory()
    teacher.school_class = SchoolClassFactory(school=teacher.school)
    teacher.save(update_fields=["school_class"])
    return teacher


@pytest.fixture
def client(api_client, teacher):
    from rest_framework_simplejwt.tokens import RefreshToken

    token = RefreshToken.for_user(teacher.user).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api_client


@pytest.fixture
def subskill():
    skill = SkillFactory(domain=Domain.LITERACY, code="api_grp", min_level=1, max_level=3)
    return SubskillFactory(skill=skill, code="api_grp_sub", name="Blending")


def child(teacher, *, level=2, weak=None):
    student = StudentFactory(school=teacher.school, school_class=teacher.school_class)
    StudentProfile.objects.create(student=student, literacy_level=level)
    if weak:
        StudentSkillState.objects.create(
            student=student, subskill=weak, status=SkillStateStatus.WEAK
        )
    return student


class TestCreate:
    def test_a_group_is_filled_the_moment_it_is_created(self, client, teacher):
        """So a teacher sees who matched, not an empty group they cannot read."""
        child(teacher, level=2)
        child(teacher, level=3)

        response = client.post(
            reverse(LIST),
            {
                "name": "Level 2 literacy",
                "domain": Domain.LITERACY,
                "criteria": [{"type": CriterionType.LEVEL, "level": 2, "comparator": "eq"}],
            },
            format="json",
        )

        assert response.status_code == 201
        assert response.data["size"] == 1
        assert len(response.data["members"]) == 1

    def test_a_rule_that_names_nothing_is_refused(self, client):
        response = client.post(
            reverse(LIST),
            {"name": "Broken", "criteria": [{"type": CriterionType.LEVEL}]},
            format="json",
        )
        assert response.status_code == 400

    def test_the_list_shows_only_this_teacher_s_groups(self, client, teacher):
        Group.objects.create(school=teacher.school, teacher=teacher, name="Mine")
        other = TeacherFactory()
        Group.objects.create(school=other.school, teacher=other, name="Theirs")

        response = client.get(reverse(LIST))
        assert [g["name"] for g in response.data] == ["Mine"]

    def test_another_teacher_s_group_is_not_reachable(self, client):
        other = TeacherFactory()
        group = Group.objects.create(school=other.school, teacher=other, name="Theirs")

        response = client.get(reverse("v1:teacher_portal:group-detail", args=[group.pk]))
        assert response.status_code == 404


class TestMembers:
    def test_a_teacher_can_add_and_remove_by_hand(self, client, teacher):
        group = Group.objects.create(school=teacher.school, teacher=teacher, name="Manual")
        student = child(teacher)

        added = client.post(
            reverse("v1:teacher_portal:group-members", args=[group.pk]),
            {"student": str(student.pk)},
            format="json",
        )
        assert added.status_code == 201

        removed = client.delete(
            reverse("v1:teacher_portal:group-member-detail", args=[group.pk, student.pk])
        )
        assert removed.status_code == 204
        assert group.size == 0

    def test_history_is_kept_and_current_can_be_filtered(self, client, teacher):
        group = Group.objects.create(school=teacher.school, teacher=teacher, name="History")
        student = child(teacher)
        url = reverse("v1:teacher_portal:group-members", args=[group.pk])
        client.post(url, {"student": str(student.pk)}, format="json")
        client.delete(reverse("v1:teacher_portal:group-member-detail", args=[group.pk, student.pk]))

        assert len(client.get(url).data) == 1
        assert client.get(url, {"current": "true"}).data == []

    def test_another_school_s_child_cannot_be_added(self, client, teacher):
        group = Group.objects.create(school=teacher.school, teacher=teacher, name="Manual")
        outsider = StudentFactory()

        response = client.post(
            reverse("v1:teacher_portal:group-members", args=[group.pk]),
            {"student": str(outsider.pk)},
            format="json",
        )
        assert response.status_code == 404


class TestPlans:
    def test_generation_is_backgrounded_and_polled(self, client, teacher, subskill):
        group = Group.objects.create(
            school=teacher.school, teacher=teacher, name="Blending", domain=Domain.LITERACY
        )
        for _ in range(4):
            student = child(teacher, weak=subskill)
            client.post(
                reverse("v1:teacher_portal:group-members", args=[group.pk]),
                {"student": str(student.pk)},
                format="json",
            )
        register_client(
            ScriptedClient(
                replies={
                    JobType.LESSON_PLAN_CANONICAL: {
                        "objective": "Blend three sounds.",
                        "duration_minutes": 25,
                        "materials": [],
                        "steps": [
                            {"teacher_does": "Model.", "children_do": "Repeat.", "minutes": 10},
                            {"teacher_does": "Practise.", "children_do": "Blend.", "minutes": 15},
                        ],
                        "checks": ["Watch for unprompted blending."],
                    },
                }
            )
        )

        started = client.post(reverse("v1:teacher_portal:group-plan", args=[group.pk]))
        assert started.status_code == 202

        plan = client.get(reverse("v1:teacher_portal:group-plan", args=[group.pk]))
        assert plan.status_code == 200
        # No adaptation reply, so the canonical plan is served rather than an error.
        assert plan.data["status"] == PlanStatus.FALLBACK
        assert plan.data["content"]["objective"] == "Blend three sounds."

    def test_opening_a_plan_is_recorded(self, client, teacher):
        group = Group.objects.create(school=teacher.school, teacher=teacher, name="G")
        LessonPlan.objects.create(group=group, content={"objective": "x"}, status=PlanStatus.READY)

        client.get(reverse("v1:teacher_portal:group-plan", args=[group.pk]))
        assert LessonPlan.objects.get(group=group).opened_at is not None

    def test_feedback_is_a_thumb_and_nothing_more(self, client, teacher):
        group = Group.objects.create(school=teacher.school, teacher=teacher, name="G")
        plan = LessonPlan.objects.create(
            group=group, content={"objective": "x"}, status=PlanStatus.READY
        )

        response = client.post(
            reverse("v1:teacher_portal:plan-feedback", args=[plan.pk]),
            {"was_helpful": False},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["was_helpful"] is False

    def test_no_plan_yet_is_a_404_not_an_empty_object(self, client, teacher):
        group = Group.objects.create(school=teacher.school, teacher=teacher, name="G")
        response = client.get(reverse("v1:teacher_portal:group-plan", args=[group.pk]))
        assert response.status_code == 404


class TestAutoFormation:
    def test_it_forms_groups_for_shared_weaknesses(self, client, teacher, subskill):
        for _ in range(4):
            child(teacher, weak=subskill)

        response = client.post(reverse("v1:teacher_portal:group-form"))

        assert response.status_code == 201
        assert len(response.data) == 1
        assert response.data[0]["size"] == 4
