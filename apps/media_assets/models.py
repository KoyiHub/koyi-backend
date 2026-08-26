"""Uploaded media, stored once and referenced from everywhere.

Questions, options and student responses all point at rows here rather than
carrying their own `FileField`s, so the same audio clip can back a question
prompt and an option without being uploaded twice.
"""

from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.common.enums import MediaType
from apps.common.models import BaseModel


class MediaAsset(BaseModel):
    type = models.CharField(_("type"), max_length=16, choices=MediaType.choices, db_index=True)
    url = models.URLField(_("url"), max_length=1000)
    mime_type = models.CharField(_("mime type"), max_length=127)
    original_filename = models.CharField(_("original filename"), max_length=255)
    size_bytes = models.PositiveBigIntegerField(
        _("size in bytes"), validators=[MinValueValidator(1)]
    )
    # Fractional seconds matter for short prompts, so this is not an integer.
    duration_seconds = models.DecimalField(
        _("duration in seconds"),
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Time-based media only (audio, video)."),
    )

    class Meta:
        verbose_name = _("media asset")
        verbose_name_plural = _("media assets")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["type", "-created_at"], name="media_type_created_idx"),
        ]
        constraints = [
            # An image with a duration means the uploader mislabelled something.
            models.CheckConstraint(
                condition=models.Q(duration_seconds__isnull=True)
                | models.Q(type__in=[MediaType.AUDIO, MediaType.VIDEO]),
                name="media_duration_only_for_timed_types",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.original_filename} ({self.type})"

    @property
    def is_timed(self) -> bool:
        return self.type in {MediaType.AUDIO, MediaType.VIDEO}
