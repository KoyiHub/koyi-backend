"""Who belongs in which group, and when that may change.

Two clocks, deliberately out of step.

Membership runs on the fast one. A child who progresses past a group's criteria
leaves the moment placement says so, because the profile should always tell the
truth about where they are. Freezing them instead would quietly reintroduce the
promotion ladder this product does not have.

The group runs on the slow one. It holds for a stability window regardless, so
a plan written for it survives long enough to be delivered. A group falling
below the minimum size is flagged rather than dissolved - dissolving mid-window
strands whoever is left in it.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.common.enums import ActivityAction, Domain, SkillStateStatus
from apps.common.services import BaseService
from apps.instruction.enums import (
    Comparator,
    CriterionType,
    GroupOrigin,
    GroupStatus,
    MembershipReason,
)
from apps.instruction.models import (
    MIN_GROUP_SIZE,
    STABILITY_DAYS,
    Group,
    GroupCriterion,
    GroupMembership,
)
from apps.schools.models import Student, StudentSkillState


@dataclass(frozen=True, slots=True)
class MembershipChange:
    group: Group
    joined: tuple = ()
    left: tuple = ()
    below_minimum: bool = False


class GroupMatcher:
    """Turns a group's criteria into a queryset of the children who match.

    Built from rows rather than interpreted from JSON, so an impossible rule is
    a save-time error instead of a silent empty group.
    """

    def __init__(self, group: Group) -> None:
        self.group = group

    def matches(self) -> QuerySet[Student]:
        criteria = list(self.group.criteria.select_related("skill", "subskill"))
        if not criteria:
            # A group with no rules matches nobody. Matching everybody would be
            # the more dangerous reading of the same silence.
            return Student.objects.none()

        queryset = Student.objects.filter(school=self.group.school, is_active=True).select_related(
            "school_class"
        )

        for criterion in criteria:
            queryset = self._apply(queryset, criterion)
        return queryset.distinct()

    def _apply(self, queryset: QuerySet[Student], criterion: GroupCriterion) -> QuerySet[Student]:
        if criterion.type == CriterionType.CLASS:
            return queryset.filter(school_class=criterion.school_class)
        if criterion.type == CriterionType.LEVEL:
            return self._by_level(queryset, criterion)
        if criterion.type == CriterionType.SUBSKILL:
            return queryset.filter(
                skill_states__subskill=criterion.subskill,
                skill_states__status=SkillStateStatus.WEAK,
            )
        if criterion.type == CriterionType.SKILL:
            return queryset.filter(
                skill_states__subskill__skill=criterion.skill,
                skill_states__status=SkillStateStatus.WEAK,
            )
        return queryset

    def _by_level(
        self, queryset: QuerySet[Student], criterion: GroupCriterion
    ) -> QuerySet[Student]:
        """Level is per domain, so the group has to say which one it means."""
        domain = self.group.domain or Domain.LITERACY
        field = (
            "profile__literacy_level" if domain == Domain.LITERACY else "profile__numeracy_level"
        )
        # Keyed by str rather than by the enum: the model field comes back as
        # a plain string, and a dict keyed on members would never match it.
        suffixes: dict[str, str] = {
            Comparator.GTE.value: "__gte",
            Comparator.LTE.value: "__lte",
        }
        suffix = suffixes.get(criterion.comparator, "")
        return queryset.filter(**{f"{field}{suffix}": criterion.level})


class GroupingService(BaseService):
    """Reconciles membership after placement, and forms groups that are missing."""

    def __init__(self, school, teacher=None) -> None:
        self.school = school
        self.teacher = teacher

    @transaction.atomic
    def reconcile(self, group: Group) -> MembershipChange:
        """Bring one group's membership in line with its criteria."""
        now = timezone.now()
        matched = set(GroupMatcher(group).matches().values_list("pk", flat=True))
        current = {
            membership.student_id: membership
            for membership in group.memberships.filter(left_at__isnull=True)
        }

        joined = []
        for student_id in matched - set(current):
            GroupMembership.objects.create(
                group=group,
                student_id=student_id,
                joined_at=now,
                join_reason=MembershipReason.MATCHED,
            )
            joined.append(student_id)

        left = []
        for student_id, membership in current.items():
            if student_id in matched:
                continue
            # Only rules-based joins are closed automatically. A child a teacher
            # put here by hand stays until that teacher removes them.
            if membership.join_reason != MembershipReason.MATCHED:
                continue
            membership.left_at = now
            membership.leave_reason = MembershipReason.PROGRESSED
            membership.save(update_fields=["left_at", "leave_reason", "updated_at"])
            left.append(student_id)

        size = group.memberships.filter(left_at__isnull=True).count()
        return MembershipChange(
            group=group,
            joined=tuple(joined),
            left=tuple(left),
            below_minimum=0 < size < MIN_GROUP_SIZE,
        )

    def reconcile_all(self) -> list[MembershipChange]:
        """Every active group in the school. Runs after a cohort is placed."""
        groups = Group.objects.filter(
            school=self.school, status=GroupStatus.ACTIVE
        ).prefetch_related("criteria")
        return [self.reconcile(group) for group in groups]

    @transaction.atomic
    def form_groups(self, *, teacher, domain: str = "") -> list[Group]:
        """Create groups for weaknesses enough children share.

        Only where one does not already exist and the shared weakness clears
        the minimum size, and never touching a group still inside its stability
        window - restructuring faster than teaching happens is the failure this
        guards against.
        """
        now = timezone.now()
        student_ids = self._students_of(teacher)
        if not student_ids:
            return []

        existing = self._existing_subskill_groups(teacher)
        created = []

        for subskill, count in self._shared_weaknesses(student_ids, domain).items():
            if count < MIN_GROUP_SIZE or subskill.pk in existing:
                continue
            group = Group.objects.create(
                school=self.school,
                teacher=teacher,
                name=f"{subskill.name} support",
                domain=subskill.skill.domain,
                origin=GroupOrigin.AUTO,
                stable_until=now + timedelta(days=STABILITY_DAYS),
            )
            GroupCriterion.objects.create(
                group=group, type=CriterionType.SUBSKILL, subskill=subskill
            )
            self.reconcile(group)
            self._log(group, count)
            created.append(group)
        return created

    def _students_of(self, teacher) -> list:
        """The children this teacher handles, via the classes they are assigned."""
        return list(
            Student.objects.filter(
                school=self.school, is_active=True, school_class__teachers=teacher
            ).values_list("pk", flat=True)
        )

    def _existing_subskill_groups(self, teacher) -> set:
        return set(
            GroupCriterion.objects.filter(
                group__teacher=teacher,
                group__status=GroupStatus.ACTIVE,
                type=CriterionType.SUBSKILL,
            ).values_list("subskill_id", flat=True)
        )

    def _shared_weaknesses(self, student_ids: list, domain: str) -> dict:
        rows = StudentSkillState.objects.filter(
            student_id__in=student_ids, status=SkillStateStatus.WEAK
        ).select_related("subskill", "subskill__skill")
        if domain:
            rows = rows.filter(subskill__skill__domain=domain)

        counts: dict = {}
        for row in rows:
            counts[row.subskill] = counts.get(row.subskill, 0) + 1
        # Most widely shared first, so the biggest win is formed even if a later
        # one would have overlapped with it.
        return dict(sorted(counts.items(), key=lambda pair: -pair[1]))

    def add_member(self, group: Group, student: Student) -> GroupMembership:
        """A teacher's own judgement, which the rules will not undo."""
        existing = group.memberships.filter(student=student, left_at__isnull=True).first()
        if existing:
            return existing
        return GroupMembership.objects.create(
            group=group,
            student=student,
            joined_at=timezone.now(),
            join_reason=MembershipReason.ADDED,
        )

    def remove_member(self, group: Group, student: Student) -> None:
        group.memberships.filter(student=student, left_at__isnull=True).update(
            left_at=timezone.now(), leave_reason=MembershipReason.REMOVED
        )

    def archive(self, group: Group) -> Group:
        now = timezone.now()
        group.memberships.filter(left_at__isnull=True).update(
            left_at=now, leave_reason=MembershipReason.ARCHIVED
        )
        group.status = GroupStatus.ARCHIVED
        group.save(update_fields=["status", "updated_at"])
        return group

    def restructurable(self) -> QuerySet[Group]:
        """Groups whose stability window has passed."""
        now = timezone.now()
        return Group.objects.filter(school=self.school, status=GroupStatus.ACTIVE).filter(
            Q(stable_until__isnull=True) | Q(stable_until__lte=now)
        )

    def _log(self, group: Group, count: int) -> None:
        from apps.common.services import ActivityService

        ActivityService().record(
            school=self.school,
            action=ActivityAction.GROUP_CREATED,
            label=f"Group formed: {group.name}",
            description=f"{count} children share this weakness.",
            teacher=group.teacher,
            metadata={"group_id": str(group.pk), "size": count},
        )


__all__ = ["GroupMatcher", "GroupingService", "MembershipChange"]
