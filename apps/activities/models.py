"""Remediation activities.

The catalogue an analysis draws from: once a weak topic is identified for a
student, activities are matched by tag.
"""

from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel


class ActivityTag(BaseModel):
    """A free-form label ("fractions", "listening") activities are matched on."""

    value = models.CharField(_("value"), max_length=64)

    class Meta:
        verbose_name = _("activity tag")
        verbose_name_plural = _("activity tags")
        ordering = ["value"]
        constraints = [
            models.UniqueConstraint(Upper("value"), name="activity_tag_value_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.value


class Activity(BaseModel):
    label = models.CharField(_("label"), max_length=255, db_index=True)
    description = models.TextField(_("description"), blank=True)
    tags = models.ManyToManyField(
        ActivityTag, related_name="activities", blank=True, verbose_name=_("tags")
    )

    class Meta:
        verbose_name = _("activity")
        verbose_name_plural = _("activities")
        ordering = ["label"]

    def __str__(self) -> str:
        return self.label
