from django.db import models
from django.utils.translation import gettext_lazy as _


class JobType(models.TextChoices):
    """The distinct things a model is asked to do.

    Each carries its own prompt documents, so no call loads guidance it does
    not need, and each gets its own cache namespace as a result.
    """

    MARK_TEXT_RESPONSE = "mark_text_response", _("Mark a written answer")
    MARK_AUDIO_RESPONSE = "mark_audio_response", _("Mark a spoken answer")
    SUGGEST_QUESTION_TAGS = "suggest_question_tags", _("Suggest subskill and level")
    ASSESSMENT_ANALYTICS = "assessment_analytics", _("Explain a class result")
    STUDENT_SKILL_NARRATIVE = "student_skill_narrative", _("Explain one child's result")
    LESSON_PLAN_CANONICAL = "lesson_plan_canonical", _("Author a canonical plan")
    LESSON_PLAN_GROUP = "lesson_plan_group", _("Adapt a plan to a group")
    LESSON_PLAN_STUDENT = "lesson_plan_student", _("Personalise for one child")


class GenerationStatus(models.TextChoices):
    SUCCEEDED = "succeeded", _("Succeeded")
    FAILED = "failed", _("Failed")
    #: Returned something, but not something we could use.
    INVALID = "invalid", _("Invalid output")
