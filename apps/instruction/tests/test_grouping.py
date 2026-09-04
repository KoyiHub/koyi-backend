"""Who ends up in a group, and when that is allowed to change."""

import pytest
from django.utils import timezone

from apps.common.enums import Domain, SkillStateStatus
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.instruction.enums import Comparator, CriterionType, GroupOrigin, MembershipReason
from apps.instruction.grouping import GroupingService
from apps.instruction.models import MIN_GROUP_SIZE, Group, GroupCriterion
from apps.schools.factories import (
    SchoolClassFactory,
    SchoolFactory,
    StudentFactory,
    TeacherFactory,
)
from apps.schools.models import StudentProfile, StudentSkillState

pytestmark = pytest.mark.django_db


@pytest.fixture
def school():
    return SchoolFactory()


@pytest.fixture
def teacher(school):
    school_class = SchoolClassFactory(school=school)
    return TeacherFactory(school=school, school_class=school_class)


@pytest.fixture
def grouping(school, teacher):
    return GroupingService(school, teacher)


@pytest.fixture
def subskill():
    skill = SkillFactory(domain=Domain.LITERACY, code="grp_skill", min_level=1, max_level=3)
    return SubskillFactory(skill=skill, code="grp_sub", name="Blending")


def child(school, *, literacy=None, numeracy=None, weak=None, school_class=None):
    # An active student always has a class - the model enforces it, so the
    # helper has to supply one rather than leaving it null.
    student = StudentFactory(
        school=school, school_class=school_class or SchoolClassFactory(school=school)
    )
    StudentProfile.objects.create(student=student, literacy_level=literacy, numeracy_level=numeracy)
    if weak is not None:
        StudentSkillState.objects.create(
            student=student, subskill=weak, status=SkillStateStatus.WEAK
        )
    return student


def group_with(school, teacher, **criteria) -> Group:
    group = Group.objects.create(
        school=school, teacher=teacher, name="Test group", domain=Domain.LITERACY
    )
    for kind, value in criteria.items():
        if kind == "level":
            GroupCriterion.objects.create(
                group=group, type=CriterionType.LEVEL, level=value, comparator=Comparator.EQ
            )
        elif kind == "subskill":
            GroupCriterion.objects.create(group=group, type=CriterionType.SUBSKILL, subskill=value)
        elif kind == "school_class":
            GroupCriterion.objects.create(group=group, type=CriterionType.CLASS, school_class=value)
    return group


class TestMatching:
    def test_a_level_criterion_gathers_children_at_that_level(self, school, teacher, grouping):
        wanted = child(school, literacy=2)
        child(school, literacy=3)
        group = group_with(school, teacher, level=2)

        grouping.reconcile(group)
        assert [m.student_id for m in group.current_members] == [wanted.pk]

    def test_at_or_above_widens_it(self, school, teacher, grouping):
        child(school, literacy=1)
        child(school, literacy=3)
        group = Group.objects.create(
            school=school, teacher=teacher, name="L2+", domain=Domain.LITERACY
        )
        GroupCriterion.objects.create(
            group=group, type=CriterionType.LEVEL, level=2, comparator=Comparator.GTE
        )

        grouping.reconcile(group)
        assert group.size == 1

    def test_level_reads_the_group_s_own_domain(self, school, teacher, grouping):
        """A numeracy group must not gather children by their literacy level."""
        child(school, literacy=2, numeracy=4)
        group = Group.objects.create(
            school=school, teacher=teacher, name="Numeracy 2", domain=Domain.NUMERACY
        )
        GroupCriterion.objects.create(
            group=group, type=CriterionType.LEVEL, level=2, comparator=Comparator.EQ
        )

        grouping.reconcile(group)
        assert group.size == 0

    def test_criteria_are_anded(self, school, teacher, grouping, subskill):
        school_class = SchoolClassFactory(school=school)
        child(school, literacy=2, weak=subskill, school_class=school_class)
        child(school, literacy=2)  # right level, not weak
        child(school, literacy=3, weak=subskill)  # weak, wrong level

        group = group_with(school, teacher, level=2, subskill=subskill)
        grouping.reconcile(group)
        assert group.size == 1

    def test_a_group_with_no_rules_matches_nobody(self, school, teacher, grouping):
        """Matching everybody would be the more dangerous reading of silence."""
        child(school, literacy=2)
        group = Group.objects.create(school=school, teacher=teacher, name="Empty")

        grouping.reconcile(group)
        assert group.size == 0

    def test_another_school_s_children_are_never_matched(self, school, teacher, grouping):
        child(SchoolFactory(), literacy=2)
        group = group_with(school, teacher, level=2)

        grouping.reconcile(group)
        assert group.size == 0


