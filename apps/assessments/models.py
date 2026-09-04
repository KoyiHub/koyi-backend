"""Papers, sittings and their results.

An assessment is built from *sections*. A section is one sitting: it covers a
single domain and whatever skills the teacher chose, spanning mixed FLN
levels. Children work through sections one at a time, on different days if
need be, which is why the timer and the unlock state live on the section
rather than on the paper.

Assessment questions are *copies*, not references, of `curriculum.Question`.
That duplication is deliberate: once a paper has been sat, editing the source
question in the bank must not silently change what a student was asked or how
they were marked. `source_question` records where a copy came from — null when
the teacher authored it directly — which is what makes it possible to compare
a child's performance on the same item across two rounds.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from apps.assessments.enums import (
    AssessmentStatus,
    CellOutcome,
    ErrorType,
    GradedBy,
    ResultStatus,
    SectionResultStatus,
)
from apps.common.enums import (
    MAX_FLN_LEVEL,
    MIN_FLN_LEVEL,
    ContentBlockType,
    Domain,
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
    """A diagnostic paper, built from one or more sections.

    Carries no class, subject or difficulty: children are grouped by
    demonstrated level rather than by year group, and the domain comes from
    each section. What it does carry is `code` — the short string a child
    types alongside their student id to open a sitting.
    """

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
    code = models.CharField(
        _("code"),
        max_length=16,
        blank=True,
        help_text=_("Typed by the child to open a sitting. Minted at publish."),
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=AssessmentStatus.choices,
        default=AssessmentStatus.DRAFT,
    )
    opens_at = models.DateTimeField(_("opens at"), null=True, blank=True)
    closes_at = models.DateTimeField(
        _("closes at"),
        null=True,
        blank=True,
        help_text=_("After this, sittings stop and grouping may run."),
    )
    published_at = models.DateTimeField(_("published at"), null=True, blank=True)

    class Meta:
        verbose_name = _("assessment")
        verbose_name_plural = _("assessments")
        ordering = ["-created_at"]
        constraints = [
            # Blank while a draft, unique once minted. A partial constraint
            # rather than `unique=True` so many drafts can coexist.
            models.UniqueConstraint(
                Upper("code"),
                condition=~models.Q(code=""),
                name="assessment_code_ci_unique",
            ),
        ]
        indexes = [
            models.Index(
                fields=["school", "status", "-created_at"], name="assess_school_status_idx"
            ),
            models.Index(fields=["teacher", "status"], name="assess_teacher_status_idx"),
            models.Index(fields=["code"], name="assess_code_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})" if self.code else self.name

    @property
    def is_editable(self) -> bool:
        """Whether sections and questions may still be changed."""
        return self.status in AssessmentStatus.editable()

    @property
    def is_sittable(self) -> bool:
        """Whether a child may start or continue a section."""
        return self.status in AssessmentStatus.sittable()

    @property
    def total_points(self):
        return (
            AssessmentQuestion.objects.filter(assessment=self).aggregate(total=models.Sum("point"))[
                "total"
            ]
            or 0
        )


class AssessmentSection(BaseModel):
    """One sitting: a single domain, a chosen set of skills, mixed levels.

    Sections exist so a Grade 1 child is never asked to sit an hour-long paper
    in one go. They are worked through in `order`, each unlocking the next, and
    each may be taken on a different day.

    `covers` records which subskills the section is *meant* to probe, so the
    authoring UI can warn about a coverage gap before a child ever sits it
    rather than leaving it to be discovered at placement.
    """

    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE, related_name="sections", verbose_name=_("assessment")
    )
    domain = models.CharField(_("domain"), max_length=16, choices=Domain.choices)
    name = models.CharField(_("name"), max_length=255)
    instructions = models.TextField(_("instructions"), blank=True)
    order = models.PositiveSmallIntegerField(_("order"))
    timer = models.DurationField(
        _("timer"),
        null=True,
        blank=True,
        help_text=_("Time allowed for this sitting. Null means untimed."),
    )
    covers = models.ManyToManyField(
        "curriculum.Subskill",
        related_name="assessment_sections",
        blank=True,
        verbose_name=_("covers"),
    )

    class Meta:
        verbose_name = _("assessment section")
        verbose_name_plural = _("assessment sections")
        ordering = ["assessment", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "order"], name="assessment_section_order_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["assessment", "order"], name="section_assess_order_idx"),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def total_points(self):
        return self.questions.aggregate(total=models.Sum("point"))["total"] or 0


class AssessmentQuestion(BaseModel):
    """A question as it appears on one paper.

    `assessment` is denormalised from `section.assessment` so that "every
    question on this paper" and "every response to this paper" stay
    single-table reads during marking and analytics.
    """

    section = models.ForeignKey(
        AssessmentSection,
        on_delete=models.CASCADE,
        related_name="questions",
        verbose_name=_("section"),
    )
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
        ordering = ["section", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["section", "order"], name="assessment_question_order_unique"
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
                section_id=self.section_id
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


class AssessmentAssignment(BaseModel):
    """That a given child is expected to sit a given paper.

    Assignment is per assessment, not per section: a child is given the whole
    paper and works through its sections in order. Per-section progress lives
    on `AssessmentSectionResult`.

    `code` is what makes a sitting the child's own. The assessment code is
    shared by everyone taking the paper, so on its own it identifies the paper
    and nothing else; this one is personal, and a child opens their sitting by
    giving both. It replaces asking for a student id, which was printed on a
    card and known to every classmate — two public facts are not a credential.

    It is stored in the clear on purpose: a teacher has to be able to read it
    back to a child who has lost theirs, and you cannot hash something and also
    show it to someone. That is an accepted trade. The code is scoped to one
    child and one paper, stops working when the assessment closes, and grants
    nothing beyond sitting that assessment.

    Verifying with it mints a session (stored here as a hash) which is what
    carries the sitting from request to request. The code is long-lived and
    travels in a guardian's email; the session is short-lived and travels in a
    header, so the durable secret is presented twice rather than on every
    autosave for days.
    """

    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name=_("assessment"),
    )
    student = models.ForeignKey(
        "schools.Student",
        on_delete=models.CASCADE,
        related_name="assignments",
        verbose_name=_("student"),
    )
    assigned_by = models.ForeignKey(
        "schools.Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments_made",
        verbose_name=_("assigned by"),
    )
    code = models.CharField(
        _("code"),
        max_length=16,
        help_text=_("The child's personal code for this paper. Unique within it."),
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=ResultStatus.choices,
        default=ResultStatus.NOT_STARTED,
    )
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    session_hash = models.CharField(_("session hash"), max_length=64, blank=True, db_index=True)
    session_expires_at = models.DateTimeField(_("session expires at"), null=True, blank=True)

    class Meta:
        verbose_name = _("assessment assignment")
        verbose_name_plural = _("assessment assignments")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "student"], name="assignment_assessment_student_unique"
            ),
            # Only unique within one paper: a child gives the assessment code
            # first, so the pair is what has to be unambiguous.
            models.UniqueConstraint(
                "assessment", Upper("code"), name="assignment_assessment_code_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["assessment", "status"], name="assignment_assess_status_idx"),
            models.Index(fields=["student", "-created_at"], name="assignment_student_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student_id} → {self.assessment_id}"


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
    # Rolled up once at marking time. The teacher's results table reads these
    # for every student on the paper, so recomputing them per row would mean a
    # scan of every response in the class.
    items_attempted = models.PositiveSmallIntegerField(_("items attempted"), default=0)
    items_correct = models.PositiveSmallIntegerField(_("items correct"), default=0)
    percentage = models.DecimalField(
        _("percentage"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    marked_at = models.DateTimeField(_("marked at"), null=True, blank=True)

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


class AssessmentSectionResult(BaseModel):
    """One student's progress through one section.

    Sections unlock in order, so this is also the unlock state: the first
    section is unlocked when the paper is assigned and each submission unlocks
    the next. When the last one is submitted the assessment finalises itself —
    there is no separate submit step, because a child who completes everything
    and misses a final button would lose the whole sitting.
    """

    result = models.ForeignKey(
        AssessmentResult,
        on_delete=models.CASCADE,
        related_name="section_results",
        verbose_name=_("result"),
    )
    section = models.ForeignKey(
        AssessmentSection,
        on_delete=models.CASCADE,
        related_name="results",
        verbose_name=_("section"),
    )
    status = models.CharField(
        _("status"),
        max_length=16,
        choices=SectionResultStatus.choices,
        default=SectionResultStatus.LOCKED,
    )
    score = models.DecimalField(
        _("score"),
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    items_attempted = models.PositiveSmallIntegerField(_("items attempted"), default=0)
    items_correct = models.PositiveSmallIntegerField(_("items correct"), default=0)
    started_at = models.DateTimeField(_("started at"), null=True, blank=True)
    submitted_at = models.DateTimeField(_("submitted at"), null=True, blank=True)
    expires_at = models.DateTimeField(
        _("expires at"),
        null=True,
        blank=True,
        help_text=_("When a timed sitting must end. Set from the section timer on start."),
    )

    class Meta:
        verbose_name = _("assessment section result")
        verbose_name_plural = _("assessment section results")
        ordering = ["result", "section__order"]
        constraints = [
            models.UniqueConstraint(fields=["result", "section"], name="section_result_unique"),
        ]
        indexes = [
            models.Index(fields=["result", "status"], name="section_result_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.result_id} → {self.section_id}: {self.status}"


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


class PlacementRule(BaseModel):
    """How many core skills must pass for one level to count as passed.

    Stored per (domain, level) rather than as a single fraction, because a
    fraction does not discretise at small N. No skill spans all five levels, so
    the number applicable at each differs - two at literacy level 1, six at
    level 3 - and three quarters of two rounds to "both". That is defensible at
    the extremes and too strict in the middle of numeracy, where level 4 draws
    on only three skills and would demand all of them.

    Seeded from the taxonomy with `seed_placement_rules`, then tuned in place.
    Re-run the seed after changing which levels a skill covers.
    """

    domain = models.CharField(_("domain"), max_length=16, choices=Domain.choices)
    fln_level = models.PositiveSmallIntegerField(_("FLN level"), validators=LEVEL_VALIDATORS)
    required_skills = models.PositiveSmallIntegerField(
        _("required skills"),
        help_text=_("How many core skills must pass at this level for it to be passed."),
    )
    applicable_skills = models.PositiveSmallIntegerField(
        _("applicable skills"),
        help_text=_("How many core skills cover this level. Recorded so drift is visible."),
    )

    class Meta:
        verbose_name = _("placement rule")
        verbose_name_plural = _("placement rules")
        ordering = ["domain", "fln_level"]
        constraints = [
            models.UniqueConstraint(
                fields=["domain", "fln_level"], name="placement_rule_domain_level_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.domain} L{self.fln_level}: {self.required_skills}/{self.applicable_skills}"


class SkillLevelResult(BaseModel):
    """One cell of the diagnosis: how a child did on one subskill at one level.

    Persisted rather than computed on demand, for three reasons. Placement can
    be re-run after a threshold change without re-marking anything. Two rounds
    can be diffed to show real movement. And the evidence behind a placement
    stays inspectable when a school asks why a child was placed where they were.
    """

    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.CASCADE,
        related_name="skill_level_results",
        verbose_name=_("assessment"),
    )
    student = models.ForeignKey(
        "schools.Student",
        on_delete=models.CASCADE,
        related_name="skill_level_results",
        verbose_name=_("student"),
    )
    skill = models.ForeignKey(
        "curriculum.Skill",
        on_delete=models.PROTECT,
        related_name="level_results",
        verbose_name=_("skill"),
    )
    subskill = models.ForeignKey(
        "curriculum.Subskill",
        on_delete=models.PROTECT,
        related_name="level_results",
        verbose_name=_("subskill"),
    )
    fln_level = models.PositiveSmallIntegerField(_("FLN level"), validators=LEVEL_VALIDATORS)
    items_attempted = models.PositiveSmallIntegerField(_("items attempted"), default=0)
    items_correct = models.PositiveSmallIntegerField(_("items correct"), default=0)
    outcome = models.CharField(_("outcome"), max_length=8, choices=CellOutcome.choices)

    class Meta:
        verbose_name = _("skill level result")
        verbose_name_plural = _("skill level results")
        ordering = ["student", "skill__display_order", "fln_level"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "student", "subskill", "fln_level"],
                name="skill_level_result_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["assessment", "student"], name="slr_assess_student_idx"),
            models.Index(fields=["student", "subskill"], name="slr_student_subskill_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.student_id} {self.subskill_id} L{self.fln_level}: {self.outcome}"


class Placement(BaseModel):
    """The level a child needs taught in one domain, from one assessment.

    Placement is absolute: each assessment decides the level outright. A child
    placed at 4 who later assesses at 2 is at 2. There is no promotion ladder
    and nothing carried forward, so this row is a reading taken on a date, not
    a rung a child holds.

    `level` names what to teach next, not what has been mastered - it is the
    lowest probed level they did not pass.
    """

    student = models.ForeignKey(
        "schools.Student",
        on_delete=models.CASCADE,
        related_name="placements",
        verbose_name=_("student"),
    )
    assessment = models.ForeignKey(
        Assessment,
        on_delete=models.CASCADE,
        related_name="placements",
        verbose_name=_("assessment"),
    )
    domain = models.CharField(_("domain"), max_length=16, choices=Domain.choices)
    level = models.PositiveSmallIntegerField(
        _("level"), null=True, blank=True, validators=LEVEL_VALIDATORS
    )
    levels_probed = models.JSONField(
        _("levels probed"),
        default=list,
        help_text=_("Which levels this paper carried items for, in this domain."),
    )
    computed_at = models.DateTimeField(_("computed at"))

    class Meta:
        verbose_name = _("placement")
        verbose_name_plural = _("placements")
        ordering = ["-computed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "assessment", "domain"], name="placement_unique"
            ),
        ]
        indexes = [
            models.Index(
                fields=["student", "domain", "-computed_at"], name="placement_student_idx"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student_id} {self.domain}: L{self.level}"
