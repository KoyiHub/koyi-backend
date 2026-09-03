"""Papers, sittings and their results.

Assessment questions are *copies*, not references, of `curriculum.Question`.
That duplication is deliberate: once a paper has been sat, editing the source
question in the bank must not silently change what a student was asked or how
they were marked.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.assessments.enums import AssessmentStatus, ErrorType, GradedBy, ResultStatus
from apps.common.enums import (
    MAX_FLN_LEVEL,
    MIN_FLN_LEVEL,
    ContentBlockType,
    OptionType,
    QuestionLayout,
    QuestionType,
)
from apps.common.models import BaseModel

LEVEL_VALIDATORS = [
    MinValueValidator(MIN_FLN_LEVEL),
    MaxValueValidator(MAX_FLN_LEVEL),
]


class Assessment(BaseModel):
    """A paper set for one class, in one subject, within a session."""

    school = models.ForeignKey(
        "schools.School",
        on_delete=models.CASCADE,
        related_name="assessments",
        verbose_name=_("school"),
    )
    teacher = models.ForeignKey(
        "schools.Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assessments",
        verbose_name=_("teacher"),
        help_text=_("Cleared rather than cascaded so a paper outlives its author."),
    )
    session = models.ForeignKey(
        "schools.AcademicSession",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assessments",
        verbose_name=_("session"),
    )
    name = models.CharField(_("name"), max_length=255)
    instructions = models.TextField(_("instructions"), blank=True)
    due_date = models.DateTimeField(_("due date"))
    timer = models.DurationField(
        _("timer"),
        null=True,
        blank=True,
        help_text=_("Time allowed per attempt. Null means untimed."),
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=AssessmentStatus.choices,
        default=AssessmentStatus.PENDING,
    )
    assigned_students = models.ManyToManyField(
        "schools.Student",
        related_name="assessments",
        blank=True,
        verbose_name=_("assigned students"),
    )

    class Meta:
        verbose_name = _("assessment")
        verbose_name_plural = _("assessments")
        ordering = ["-due_date"]
        indexes = [
            # The dashboard lists "my school's active papers, soonest first".
            models.Index(fields=["school", "status", "-due_date"], name="assess_school_status_idx"),
            models.Index(fields=["teacher", "status"], name="assess_teacher_status_idx"),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def is_open(self) -> bool:
        """Whether a student may still start or continue an attempt."""
        return self.status == AssessmentStatus.ACTIVE

    @property
    def total_points(self):
        return self.questions.aggregate(total=models.Sum("point"))["total"] or 0


class AssessmentQuestion(BaseModel):
    """A question as it appears on one paper."""

    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE, related_name="questions", verbose_name=_("assessment")
    )
    text = models.TextField(_("text"))
    description = models.TextField(_("description"), blank=True)
    source_question = models.ForeignKey(
        "curriculum.Question",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assessment_copies",
        verbose_name=_("source question"),
        help_text=_("Set when pulled from the bank; null when the teacher authored it."),
    )
    subskill = models.ForeignKey(
        "curriculum.Subskill",
        on_delete=models.PROTECT,
        related_name="assessment_questions",
        verbose_name=_("subskill"),
    )
    skill = models.ForeignKey(
        "curriculum.Skill",
        on_delete=models.PROTECT,
        related_name="assessment_questions",
        verbose_name=_("skill"),
        help_text=_("Denormalised from the subskill so the matrix is a single-table read."),
    )
    fln_level = models.PositiveSmallIntegerField(_("FLN level"), validators=LEVEL_VALIDATORS)
    order = models.PositiveSmallIntegerField(
        _("order"), help_text=_("Position on the paper; assigned automatically when omitted.")
    )
    point = models.DecimalField(
        _("point"),
        max_digits=6,
        decimal_places=2,
        default=1,
        validators=[MinValueValidator(0)],
    )
    question_type = models.CharField(
        _("question type"), max_length=32, choices=QuestionType.choices
    )
    layout = models.CharField(
        _("layout"), max_length=32, choices=QuestionLayout.choices, blank=True
    )

    class Meta:
        verbose_name = _("assessment question")
        verbose_name_plural = _("assessment questions")
        ordering = ["assessment", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "order"], name="assessment_question_order_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(fln_level__gte=MIN_FLN_LEVEL, fln_level__lte=MAX_FLN_LEVEL),
                name="assessment_question_level_in_range",
            ),
        ]

    def __str__(self) -> str:
        return f"Q{self.order}: {self.text[:50]}"

    def save(self, *args, **kwargs):
        # Reachable despite the stubs saying otherwise: Django leaves a
        # non-null field with no default as None on an unsaved instance, which
        # is exactly the "order omitted" case this branch exists for.
        if self.order is None:
            # Append to the end of the paper. Two concurrent appends can pick
            # the same slot; the unique constraint rejects the loser and the
            # authoring service retries.
            highest = AssessmentQuestion.objects.filter(  # type: ignore[unreachable]
                assessment_id=self.assessment_id
            ).aggregate(top=models.Max("order"))["top"]
            self.order = (highest or 0) + 1
        return super().save(*args, **kwargs)

    @property
    def is_option_based(self) -> bool:
        return self.question_type in QuestionType.option_based()


class AssessmentQuestionContent(BaseModel):
    """One block in an assessment question's body."""

    assessment_question = models.ForeignKey(
        AssessmentQuestion,
        on_delete=models.CASCADE,
        related_name="contents",
        verbose_name=_("assessment question"),
    )
    type = models.CharField(_("type"), max_length=16, choices=ContentBlockType.choices)
    display_order = models.PositiveSmallIntegerField(_("display order"))
    text_content = models.TextField(_("text content"), blank=True)
    media = models.ForeignKey(
        "media_assets.MediaAsset",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assessment_question_contents",
        verbose_name=_("media"),
    )
    alt_text = models.CharField(_("alt text"), max_length=255, blank=True)
    caption = models.CharField(_("caption"), max_length=255, blank=True)

    class Meta:
        verbose_name = _("assessment question content")
        verbose_name_plural = _("assessment question contents")
        ordering = ["assessment_question", "display_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment_question", "display_order"],
                name="assessment_question_content_order_unique",
            ),
            models.CheckConstraint(
                condition=~models.Q(text_content="") | models.Q(media__isnull=False),
                name="assessment_content_requires_text_or_media",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.assessment_question_id} #{self.display_order} ({self.type})"


