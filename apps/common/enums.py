"""Enumerations shared across more than one domain app.

Anything used by a single app lives next to that app's models; putting the
cross-cutting ones here keeps `curriculum` and `assessments` from importing
each other just to agree on what "single_choice" means.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class UserRole(models.TextChoices):
    """Which product surface an authenticated identity belongs to.

    Students are deliberately absent: they never hold a `User` row (see
    `apps.student_portal.authentication`).
    """

    ADMIN = "admin", _("Product admin")
    SCHOOL = "school", _("School management")
    TEACHER = "teacher", _("Teacher")


class MediaType(models.TextChoices):
    IMAGE = "image", _("Image")
    AUDIO = "audio", _("Audio")
    VIDEO = "video", _("Video")
    DOCUMENT = "document", _("Document")


class ContentBlockType(models.TextChoices):
    """The kinds of block a (assessment) question body can be built from."""

    TEXT = "text", _("Text")
    IMAGE = "image", _("Image")
    AUDIO = "audio", _("Audio")
    VIDEO = "video", _("Video")


class QuestionType(models.TextChoices):
    SINGLE_CHOICE = "single_choice", _("Single choice")
    MULTIPLE_CHOICE = "multiple_choice", _("Multiple choice")
    TEXT = "text", _("Text")
    AUDIO = "audio", _("Audio")
    NUMBER = "number", _("Number")
    TRUE_FALSE = "true_false", _("True / false")
    FILE_UPLOAD = "file_upload", _("File upload")

    @classmethod
    def option_based(cls) -> set[str]:
        """Types answered by picking from `Option` rows rather than free input."""
        return {cls.SINGLE_CHOICE, cls.MULTIPLE_CHOICE, cls.TRUE_FALSE}


class AnswerType(models.TextChoices):
    """Shape of the expected answer for questions that are not option-based."""

    TEXT = "text", _("Text")
    AUDIO = "audio", _("Audio")
    FILE_UPLOAD = "file_upload", _("File upload")


class OptionType(models.TextChoices):
    TEXT = "text", _("Text")
    AUDIO = "audio", _("Audio")
    IMAGE = "image", _("Image")
    TRUE_FALSE = "true_false", _("True / false")


class DifficultyLevel(models.TextChoices):
    EASY = "easy", _("Easy")
    MEDIUM = "medium", _("Medium")
    HARD = "hard", _("Hard")


# Questions carry a numeric difficulty ("level") alongside the assessment-wide
# `difficulty`; bound it so analytics can safely bucket on it.
MIN_QUESTION_LEVEL = 1
MAX_QUESTION_LEVEL = 5
