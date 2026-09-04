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


class Domain(models.TextChoices):
    """The two halves of foundational learning.

    A closed set by design: placement branches on it constantly, and a third
    value appearing would silently invalidate every level computation.
    """

    LITERACY = "literacy", _("Literacy")
    NUMERACY = "numeracy", _("Numeracy")


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


class QuestionLayout(models.TextChoices):
    """How the client renders a question.

    A closed set rather than a table: a layout the client has no rendering
    code for is useless, so a new layout ships with a client release either
    way. Being an enum buys validation the table could not give.
    """

    MEDIA_GRID_CHOICE = "media_grid_choice", _("Media grid")
    MEDIA_LIST_CHOICE = "media_list_choice", _("Media list")
    COMPARISON_PANEL_CHOICE = "comparison_panel_choice", _("Comparison panel")
    SPEECH_RESPONSE_PROMPT = "speech_response_prompt", _("Speech response")
    PASSAGE_COMPREHENSION_CHOICE = "passage_comprehension_choice", _("Passage comprehension")

    @classmethod
    def option_layouts(cls) -> set[str]:
        """Layouts that render answer options; the rest expect none."""
        return {
            cls.MEDIA_GRID_CHOICE,
            cls.MEDIA_LIST_CHOICE,
            cls.COMPARISON_PANEL_CHOICE,
            cls.PASSAGE_COMPREHENSION_CHOICE,
        }


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


class SkillStateStatus(models.TextChoices):
    """Where a child currently stands on one subskill.

    Lives here rather than in `assessments` because `schools` reads it too, and
    a foreign key between those two apps in both directions costs an extra
    migration in each.
    """

    NOT_ASSESSED = "not_assessed", _("Not assessed")
    WEAK = "weak", _("Weak")
    DEVELOPING = "developing", _("Developing")
    MASTERED = "mastered", _("Mastered")


class ActivityAction(models.TextChoices):
    """Core actions recorded in the audit log."""

    ASSESSMENT_CREATED = "assessment_created", _("Assessment created")
    ASSESSMENT_PUBLISHED = "assessment_published", _("Assessment published")
    ASSESSMENT_ASSIGNED = "assessment_assigned", _("Assessment assigned")
    SECTION_STARTED = "section_started", _("Section started")
    SECTION_SUBMITTED = "section_submitted", _("Section submitted")
    ASSESSMENT_SUBMITTED = "assessment_submitted", _("Assessment submitted")
    ASSESSMENT_MARKED = "assessment_marked", _("Assessment marked")
    STUDENT_PLACED = "student_placed", _("Student placed")
    GROUP_CREATED = "group_created", _("Group created")
    GROUP_MEMBER_ADDED = "group_member_added", _("Group member added")
    GROUP_MEMBER_REMOVED = "group_member_removed", _("Group member removed")
    LESSON_PLAN_GENERATED = "lesson_plan_generated", _("Lesson plan generated")
    STUDENT_CREATED = "student_created", _("Student created")
    STUDENT_TRANSFERRED = "student_transferred", _("Student transferred")
    STUDENT_DISABLED = "student_disabled", _("Student disabled")
    TEACHER_CREATED = "teacher_created", _("Teacher created")
    TEACHER_DISABLED = "teacher_disabled", _("Teacher disabled")


#: FLN developmental levels. Not a difficulty rating — these are the five
#: ability bands the whole product is organised around.
MIN_FLN_LEVEL = 1
MAX_FLN_LEVEL = 5
FLN_LEVELS = range(MIN_FLN_LEVEL, MAX_FLN_LEVEL + 1)
