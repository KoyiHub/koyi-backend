from django.db import models
from django.utils.translation import gettext_lazy as _


class ClassSystem(models.TextChoices):
    """How a school names its year groups. Drives labelling, not structure."""

    GRADE = "grade", _("Grade")
    PRIMARY = "primary", _("Primary")


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