class TestMembershipIsLive:
    def test_a_child_who_progresses_leaves_immediately(self, school, teacher, grouping):
        """The profile should always tell the truth about where a child is."""
        student = child(school, literacy=2)
        group = group_with(school, teacher, level=2)
        grouping.reconcile(group)
        assert group.size == 1

        StudentProfile.objects.filter(student=student).update(literacy_level=3)
        grouping.reconcile(group)

        assert group.size == 0
        membership = group.memberships.get(student=student)
        assert membership.left_at is not None
        assert membership.leave_reason == MembershipReason.PROGRESSED

    def test_a_teacher_s_own_addition_survives_reconciliation(self, school, teacher, grouping):
        """A teacher's judgement outranks a criterion they did not write."""
        student = child(school, literacy=5)
        group = group_with(school, teacher, level=2)
        grouping.add_member(group, student)

        grouping.reconcile(group)
        assert group.size == 1

    def test_rejoining_opens_a_second_row_rather_than_reusing_the_first(
        self, school, teacher, grouping
    ):
        student = child(school, literacy=2)
        group = group_with(school, teacher, level=2)
        grouping.reconcile(group)

        StudentProfile.objects.filter(student=student).update(literacy_level=3)
        grouping.reconcile(group)
        StudentProfile.objects.filter(student=student).update(literacy_level=2)
        grouping.reconcile(group)

        # History, not a toggle: two spells, one of them still open.
        assert group.memberships.filter(student=student).count() == 2
        assert group.size == 1

    def test_reconciling_twice_changes_nothing(self, school, teacher, grouping):
        child(school, literacy=2)
        group = group_with(school, teacher, level=2)

        grouping.reconcile(group)
        grouping.reconcile(group)
        assert group.memberships.count() == 1


class TestGroupIsSlow:
    def test_a_thin_group_is_flagged_not_dissolved(self, school, teacher, grouping):
        """Dissolving mid-window strands whoever is left in it."""
        child(school, literacy=2)
        group = group_with(school, teacher, level=2)

        change = grouping.reconcile(group)
        assert change.below_minimum is True
        group.refresh_from_db()
        assert group.status == "active"

    def test_a_new_group_gets_a_stability_window(self, school, teacher, grouping, subskill):
        for _ in range(MIN_GROUP_SIZE):
            child(school, weak=subskill, school_class=teacher.school_class)

        created = grouping.form_groups(teacher=teacher)
        assert len(created) == 1
        assert created[0].stable_until > timezone.now()
        assert created[0] not in grouping.restructurable()

    def test_a_window_that_has_passed_makes_a_group_restructurable(self, school, teacher, grouping):
        group = group_with(school, teacher, level=2)
        group.stable_until = timezone.now() - timezone.timedelta(days=1)
        group.save(update_fields=["stable_until"])

        assert group in grouping.restructurable()


class TestAutoFormation:
    def test_it_forms_a_group_for_a_widely_shared_weakness(
        self, school, teacher, grouping, subskill
    ):
        for _ in range(MIN_GROUP_SIZE):
            child(school, weak=subskill, school_class=teacher.school_class)

        created = grouping.form_groups(teacher=teacher)

        assert len(created) == 1
        assert created[0].origin == GroupOrigin.AUTO
        assert created[0].size == MIN_GROUP_SIZE

    def test_it_refuses_below_the_minimum(self, school, teacher, grouping, subskill):
        """A group of two is a plan no teacher will run."""
        for _ in range(MIN_GROUP_SIZE - 1):
            child(school, weak=subskill, school_class=teacher.school_class)

        assert grouping.form_groups(teacher=teacher) == []

    def test_it_does_not_duplicate_an_existing_group(self, school, teacher, grouping, subskill):
        for _ in range(MIN_GROUP_SIZE):
            child(school, weak=subskill, school_class=teacher.school_class)

        grouping.form_groups(teacher=teacher)
        assert grouping.form_groups(teacher=teacher) == []

    def test_it_only_looks_at_this_teacher_s_children(self, school, teacher, grouping, subskill):
        other_class = SchoolClassFactory(school=school)
        for _ in range(MIN_GROUP_SIZE):
            child(school, weak=subskill, school_class=other_class)

        assert grouping.form_groups(teacher=teacher) == []


class TestArchiving:
    def test_archiving_closes_every_membership(self, school, teacher, grouping):
        child(school, literacy=2)
        group = group_with(school, teacher, level=2)
        grouping.reconcile(group)

        grouping.archive(group)

        assert group.size == 0
        assert group.memberships.first().leave_reason == MembershipReason.ARCHIVED
