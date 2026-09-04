"""Turning a group into something a teacher can teach from."""

import pytest

from apps.ai.client import ScriptedClient, register_client, reset_client
from apps.ai.enums import JobType
from apps.common.enums import Domain, SkillStateStatus
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.instruction.enums import PlanStatus, ResourceTier
from apps.instruction.grouping import GroupingService
from apps.instruction.models import CanonicalLessonPlan, Group, LessonPlan
from apps.instruction.plans import CanonicalLibraryService, GroupPlanService, StudentPlanService
from apps.schools.factories import SchoolClassFactory, SchoolFactory, StudentFactory, TeacherFactory
from apps.schools.models import StudentProfile, StudentSkillState

pytestmark = pytest.mark.django_db

PLAN_REPLY = {
    "objective": "Children will blend three sounds into a word.",
    "duration_minutes": 25,
    "materials": ["chalkboard"],
    "steps": [
        {"teacher_does": "Say each sound.", "children_do": "Repeat.", "minutes": 10},
        {"teacher_does": "Blend them.", "children_do": "Blend together.", "minutes": 15},
    ],
    "checks": ["Listen for children blending without prompting."],
    "common_errors": ["Saying the sounds without joining them."],
    "success_criteria": ["Blends a new three-sound word unaided."],
    "note": "",
}


@pytest.fixture(autouse=True)
def _isolate_ai():
    reset_client()
    yield
    reset_client()


@pytest.fixture
def school():
    return SchoolFactory()


@pytest.fixture
def teacher(school):
    return TeacherFactory(school=school, school_class=SchoolClassFactory(school=school))


@pytest.fixture
def subskill():
    skill = SkillFactory(domain=Domain.LITERACY, code="plan_skill", min_level=1, max_level=3)
    return SubskillFactory(skill=skill, code="plan_sub", name="Blending")


@pytest.fixture
def group(school, teacher, subskill):
    """Four children who share one weakness."""
    group = Group.objects.create(
        school=school,
        teacher=teacher,
        name="Blending support",
        domain=Domain.LITERACY,
        resource_tier=ResourceTier.MINIMAL,
    )
    for _ in range(4):
        student = StudentFactory(school=school, school_class=teacher.school_class)
        StudentProfile.objects.create(student=student, literacy_level=2)
        StudentSkillState.objects.create(
            student=student, subskill=subskill, status=SkillStateStatus.WEAK
        )
        GroupingService(school, teacher).add_member(group, student)
    return group


def script(**replies):
    register_client(ScriptedClient(replies=replies))


class TestCanonicalLibrary:
    def test_a_plan_is_authored_once_and_reused(self, subskill):
        script(**{JobType.LESSON_PLAN_CANONICAL: PLAN_REPLY})
        library = CanonicalLibraryService()
        args = {
            "domain": Domain.LITERACY,
            "from_level": 2,
            "to_level": 3,
            "subskill": subskill,
            "resource_tier": ResourceTier.MINIMAL,
        }

        first = library.get_or_author(**args)
        second = library.get_or_author(**args)

        assert first.pk == second.pk
        assert CanonicalLessonPlan.objects.count() == 1

    def test_a_plan_needing_unavailable_materials_is_rejected(self, subskill):
        """Worse than no plan: the teacher finds out mid-lesson."""
        script(**{JobType.LESSON_PLAN_CANONICAL: {**PLAN_REPLY, "materials": ["printed cards"]}})
        result = CanonicalLibraryService().get_or_author(
            domain=Domain.LITERACY,
            from_level=2,
            to_level=3,
            subskill=subskill,
            resource_tier=ResourceTier.MINIMAL,
        )
        assert result is None

    def test_an_equipped_room_accepts_anything(self, subskill):
        script(
            **{
                JobType.LESSON_PLAN_CANONICAL: {
                    **PLAN_REPLY,
                    "materials": ["number rods", "tablets"],
                }
            }
        )
        result = CanonicalLibraryService().get_or_author(
            domain=Domain.LITERACY,
            from_level=2,
            to_level=3,
            subskill=subskill,
            resource_tier=ResourceTier.EQUIPPED,
        )
        assert result is not None


