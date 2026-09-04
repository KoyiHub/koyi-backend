from django.db import models
from django.utils.translation import gettext_lazy as _


class AssessmentStatus(models.TextChoices):
    """Lifecycle of the paper itself.

    `DRAFT` is where a teacher builds: sections and questions can be added,
    edited and removed freely. `publish` is the one-way door — it validates,
    snapshots every selected bank question, mints the code children type, and
    from then on the paper is immutable because children may already have sat
    it. `CLOSED` stops new sittings; marking and placement run afterwards.
    """

    DRAFT = "draft", _("Draft")
    PUBLISHED = "published", _("Published")
    OPEN = "open", _("Open")
    CLOSED = "closed", _("Closed")

    @classmethod
    def editable(cls) -> set[str]:
        """Statuses in which the paper's content may still change."""
        return {cls.DRAFT}

    @classmethod
    def sittable(cls) -> set[str]:
        """Statuses in which a child may start or continue a section."""
        return {cls.PUBLISHED, cls.OPEN}


class SectionResultStatus(models.TextChoices):
    """Progress through one section, which is one sitting."""

    LOCKED = "locked", _("Locked")
    UNLOCKED = "unlocked", _("Unlocked")
    IN_PROGRESS = "in_progress", _("In progress")
    SUBMITTED = "submitted", _("Submitted")


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


class CellOutcome(models.TextChoices):
    """Whether one (subskill x level) cell was demonstrated.

    Two values only. A cell with no items is not recorded at all, so absence
    means "not probed" and there is no third state to reason about.
    """

    PASS = "pass", _("Pass")
    FAIL = "fail", _("Fail")
