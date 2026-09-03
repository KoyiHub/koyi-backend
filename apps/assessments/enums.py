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


class GradedBy(models.TextChoices):
    """What marked a response. `AI` results carry a confidence."""

    AUTO = "auto", _("Automatic")
    AI = "ai", _("AI")
    TEACHER = "teacher", _("Teacher")


class ErrorType(models.TextChoices):
    """How an answer was wrong.

    A closed set so remediation can group on it: the marker is already paying
    for the judgement, and a constrained classification costs nothing extra.
    """

    NO_RESPONSE = "no_response", _("No response")
    SUBSTITUTION = "substitution", _("Substitution")
    OMISSION = "omission", _("Omission")
    INSERTION = "insertion", _("Insertion")
    REVERSAL = "reversal", _("Reversal")
    SELF_CORRECTED = "self_corrected", _("Self-corrected")
    PLACE_VALUE = "place_value", _("Place-value error")
    OPERATION_CONFUSION = "operation_confusion", _("Wrong operation")
    COMPUTATION = "computation", _("Computation slip")
    PARTIAL = "partial", _("Partially correct")
    OTHER = "other", _("Other")