class AssessmentQuestionAnswer(BaseModel):
    """The expected answer for a question that is not answered by picking an
    option — a model answer to mark free text or an upload against."""

    assessment_question = models.OneToOneField(
        AssessmentQuestion,
        on_delete=models.CASCADE,
        related_name="answer",
        verbose_name=_("assessment question"),
    )
    value = models.TextField(_("value"), blank=True)
    media = models.ForeignKey(
        "media_assets.MediaAsset",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assessment_question_answers",
        verbose_name=_("media"),
    )

    class Meta:
        verbose_name = _("assessment question answer")
        verbose_name_plural = _("assessment question answers")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(value="") | models.Q(media__isnull=False),
                name="assessment_answer_requires_value_or_media",
            ),
        ]

    def __str__(self) -> str:
        return f"Answer for {self.assessment_question_id}"


class AssessmentQuestionOption(BaseModel):
    """A choice on an option-based assessment question."""

    assessment_question = models.ForeignKey(
        AssessmentQuestion,
        on_delete=models.CASCADE,
        related_name="options",
        verbose_name=_("assessment question"),
    )
    value = models.TextField(_("value"), blank=True)
    type = models.CharField(_("type"), max_length=16, choices=OptionType.choices)
    media = models.ForeignKey(
        "media_assets.MediaAsset",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="assessment_question_options",
        verbose_name=_("media"),
    )
    is_correct = models.BooleanField(_("is correct"), default=False)

    class Meta:
        verbose_name = _("assessment question option")
        verbose_name_plural = _("assessment question options")
        ordering = ["created_at"]
        indexes = [
            models.Index(
                fields=["assessment_question", "is_correct"], name="aq_option_correct_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(value="") | models.Q(media__isnull=False),
                name="assessment_option_requires_value_or_media",
            ),
        ]

    def __str__(self) -> str:
        return self.value[:60] or f"{self.type} option"


