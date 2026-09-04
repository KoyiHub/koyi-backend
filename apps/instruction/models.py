"""Groups, and the plans that target them.

The teaching half of the loop. Placement says what a child needs; a group
gathers the children who need the same thing; a lesson plan says how to teach
it.

Two things shape the design and are worth stating before the fields.

**Membership is live, the group is slow.** A child who progresses past a
group's criteria leaves immediately - the profile should always tell the truth
about where they are. But the group and its plan persist for a stability
window, because a plan re-scoped every few days is never taught. That split is
deliberate; freezing the child instead would quietly reintroduce the promotion
ladder this product does not have.

**Plans are advice.** There is no edit workflow and no approval queue. A
teacher reads one and decides; the only signal back is whether it helped.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.enums import MAX_FLN_LEVEL, MIN_FLN_LEVEL, Domain
from apps.common.models import BaseModel
from apps.instruction.enums import (
    Comparator,
    CriterionType,
    GroupOrigin,
    GroupStatus,
    MembershipReason,
    PlanStatus,
    ResourceTier,
)

#: Below this, the right answer is individual remediation. A "group" of two is
#: a lesson plan no teacher will run.
MIN_GROUP_SIZE = 4

#: How long a group is left alone before auto-restructuring may touch it, so a
#: plan survives long enough to be delivered.
STABILITY_DAYS = 14


class Group(BaseModel):
    """Children who need the same thing next.

    Scoped to a teacher rather than a class: grouping by demonstrated level is
    the point, and a level cuts across year groups.
    """

    school = models.ForeignKey(
        "schools.School",
        on_delete=models.CASCADE,
        related_name="groups",
        verbose_name=_("school"),
    )
    teacher = models.ForeignKey(
        "schools.Teacher",
        on_delete=models.CASCADE,
        related_name="groups",
        verbose_name=_("teacher"),
    )
    name = models.CharField(_("name"), max_length=255)
    domain = models.CharField(_("domain"), max_length=16, choices=Domain.choices, blank=True)
    origin = models.CharField(
        _("origin"), max_length=16, choices=GroupOrigin.choices, default=GroupOrigin.MANUAL
    )
    is_primary = models.BooleanField(
        _("is primary"),
        default=False,
        help_text=_(
            "The group whose plan the teacher actually runs. A child matches "
            "several; without this the teacher opens Monday to competing plans."
        ),
    )
    status = models.CharField(
        _("status"), max_length=16, choices=GroupStatus.choices, default=GroupStatus.ACTIVE
    )
    resource_tier = models.CharField(
        _("resource tier"),
        max_length=16,
        choices=ResourceTier.choices,
        default=ResourceTier.BASIC,
    )
    #: When restructuring may next touch this group.
    stable_until = models.DateTimeField(_("stable until"), null=True, blank=True)

    class Meta:
        verbose_name = _("group")
        verbose_name_plural = _("groups")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["teacher", "status"], name="group_teacher_status_idx"),
            models.Index(fields=["school", "domain"], name="group_school_domain_idx"),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def current_members(self):
        return self.memberships.filter(left_at__isnull=True)

    @property
    def size(self) -> int:
        return self.current_members.count()


class GroupCriterion(BaseModel):
    """One rule. A group's rules are ANDed, and any of them may be absent.

    Normalised rather than a JSON blob because the types are a closed set: a
    queryset can be built from these deterministically, and a nonsense rule is
    rejected when it is saved rather than when it next runs.
    """

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="criteria", verbose_name=_("group")
    )
    type = models.CharField(_("type"), max_length=16, choices=CriterionType.choices)
    comparator = models.CharField(
        _("comparator"), max_length=8, choices=Comparator.choices, blank=True
    )
    level = models.PositiveSmallIntegerField(_("level"), null=True, blank=True)
    skill = models.ForeignKey(
        "curriculum.Skill",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="group_criteria",
        verbose_name=_("skill"),
    )
    subskill = models.ForeignKey(
        "curriculum.Subskill",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="group_criteria",
        verbose_name=_("subskill"),
    )
    school_class = models.ForeignKey(
        "schools.SchoolClass",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="group_criteria",
        verbose_name=_("class"),
    )

    class Meta:
        verbose_name = _("group criterion")
        verbose_name_plural = _("group criteria")
        ordering = ["group", "type"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(level__isnull=True)
                | models.Q(level__gte=MIN_FLN_LEVEL, level__lte=MAX_FLN_LEVEL),
                name="group_criterion_level_in_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.group_id}: {self.type}"


class GroupMembership(BaseModel):
    """That a child was in a group, and when.

    History rather than a many-to-many, for two reasons. A plan is pinned to
    the membership it was generated for, so it stays coherent as children move.
    And "why is this child in this group" is a question a teacher will ask
    about a decision the system made on its own.
    """

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name="memberships", verbose_name=_("group")
    )
    student = models.ForeignKey(
        "schools.Student",
        on_delete=models.CASCADE,
        related_name="group_memberships",
        verbose_name=_("student"),
    )
    joined_at = models.DateTimeField(_("joined at"))
    left_at = models.DateTimeField(
        _("left at"), null=True, blank=True, help_text=_("Null while a current member.")
    )
    join_reason = models.CharField(
        _("join reason"), max_length=16, choices=MembershipReason.choices, blank=True
    )
    leave_reason = models.CharField(
        _("leave reason"), max_length=16, choices=MembershipReason.choices, blank=True
    )

    class Meta:
        verbose_name = _("group membership")
        verbose_name_plural = _("group memberships")
        ordering = ["-joined_at"]
        constraints = [
            # A child may rejoin later, so uniqueness holds only over the open
            # row - two live memberships in one group is the bug to prevent.
            models.UniqueConstraint(
                fields=["group", "student"],
                condition=models.Q(left_at__isnull=True),
                name="group_membership_one_open_per_student",
            ),
        ]
        indexes = [
            models.Index(fields=["group", "left_at"], name="membership_group_open_idx"),
            models.Index(fields=["student", "left_at"], name="membership_student_open_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student_id} in {self.group_id}"


class CanonicalLessonPlan(BaseModel):
    """A plan for one pedagogical situation, authored once and reused.

    The expensive reasoning happens here rather than per group. Beyond cost,
    this is what gives the product a fallback: when generation fails, a teacher
    still gets a plan instead of an error page.
    """

    domain = models.CharField(_("domain"), max_length=16, choices=Domain.choices)
    from_level = models.PositiveSmallIntegerField(_("from level"))
    to_level = models.PositiveSmallIntegerField(_("to level"))
    focus_subskill = models.ForeignKey(
        "curriculum.Subskill",
        on_delete=models.CASCADE,
        related_name="canonical_plans",
        verbose_name=_("focus subskill"),
    )
    resource_tier = models.CharField(
        _("resource tier"), max_length=16, choices=ResourceTier.choices
    )
    content = models.JSONField(_("content"), default=dict)
    is_active = models.BooleanField(_("is active"), default=True)

    class Meta:
        verbose_name = _("canonical lesson plan")
        verbose_name_plural = _("canonical lesson plans")
        ordering = ["domain", "from_level", "focus_subskill"]
        constraints = [
            models.UniqueConstraint(
                fields=["domain", "from_level", "to_level", "focus_subskill", "resource_tier"],
                name="canonical_plan_signature_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["focus_subskill", "from_level", "resource_tier"],
                name="canonical_lookup_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.domain} L{self.from_level}->{self.to_level} {self.focus_subskill_id}"


class LessonPlan(BaseModel):
    """What a teacher actually reads.

    Targets a group or one child, never both. The group plan is what gets
    taught; a student plan is a short personalisation over it for a child whose
    profile sits away from the rest.
    """

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="lesson_plans",
        verbose_name=_("group"),
    )
    student = models.ForeignKey(
        "schools.Student",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="lesson_plans",
        verbose_name=_("student"),
    )
    canonical_source = models.ForeignKey(
        CanonicalLessonPlan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adaptations",
        verbose_name=_("canonical source"),
    )
    member_snapshot = models.JSONField(
        _("member snapshot"),
        default=list,
        help_text=_("Who was in the group when this was written, so it stays coherent."),
    )
    content = models.JSONField(_("content"), default=dict)
    status = models.CharField(
        _("status"), max_length=16, choices=PlanStatus.choices, default=PlanStatus.GENERATING
    )
    valid_from = models.DateTimeField(_("valid from"), null=True, blank=True)
    valid_until = models.DateTimeField(_("valid until"), null=True, blank=True)

    # The only feedback there is. Plans are advice, not documents to revise,
    # so there is no edit to learn from - these two are the whole signal.
    was_helpful = models.BooleanField(_("was helpful"), null=True, blank=True)
    opened_at = models.DateTimeField(_("opened at"), null=True, blank=True)

    class Meta:
        verbose_name = _("lesson plan")
        verbose_name_plural = _("lesson plans")
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(group__isnull=False, student__isnull=True)
                    | models.Q(group__isnull=True, student__isnull=False)
                ),
                name="lesson_plan_targets_one_thing",
            ),
        ]
        indexes = [
            models.Index(fields=["group", "-created_at"], name="plan_group_created_idx"),
            models.Index(fields=["student", "-created_at"], name="plan_student_created_idx"),
        ]

    def __str__(self) -> str:
        return f"Plan for {self.group_id or self.student_id}"


__all__ = [
    "MIN_GROUP_SIZE",
    "STABILITY_DAYS",
    "CanonicalLessonPlan",
    "Group",
    "GroupCriterion",
    "GroupMembership",
    "LessonPlan",
]
