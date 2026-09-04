"""The skill taxonomy and the reusable question library.

Two things live here. The *taxonomy* — domains, skills and subskills — is the
spine of the whole product: every question is tagged to a subskill at an FLN
level, and that pairing is what turns a set of responses into a diagnosis.
The *bank* is the company-authored question library teachers draw from.

Nothing here is bound to a student or a sitting. When a teacher builds an
assessment the relevant rows are copied into `apps.assessments`, so editing a
bank question later cannot retroactively change a paper somebody has sat.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from apps.common.enums import (
    MAX_FLN_LEVEL,
    MIN_FLN_LEVEL,
    AnswerType,
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


class Skill(BaseModel):
    """A strand of ability within one domain — "Reading Fluency".

    Skills are what *place* a child: a level is passed when enough of the
    skills applicable at that level pass. `min_level`/`max_level` bound where
    the skill is assessed at all, which is why "enough" has to be measured
    against the skills applicable at a level rather than against all of them.
    """

    code = models.SlugField(_("code"), max_length=64, unique=True)
    name = models.CharField(_("name"), max_length=128)
    domain = models.CharField(_("domain"), max_length=16, choices=Domain.choices)
    min_level = models.PositiveSmallIntegerField(_("minimum level"), validators=LEVEL_VALIDATORS)
    max_level = models.PositiveSmallIntegerField(_("maximum level"), validators=LEVEL_VALIDATORS)
    is_core = models.BooleanField(
        _("is core"),
        default=True,
        help_text=_("Only core skills gate placement; the rest are enrichment."),
    )
    display_order = models.PositiveSmallIntegerField(_("display order"), default=0)

    class Meta:
        verbose_name = _("skill")
        verbose_name_plural = _("skills")
        ordering = ["domain", "display_order", "name"]
        indexes = [
            models.Index(fields=["domain", "is_core"], name="skill_domain_core_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_level__lte=models.F("max_level")),
                name="skill_level_range_ordered",
            ),
            models.CheckConstraint(
                condition=models.Q(min_level__gte=MIN_FLN_LEVEL, max_level__lte=MAX_FLN_LEVEL),
                name="skill_level_range_in_bounds",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def covers_level(self, level: int) -> bool:
        return self.min_level <= level <= self.max_level


class Subskill(BaseModel):
    """The specific ability a question tests and a lesson plan remediates.

    `min_level`/`max_level` are optional: unset means the subskill is assessed
    across its parent skill's whole range. Setting them narrows it — under
    Alphabetic Knowledge (1-3), letter naming is really level 1 alone — and
    that narrowing is data, not a migration.
    """

    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="subskills", verbose_name=_("skill")
    )
    code = models.SlugField(_("code"), max_length=64, unique=True)
    name = models.CharField(_("name"), max_length=255)
    min_level = models.PositiveSmallIntegerField(
        _("minimum level"), null=True, blank=True, validators=LEVEL_VALIDATORS
    )
    max_level = models.PositiveSmallIntegerField(
        _("maximum level"), null=True, blank=True, validators=LEVEL_VALIDATORS
    )
    display_order = models.PositiveSmallIntegerField(_("display order"), default=0)

    class Meta:
        verbose_name = _("subskill")
        verbose_name_plural = _("subskills")
        ordering = ["skill", "display_order", "name"]
        indexes = [
            models.Index(fields=["skill", "display_order"], name="subskill_skill_order_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_level__isnull=True)
                | models.Q(max_level__isnull=True)
                | models.Q(min_level__lte=models.F("max_level")),
                name="subskill_level_range_ordered",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def domain(self) -> str:
        return self.skill.domain

    @property
    def level_range(self) -> tuple[int, int]:
        """The narrowest applicable range: own bounds, else the parent skill's."""
        return (
            self.min_level if self.min_level is not None else self.skill.min_level,
            self.max_level if self.max_level is not None else self.skill.max_level,
        )

    def covers_level(self, level: int) -> bool:
        low, high = self.level_range
        return low <= level <= high


class QuestionBank(BaseModel):
    """A curated pool of company-authored questions.

    Convenience for browsing, not a structural boundary — the skill tags on
    the questions are what actually organise the library.
    """

    name = models.CharField(_("name"), max_length=255)
    domain = models.CharField(_("domain"), max_length=16, choices=Domain.choices)

    class Meta:
        verbose_name = _("question bank")
        verbose_name_plural = _("question banks")
        ordering = ["domain", "name"]
        constraints = [
            models.UniqueConstraint(
                Upper("name"), "domain", name="question_bank_name_domain_ci_unique"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Question(BaseModel):
    """A reusable, company-authored question.

    Teachers never write into this table — they either pull a row into an
    assessment or author directly onto the assessment. `question_bank` is
    nullable so a question can be drafted, or outlive a retired bank.
    """

    question_bank = models.ForeignKey(
        QuestionBank,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="questions",
        verbose_name=_("question bank"),
    )
    subskill = models.ForeignKey(
        Subskill, on_delete=models.PROTECT, related_name="questions", verbose_name=_("subskill")
    )
    fln_level = models.PositiveSmallIntegerField(
        _("FLN level"),
        validators=LEVEL_VALIDATORS,
        help_text=_("The developmental level this question probes, 1 to 5."),
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
    layout = models.CharField(
        _("layout"), max_length=32, choices=QuestionLayout.choices, blank=True
    )
    type = models.CharField(_("type"), max_length=32, choices=QuestionType.choices)

    class Meta:
        verbose_name = _("question")
        verbose_name_plural = _("questions")
        ordering = ["-created_at"]
        indexes = [
            # The bank browser filters on exactly this pair.
            models.Index(fields=["subskill", "fln_level"], name="question_subskill_level_idx"),
            models.Index(fields=["question_bank", "fln_level"], name="question_bank_level_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fln_level__gte=MIN_FLN_LEVEL, fln_level__lte=MAX_FLN_LEVEL),
                name="question_fln_level_in_range",
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


__all__ = [
    "Option",
    "Question",
    "QuestionAnswer",
    "QuestionBank",
    "QuestionContent",
    "Skill",
    "Subskill",
]
