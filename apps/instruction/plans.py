"""Turning a group into something a teacher can teach from.

Two tiers, for three reasons that all point the same way.

A canonical plan is written once per pedagogical situation and reused. That
puts the expensive reasoning in one place, makes the per-group pass cheap, and
- the part that matters most on a Monday morning - means there is always
something to serve. When generation fails, a teacher gets the canonical plan
rather than an error page.

The group pass adapts it to the children actually in the room. The student pass
is a short note beside it, only for a child whose profile sits away from the
rest; forty individual plans would cost forty times as much and go unread.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.ai.jobs import adapt_plan_for_group, author_canonical_plan, personalise_for_student
from apps.common.enums import ActivityAction, Domain, SkillStateStatus
from apps.common.services import BaseService, NotFoundError
from apps.instruction.enums import PlanStatus
from apps.instruction.models import STABILITY_DAYS, CanonicalLessonPlan, Group, LessonPlan
from apps.schools.models import StudentSkillState

#: A child needs this many weak subskills the group does not share before a
#: personal note is worth writing. Below it, the group plan already covers them.
PERSONALISATION_THRESHOLD = 2


class CanonicalLibraryService(BaseService):
    """Authors and looks up the reusable plans."""

    def get_or_author(
        self, *, domain: str, from_level: int, to_level: int, subskill, resource_tier: str
    ) -> CanonicalLessonPlan | None:
        existing = CanonicalLessonPlan.objects.filter(
            domain=domain,
            from_level=from_level,
            to_level=to_level,
            focus_subskill=subskill,
            resource_tier=resource_tier,
            is_active=True,
        ).first()
        if existing:
            return existing

        outcome = author_canonical_plan(
            domain=domain,
            from_level=from_level,
            to_level=to_level,
            subskill=subskill,
            resource_tier=resource_tier,
        )
        if outcome.value is None:
            return None

        return CanonicalLessonPlan.objects.create(
            domain=domain,
            from_level=from_level,
            to_level=to_level,
            focus_subskill=subskill,
            resource_tier=resource_tier,
            content=outcome.value.as_dict(),
        )


class GroupPlanService(BaseService):
    """The plan a teacher opens for one group."""

    def __init__(self, group: Group) -> None:
        self.group = group
        self.library = CanonicalLibraryService()

    @transaction.atomic
    def generate(self) -> LessonPlan:
        members = list(self.group.current_members.select_related("student"))
        if not members:
            raise NotFoundError("This group has no members to plan for.")

        subskill, from_level = self._focus(members)
        if subskill is None:
            raise NotFoundError(
                "No shared weakness to plan around - these children need different things."
            )

        canonical = self.library.get_or_author(
            domain=self.group.domain or subskill.skill.domain,
            from_level=from_level,
            to_level=min(from_level + 1, 5),
            subskill=subskill,
            resource_tier=self.group.resource_tier,
        )
        weak = self._weak_names(members)
        snapshot = [str(m.student_id) for m in members]
        now = timezone.now()

        if canonical is None:
            # Nothing to adapt and nothing stored. The teacher gets an honest
            # failure rather than an invented plan.
            return LessonPlan.objects.create(
                group=self.group,
                member_snapshot=snapshot,
                status=PlanStatus.FAILED,
                valid_from=now,
                valid_until=now + timedelta(days=STABILITY_DAYS),
            )

        outcome = adapt_plan_for_group(
            canonical=canonical, group=self.group, weak_subskills=weak, size=len(members)
        )
        content = outcome.value.as_dict() if outcome.value else canonical.content
        status = PlanStatus.READY if outcome.value else PlanStatus.FALLBACK

        plan = LessonPlan.objects.create(
            group=self.group,
            canonical_source=canonical,
            member_snapshot=snapshot,
            content=content,
            status=status,
            valid_from=now,
            valid_until=now + timedelta(days=STABILITY_DAYS),
        )
        self._log(plan, len(members))
        return plan

    def _focus(self, members) -> tuple:
        """The subskill most of these children are weak in, and the level to teach it at.

        Most widely shared rather than most severe: a group plan that helps
        four of six children beats one that helps the one furthest behind.
        """
        student_ids = [m.student_id for m in members]
        rows = StudentSkillState.objects.filter(
            student_id__in=student_ids, status=SkillStateStatus.WEAK
        ).select_related("subskill", "subskill__skill")
        if self.group.domain:
            rows = rows.filter(subskill__skill__domain=self.group.domain)

        counts: dict = {}
        for row in rows:
            counts[row.subskill] = counts.get(row.subskill, 0) + 1
        if not counts:
            return None, 1

        subskill = max(counts, key=lambda key: counts[key])
        return subskill, self._level_for(student_ids, subskill)

    def _level_for(self, student_ids, subskill) -> int:
        """Teach at the level the group is working towards, not the one they cleared."""
        from apps.schools.models import StudentProfile

        domain = subskill.skill.domain
        field = "literacy_level" if domain == Domain.LITERACY else "numeracy_level"
        levels = [
            getattr(profile, field)
            for profile in StudentProfile.objects.filter(student_id__in=student_ids)
            if getattr(profile, field) is not None
        ]
        if not levels:
            return subskill.level_range[0]
        # The lowest, so nobody in the group is left behind by the plan.
        return min(levels)

    def _weak_names(self, members) -> list[str]:
        student_ids = [m.student_id for m in members]
        rows = (
            StudentSkillState.objects.filter(
                student_id__in=student_ids, status=SkillStateStatus.WEAK
            )
            .select_related("subskill")
            .values_list("subskill__name", flat=True)
            .distinct()
        )
        return sorted(set(rows))

    def _log(self, plan: LessonPlan, size: int) -> None:
        from apps.common.services import ActivityService

        ActivityService().record(
            school=self.group.school,
            action=ActivityAction.LESSON_PLAN_GENERATED,
            label=f"Lesson plan ready: {self.group.name}",
            description=f"A plan for {size} children was generated.",
            teacher=self.group.teacher,
            metadata={"plan_id": str(plan.pk), "status": plan.status},
        )


class StudentPlanService(BaseService):
    """A short note beside the group plan, where one is warranted."""

    def __init__(self, student, group: Group) -> None:
        self.student = student
        self.group = group

    def generate(self) -> LessonPlan | None:
        group_plan = (
            LessonPlan.objects.filter(
                group=self.group, status__in=[PlanStatus.READY, PlanStatus.FALLBACK]
            )
            .order_by("-created_at")
            .first()
        )
        if group_plan is None:
            return None

        divergent = self._divergent_subskills()
        if len(divergent) < PERSONALISATION_THRESHOLD:
            # The group plan already covers this child. A manufactured note
            # would train the teacher to stop reading them.
            return None

        profile = getattr(self.student, "profile", None)
        outcome = personalise_for_student(
            plan_content=group_plan.content,
            student=self.student,
            weak_subskills=divergent,
            levels={
                "literacy": getattr(profile, "literacy_level", None),
                "numeracy": getattr(profile, "numeracy_level", None),
            },
        )
        if outcome.value is None:
            return None

        now = timezone.now()
        return LessonPlan.objects.create(
            student=self.student,
            canonical_source=group_plan.canonical_source,
            content={"note": outcome.value.summary, "watch_for": outcome.value.attention},
            status=PlanStatus.READY,
            valid_from=now,
            valid_until=group_plan.valid_until,
        )

    def _divergent_subskills(self) -> list[str]:
        """Weaknesses this child has that the rest of the group does not."""
        peers = list(
            self.group.current_members.exclude(student=self.student).values_list(
                "student_id", flat=True
            )
        )
        mine = set(
            StudentSkillState.objects.filter(
                student=self.student, status=SkillStateStatus.WEAK
            ).values_list("subskill__name", flat=True)
        )
        theirs = set(
            StudentSkillState.objects.filter(
                student_id__in=peers, status=SkillStateStatus.WEAK
            ).values_list("subskill__name", flat=True)
        )
        return sorted(mine - theirs)


__all__ = [
    "PERSONALISATION_THRESHOLD",
    "CanonicalLibraryService",
    "GroupPlanService",
    "StudentPlanService",
]
