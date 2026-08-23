"""Abstract base models every concrete model should build on."""

import uuid

from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Adds self-managed `created_at` / `updated_at` fields."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True
        get_latest_by = "created_at"


class UUIDModel(models.Model):
    """Uses a non-guessable UUID primary key instead of a sequential integer."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        return super().update(deleted_at=timezone.now())

    def hard_delete(self):
        return super().delete()

    def alive(self):
        return self.filter(deleted_at__isnull=True)

    def dead(self):
        return self.exclude(deleted_at__isnull=True)


class SoftDeleteManager(models.Manager.from_queryset(SoftDeleteQuerySet)):  # type: ignore[misc]
    def get_queryset(self) -> SoftDeleteQuerySet:
        return super().get_queryset().filter(deleted_at__isnull=True)


class SoftDeleteModel(models.Model):
    """Rows are marked deleted rather than removed.

    `objects` hides deleted rows; `all_objects` sees everything.
    """

    deleted_at = models.DateTimeField(null=True, blank=True, editable=False, db_index=True)

    objects = SoftDeleteManager()
    all_objects = models.Manager.from_queryset(SoftDeleteQuerySet)()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False):  # noqa: ARG002
        self.deleted_at = timezone.now()
        self.save(update_fields=["deleted_at"])

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)

    def restore(self) -> None:
        self.deleted_at = None
        self.save(update_fields=["deleted_at"])


class BaseModel(UUIDModel, TimeStampedModel):
    """The default starting point for new models: UUID pk + timestamps."""

    class Meta:
        abstract = True
