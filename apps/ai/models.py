"""What the model was told, and what it said.

Two tables. `AIPromptDocument` is the guidance a job is given - authored as
markdown under version control and mirrored here so a running system can read
it, cache it, and pin a generation to the exact version that produced it.
`AIGeneration` is the record of every call.

Neither is optional infrastructure. Without the first, changing guidance means
a deploy. Without the second there is no answering "why did this plan say
that", no per-school spend, and no way to tell whether a prompt change helped.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.ai.enums import GenerationStatus, JobType
from apps.common.models import BaseModel


class AIPromptDocument(BaseModel):
    """One piece of guidance, scoped to one job.

    Tagged by job so a marking call does not carry lesson-planning pedagogy it
    will never use. Seeded from `apps/ai/documents/` by `seed_ai_documents`;
    git stays the source of truth and this is the runtime copy.
    """

    job_type = models.CharField(_("job type"), max_length=32, choices=JobType.choices)
    name = models.CharField(_("name"), max_length=128)
    version = models.CharField(
        _("version"),
        max_length=32,
        help_text=_("Bumped when the content changes. Pinned onto each generation."),
    )
    content = models.TextField(_("content"))
    display_order = models.PositiveSmallIntegerField(_("display order"), default=0)
    is_active = models.BooleanField(_("is active"), default=True)

    class Meta:
        verbose_name = _("AI prompt document")
        verbose_name_plural = _("AI prompt documents")
        ordering = ["job_type", "display_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["job_type", "name", "version"], name="ai_prompt_document_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["job_type", "is_active"], name="ai_doc_job_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.job_type}/{self.name}@{self.version}"


class AIGeneration(BaseModel):
    """One model call, kept whether it worked or not.

    The failures matter as much as the successes: a run of `invalid` on one job
    is how a drifting prompt or a downgraded model announces itself.

    `subject_type`/`subject_id` point loosely at whatever the call was about -
    a response, a question, an assessment - rather than a foreign key per job
    type, so adding a job does not mean adding a column.
    """

    job_type = models.CharField(_("job type"), max_length=32, choices=JobType.choices)
    status = models.CharField(_("status"), max_length=16, choices=GenerationStatus.choices)

    subject_type = models.CharField(
        _("subject type"),
        max_length=64,
        blank=True,
        help_text=_('What the call was about, e.g. "assessment_question_response".'),
    )
    subject_id = models.CharField(_("subject id"), max_length=64, blank=True)

    provider = models.CharField(_("provider"), max_length=32)
    model_id = models.CharField(_("model"), max_length=128)
    prompt_version = models.CharField(
        _("prompt version"),
        max_length=64,
        blank=True,
        help_text=_("The document set this call was given, so a change is traceable."),
    )

    input_hash = models.CharField(
        _("input hash"),
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("Identical inputs hash alike, which is what makes a repeat visible."),
    )
    raw_output = models.TextField(_("raw output"), blank=True)
    error = models.TextField(_("error"), blank=True)

    input_tokens = models.PositiveIntegerField(_("input tokens"), default=0)
    output_tokens = models.PositiveIntegerField(_("output tokens"), default=0)
    latency_ms = models.PositiveIntegerField(_("latency ms"), default=0)

    class Meta:
        verbose_name = _("AI generation")
        verbose_name_plural = _("AI generations")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["job_type", "-created_at"], name="ai_gen_job_created_idx"),
            models.Index(fields=["status", "-created_at"], name="ai_gen_status_idx"),
            models.Index(fields=["subject_type", "subject_id"], name="ai_gen_subject_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.job_type} ({self.status})"

    @property
    def succeeded(self) -> bool:
        return self.status == GenerationStatus.SUCCEEDED


__all__ = ["AIGeneration", "AIPromptDocument"]