class TestGroupPlan:
    def test_it_adapts_the_canonical_plan(self, group):
        script(
            **{
                JobType.LESSON_PLAN_CANONICAL: PLAN_REPLY,
                JobType.LESSON_PLAN_GROUP: {
                    **PLAN_REPLY,
                    "note": "Four children, keep it whole-class.",
                },
            }
        )
        plan = GroupPlanService(group).generate()

        assert plan.status == PlanStatus.READY
        assert plan.canonical_source is not None
        assert "whole-class" in plan.content["note"]

    def test_it_pins_the_membership_it_was_written_for(self, group):
        """So the plan a teacher is holding stays coherent as children move."""
        script(
            **{
                JobType.LESSON_PLAN_CANONICAL: PLAN_REPLY,
                JobType.LESSON_PLAN_GROUP: PLAN_REPLY,
            }
        )
        plan = GroupPlanService(group).generate()
        assert len(plan.member_snapshot) == 4

    def test_a_failed_adaptation_falls_back_to_the_canonical_plan(self, group):
        """A teacher in front of children never gets an error page."""
        script(**{JobType.LESSON_PLAN_CANONICAL: PLAN_REPLY})

        plan = GroupPlanService(group).generate()

        assert plan.status == PlanStatus.FALLBACK
        assert plan.content["objective"] == PLAN_REPLY["objective"]

    def test_nothing_at_all_is_an_honest_failure_not_an_invention(self, group):
        script()  # no replies for any job

        plan = GroupPlanService(group).generate()

        assert plan.status == PlanStatus.FAILED
        assert plan.content == {}

    def test_an_empty_group_is_refused(self, school, teacher):
        from apps.common.services import NotFoundError

        empty = Group.objects.create(school=school, teacher=teacher, name="Nobody")
        with pytest.raises(NotFoundError, match="no members"):
            GroupPlanService(empty).generate()

    def test_a_group_with_no_shared_weakness_is_refused(self, school, teacher):
        from apps.common.services import NotFoundError

        group = Group.objects.create(
            school=school, teacher=teacher, name="Mixed", domain=Domain.LITERACY
        )
        student = StudentFactory(school=school, school_class=teacher.school_class)
        StudentProfile.objects.create(student=student, literacy_level=2)
        GroupingService(school, teacher).add_member(group, student)

        with pytest.raises(NotFoundError, match="different things"):
            GroupPlanService(group).generate()


class TestStudentPersonalisation:
    def test_a_child_who_matches_the_group_gets_no_note(self, group):
        """A manufactured difference trains the teacher to stop reading them."""
        script(
            **{
                JobType.LESSON_PLAN_CANONICAL: PLAN_REPLY,
                JobType.LESSON_PLAN_GROUP: PLAN_REPLY,
            }
        )
        GroupPlanService(group).generate()
        student = group.current_members.first().student

        assert StudentPlanService(student, group).generate() is None

    def test_a_divergent_child_gets_one(self, group, school):
        script(
            **{
                JobType.LESSON_PLAN_CANONICAL: PLAN_REPLY,
                JobType.LESSON_PLAN_GROUP: PLAN_REPLY,
                JobType.LESSON_PLAN_STUDENT: {
                    "summary": "Amina is further ahead on blending than the rest.",
                    "attention": "May finish step 2 early",
                    "strength": "",
                },
            }
        )
        GroupPlanService(group).generate()

        student = group.current_members.first().student
        extra = SkillFactory(domain=Domain.LITERACY, code="extra_skill")
        for code in ("extra_a", "extra_b"):
            StudentSkillState.objects.create(
                student=student,
                subskill=SubskillFactory(skill=extra, code=code),
                status=SkillStateStatus.WEAK,
            )

        note = StudentPlanService(student, group).generate()
        assert note is not None
        assert note.student_id == student.pk
        assert "further ahead" in note.content["note"]

    def test_no_group_plan_means_no_note(self, group):
        student = group.current_members.first().student
        assert StudentPlanService(student, group).generate() is None


class TestPlanTargets:
    def test_a_plan_targets_a_group_or_a_child_never_both(self, group):
        from django.db.utils import IntegrityError

        student = group.current_members.first().student
        with pytest.raises(IntegrityError):
            LessonPlan.objects.create(group=group, student=student)
