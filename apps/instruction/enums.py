from django.db import models
from django.utils.translation import gettext_lazy as _


class GroupOrigin(models.TextChoices):
    MANUAL = "manual", _("Created by a teacher")
    AUTO = "auto", _("Formed by the system")


class GroupStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    ARCHIVED = "archived", _("Archived")


class CriterionType(models.TextChoices):
    """What a rule tests. All optional, all ANDed together."""

    LEVEL = "level", _("FLN level")
    SKILL = "skill", _("Weak in a skill")
    SUBSKILL = "subskill", _("Weak in a subskill")
    CLASS = "class", _("In a class")


class Comparator(models.TextChoices):
    """Only meaningful on a level criterion."""

    EQ = "eq", _("Equals")
    GTE = "gte", _("At or above")
    LTE = "lte", _("At or below")


class MembershipReason(models.TextChoices):
    MATCHED = "matched", _("Matched the criteria")
    ADDED = "added", _("Added by a teacher")
    PROGRESSED = "progressed", _("Progressed past the criteria")
    REMOVED = "removed", _("Removed by a teacher")
    ARCHIVED = "archived", _("Group archived")


class PlanStatus(models.TextChoices):
    GENERATING = "generating", _("Generating")
    READY = "ready", _("Ready")
    FAILED = "failed", _("Failed")
    #: Served from the canonical library because generation did not work.
    FALLBACK = "fallback", _("Fallback")


class ResourceTier(models.TextChoices):
    """What a classroom has to teach with.

    Part of the canonical plan's identity: a plan that assumes printed cards
    is useless where there are none, and one that assumes nothing wastes a
    room that has them.
    """

    MINIMAL = "minimal", _("Chalkboard and voice only")
    BASIC = "basic", _("Paper, printed materials")
    EQUIPPED = "equipped", _("Manipulatives, some devices")