class AssessmentQuestionResponse(BaseModel):
    """What one student submitted for one question.

    `assessment` is denormalised from `assessment_question.assessment` so that
    "everything this student submitted for this paper" is a single-table read
    during marking and analytics.
    """

    assessment_question = models.ForeignKey(
        AssessmentQuestion,
        on_delete=models.CASCADE,
        related_name="responses",
        verbose_name=_("assessment question"),
    )
    student = models.ForeignKey(
        "schools.Student",
        on_delete=models.CASCADE,
        related_name="responses",
        verbose_name=_("student"),
    )
    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE, related_name="responses", verbose_name=_("assessment")
    )
    type = models.CharField(_("type"), max_length=32, choices=QuestionType.choices)
    media_value = models.ForeignKey(
        "media_assets.MediaAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="responses",
        verbose_name=_("media value"),
    )
    text_value = models.TextField(_("text value"), blank=True)
    transcript = models.TextField(
        _("transcript"),
        blank=True,
        help_text=_("Speech-to-text output, kept beside the audio so a teacher can verify."),
    )

    # Marking. Null until the response has been marked; free-form items are
    # marked asynchronously, so a null here is "pending", not "wrong".
    is_correct = models.BooleanField(_("is correct"), null=True, blank=True)
    awarded_points = models.DecimalField(
        _("awarded points"),
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    graded_by = models.CharField(
        _("graded by"), max_length=16, choices=GradedBy.choices, blank=True
    )
    grading_confidence = models.DecimalField(
        _("grading confidence"),
        max_digits=4,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        help_text=_("Low confidence routes the response to a teacher for review."),
    )
    error_type = models.CharField(
        _("error type"), max_length=32, choices=ErrorType.choices, blank=True
    )
    observation_note = models.TextField(
        _("observation note"),
        blank=True,
        help_text=_("How the answer was wrong - the detail remediation is built from."),
    )

    class Meta:
        verbose_name = _("assessment question response")
        verbose_name_plural = _("assessment question responses")
        ordering = ["assessment_question__order"]
        constraints = [
            # One answer per question per student; re-answering updates in place.
            models.UniqueConstraint(
                fields=["assessment_question", "student"], name="response_question_student_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["assessment", "student"], name="response_assess_student_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student_id} → {self.assessment_question_id}"


class AssessmentQuestionResponseOption(BaseModel):
    """One option a student selected. Multiple rows per response for a
    multiple-choice question."""

    assessment_question_response = models.ForeignKey(
        AssessmentQuestionResponse,
        on_delete=models.CASCADE,
        related_name="selected_options",
        verbose_name=_("response"),
    )
    assessment_question_option = models.ForeignKey(
        AssessmentQuestionOption,
        on_delete=models.CASCADE,
        related_name="selections",
        verbose_name=_("option"),
    )

    class Meta:
        verbose_name = _("assessment question response option")
        verbose_name_plural = _("assessment question response options")
        constraints = [
            models.UniqueConstraint(
                fields=["assessment_question_response", "assessment_question_option"],
                name="response_option_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.assessment_question_response_id} → {self.assessment_question_option_id}"


class AssessmentResult(BaseModel):
    """One student's outcome on one paper. Created when the paper is assigned,
    so "not started" is a real row rather than an absence."""

    student = models.ForeignKey(
        "schools.Student",
        on_delete=models.CASCADE,
        related_name="results",
        verbose_name=_("student"),
    )
    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE, related_name="results", verbose_name=_("assessment")
    )
    score = models.DecimalField(
        _("score"),
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Null until graded."),
    )
    feedback = models.TextField(_("feedback"), blank=True)
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=ResultStatus.choices,
        default=ResultStatus.NOT_STARTED,
    )

    class Meta:
        verbose_name = _("assessment result")
        verbose_name_plural = _("assessment results")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "assessment"], name="result_student_assessment_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["assessment", "status"], name="result_assess_status_idx"),
            models.Index(fields=["student", "-created_at"], name="result_student_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student_id} — {self.assessment_id}: {self.status}"


class AssessmentAnalytics(BaseModel):
    """Aggregates for one paper, recomputed after grading.

    A stored roll-up rather than a live query: the dashboard reads it on every
    page load, and the underlying scan is over every response on the paper.
    """

    assessment = models.OneToOneField(
        Assessment, on_delete=models.CASCADE, related_name="analytics", verbose_name=_("assessment")
    )
    most_missed_questions = models.ManyToManyField(
        AssessmentQuestion,
        related_name="analytics_most_missed",
        blank=True,
        verbose_name=_("most missed questions"),
    )
    most_correct_questions = models.ManyToManyField(
        AssessmentQuestion,
        related_name="analytics_most_correct",
        blank=True,
        verbose_name=_("most correct questions"),
    )
    class_average = models.DecimalField(
        _("class average"),
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    participation_rate = models.DecimalField(
        _("participation rate"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
        help_text=_("Percentage of assigned students who submitted."),
    )

    class Meta:
        verbose_name = _("assessment analytics")
        verbose_name_plural = _("assessment analytics")

    def __str__(self) -> str:
        return f"Analytics for {self.assessment_id}"
