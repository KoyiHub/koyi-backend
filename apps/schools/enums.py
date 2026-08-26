from django.db import models
from django.utils.translation import gettext_lazy as _


class ClassSystem(models.TextChoices):
    """How a school names its year groups. Drives labelling, not structure."""

    GRADE = "grade", _("Grade")
    PRIMARY = "primary", _("Primary")


class ClassName(models.TextChoices):
    """The stream/arm within a grade. Stored as text so "1" sorts predictably
    and so a future "7" is a choices change rather than a column change."""

    ONE = "1", _("1")
    TWO = "2", _("2")
    THREE = "3", _("3")
    FOUR = "4", _("4")
    FIVE = "5", _("5")
    SIX = "6", _("6")


class Gender(models.TextChoices):
    MALE = "male", _("Male")
    FEMALE = "female", _("Female")
    OTHER = "other", _("Other")


class GuardianRelationship(models.TextChoices):
    FATHER = "father", _("Father")
    MOTHER = "mother", _("Mother")
    GRANDPARENT = "grandparent", _("Grandparent")
    SIBLING = "sibling", _("Sibling")
    UNCLE = "uncle", _("Uncle")
    AUNT = "aunt", _("Aunt")
    LEGAL_GUARDIAN = "legal_guardian", _("Legal guardian")
    OTHER = "other", _("Other")
