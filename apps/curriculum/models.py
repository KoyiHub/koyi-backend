"""The reusable question library.

This is the *template* side of the product: subjects, topics, banks and the
questions inside them. Nothing here is bound to a student or a sitting — when
a teacher builds an assessment, the relevant rows are copied into
`apps.assessments` so that editing a bank question later cannot retroactively
change a paper somebody has already sat.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from apps.common.enums import (
    MAX_QUESTION_LEVEL,
    MIN_QUESTION_LEVEL,
    AnswerType,
    ContentBlockType,
    OptionType,
    QuestionType,
)
from apps.common.models import BaseModel

LEVEL_VALIDATORS = [
    MinValueValidator(MIN_QUESTION_LEVEL),
    MaxValueValidator(MAX_QUESTION_LEVEL),
]


class Subject(BaseModel):
    name = models.CharField(_("name"), max_length=128)

    class Meta:
        verbose_name = _("subject")
        verbose_name_plural = _("subjects")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Upper("name"), name="subject_name_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.name


class Topic(BaseModel):
    name = models.CharField(_("name"), max_length=255)

    class Meta:
        verbose_name = _("topic")
        verbose_name_plural = _("topics")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Upper("name"), name="topic_name_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.name


class QuestionLayout(BaseModel):
    """A named rendering template ("two-column", "image-left"), applied by the
    client. Stored as a row rather than a choices field so new layouts ship
    without a migration."""

    name = models.CharField(_("name"), max_length=128)

    class Meta:
        verbose_name = _("question layout")
        verbose_name_plural = _("question layouts")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Upper("name"), name="question_layout_name_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.name


class QuestionBank(BaseModel):
    """A pool of questions for one class/subject/topic combination."""

    name = models.CharField(_("name"), max_length=255)
    school_class = models.ForeignKey(
        "schools.SchoolClass",
        on_delete=models.CASCADE,
        related_name="question_banks",
        verbose_name=_("class"),
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.PROTECT, related_name="question_banks", verbose_name=_("subject")
    )
    topic = models.ForeignKey(
        Topic, on_delete=models.PROTECT, related_name="question_banks", verbose_name=_("topic")
    )

    class Meta:
        verbose_name = _("question bank")
        verbose_name_plural = _("question banks")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "school_class", "subject", "topic"],
                name="question_bank_identity_unique",
            ),
        ]
        indexes = [
            # The bank browser filters on exactly this triple.
            models.Index(
                fields=["school_class", "subject", "topic"], name="qbank_class_subj_topic_idx"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Question(BaseModel):
    """A reusable question.

    `question_bank` is nullable so a question can exist as a draft, or be
    orphaned when its bank is retired, without being destroyed.
    """

    question_bank = models.ForeignKey(
        QuestionBank,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions",
        verbose_name=_("question bank"),
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.PROTECT, related_name="questions", verbose_name=_("subject")
    )
    content = models.TextField(_("content"))
    # Kept alongside the richer `contents` blocks below: these two are the
    # single "headline" media a simple question needs, and every existing
    # client can render them without walking the block list.
    audio_description = models.ForeignKey(
        "media_assets.MediaAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions_described",
        verbose_name=_("audio description"),
    )
    image = models.ForeignKey(
        "media_assets.MediaAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions_illustrated",
        verbose_name=_("image"),
    )
    layout = models.ForeignKey(
        QuestionLayout,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions",
        verbose_name=_("layout"),
    )
    level = models.PositiveSmallIntegerField(
        _("level"),
        default=MIN_QUESTION_LEVEL,
        validators=LEVEL_VALIDATORS,
        help_text=_("Difficulty from 1 (easiest) to 5 (hardest)."),
    )
    type = models.CharField(_("type"), max_length=32, choices=QuestionType.choices)

    class Meta:
        verbose_name = _("question")
        verbose_name_plural = _("questions")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["question_bank", "level"], name="question_bank_level_idx"),
            models.Index(fields=["subject", "type"], name="question_subject_type_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    level__gte=MIN_QUESTION_LEVEL, level__lte=MAX_QUESTION_LEVEL
                ),
                name="question_level_in_range",
            ),
        ]

    def __str__(self) -> str:
        return self.content[:60]

    @property
    def is_option_based(self) -> bool:
        return self.type in QuestionType.option_based()


class QuestionContent(BaseModel):
    """One block in a question body, so a prompt can mix text, an image and an
    audio clip in a defined order."""

    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="contents", verbose_name=_("question")
    )
    type = models.CharField(_("type"), max_length=16, choices=ContentBlockType.choices)
    display_order = models.PositiveSmallIntegerField(_("display order"))
    text_content = models.TextField(_("text content"), blank=True)
    media = models.ForeignKey(
        "media_assets.MediaAsset",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="question_contents",
        verbose_name=_("media"),
    )
    alt_text = models.CharField(_("alt text"), max_length=255, blank=True)
    caption = models.CharField(_("caption"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("question content")
        verbose_name_plural = _("question contents")
        ordering = ["question", "display_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["question", "display_order"], name="question_content_order_unique"
            ),
            # A block that carries neither text nor media renders as nothing.
            models.CheckConstraint(
                condition=~models.Q(text_content="") | models.Q(media__isnull=False),
                name="question_content_requires_text_or_media",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.question_id} #{self.display_order} ({self.type})"


class QuestionAnswer(BaseModel):
    """Declares how a non-option question is expected to be answered.

    One row per question: a question has a single answer format, and modelling
    it one-to-one means a grader can `select_related("answer")`.
    """

    question = models.OneToOneField(
        Question, on_delete=models.CASCADE, related_name="answer", verbose_name=_("question")
    )
    type = models.CharField(_("type"), max_length=16, choices=AnswerType.choices)

    class Meta:
        verbose_name = _("question answer")
        verbose_name_plural = _("question answers")

    def __str__(self) -> str:
        return f"{self.question_id} → {self.type}"


class Option(BaseModel):
    """A choice attached to an option-based question."""

    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="options", verbose_name=_("question")
    )
    option_type = models.CharField(_("option type"), max_length=16, choices=OptionType.choices)
    content = models.TextField(_("content"))
    is_correct = models.BooleanField(_("is correct"), default=False)

    class Meta:
        verbose_name = _("option")
        verbose_name_plural = _("options")
        ordering = ["created_at"]
        indexes = [
            # Marking a paper reads "the correct options for this question".
            models.Index(fields=["question", "is_correct"], name="option_question_correct_idx"),
        ]

    def __str__(self) -> str:
        return self.content[:60]
