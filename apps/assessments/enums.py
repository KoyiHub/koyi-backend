from django.db import models
from django.utils.translation import gettext_lazy as _


class AssessmentStatus(models.TextChoices):
    """Lifecycle of the paper itself, set by the teacher."""

    PENDING = "pending", _("Pending")
    ACTIVE = "active", _("Active")
    COMPLETED = "completed", _("Completed")


class ResultStatus(models.TextChoices):
    """Lifecycle of one student's attempt at a paper."""

    NOT_STARTED = "not_started", _("Not started")
    IN_PROGRESS = "in_progress", _("In progress")
    FINISHED = "finished", _("Finished")
    GRADED = "graded", _("Graded")
