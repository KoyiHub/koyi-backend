"""The activity log.

Every core action in the product writes a row here: a teacher publishing an
assessment, a child starting a section, the engine placing a student. The log
is what the school management view reads, filtered by teacher, class or
student, which is why those all appear as nullable foreign keys rather than
being buried in `metadata`.

Rows are written by `apps.common.services.ActivityService` and never edited.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.enums import ActivityAction
from apps.common.models import BaseModel


class Activity(BaseModel):
    """One recorded action.

    `label` and `description` are rendered straight to a person, so they are
    written at record time rather than reconstructed later — the wording
    should survive the referenced rows being renamed or removed.
    """

    school = models.ForeignKey(
        "schools.School",
        on_delete=models.CASCADE,
        related_name="activities",
        verbose_name=_("school"),
    )
    action = models.CharField(_("action"), max_length=32, choices=ActivityAction.choices)
    label = models.CharField(_("label"), max_length=255)
    description = models.TextField(_("description"), blank=True)

    # Who did it. Null for anything the system did on its own.
    actor_user = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
        verbose_name=_("actor"),
    )

    # What it was about. All nullable, all indexed: these are the filters the
    # school management activity feed offers.
    teacher = models.ForeignKey(
        "schools.Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
        verbose_name=_("teacher"),
    )
    student = models.ForeignKey(
        "schools.Student",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
        verbose_name=_("student"),
    )
    school_class = models.ForeignKey(
        "schools.SchoolClass",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
        verbose_name=_("class"),
    )
    assessment = models.ForeignKey(
        "assessments.Assessment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activities",
        verbose_name=_("assessment"),
    )

    metadata = models.JSONField(_("metadata"), default=dict, blank=True)
    occurred_at = models.DateTimeField(_("occurred at"), db_index=True)

    class Meta:
        verbose_name = _("activity")
        verbose_name_plural = _("activities")
        ordering = ["-occurred_at"]
        indexes = [
            # The feed is always "this school, newest first", then narrowed.
            models.Index(fields=["school", "-occurred_at"], name="activity_school_time_idx"),
            models.Index(fields=["school", "teacher"], name="activity_school_teacher_idx"),
            models.Index(fields=["school", "student"], name="activity_school_student_idx"),
            models.Index(fields=["school", "school_class"], name="activity_school_class_idx"),
            models.Index(fields=["school", "action"], name="activity_school_action_idx"),
        ]

    def __str__(self) -> str:
        return self.label


__all__ = ["Activity"]
