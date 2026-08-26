"""A thin repository layer over the ORM.

The point is not to hide Django — it is to give services a single, typed place
where a query lives, so that "how do I fetch the assessments a teacher may
see?" has exactly one answer and tenant scoping cannot be forgotten in a view.

Repositories return querysets/instances and never raise HTTP errors; services
decide what a missing row means.
"""

from collections.abc import Iterable, Sequence
from typing import Any, Generic, TypeVar

from django.db import models
from django.db.models import QuerySet

ModelT = TypeVar("ModelT", bound=models.Model)


class BaseRepository(Generic[ModelT]):
    """CRUD primitives for one model.

    Subclasses set `model` and add query methods that express domain intent.
    Override `get_queryset()` to bake in the joins every caller needs.
    """

    model: type[ModelT]

    #: Applied by `get_queryset()` so callers stop paying for N+1s by default.
    select_related: Sequence[str] = ()
    prefetch_related: Sequence[str] = ()

    def get_queryset(self) -> QuerySet[ModelT]:
        qs = self.model._default_manager.all()
        if self.select_related:
            qs = qs.select_related(*self.select_related)
        if self.prefetch_related:
            qs = qs.prefetch_related(*self.prefetch_related)
        return qs

    # --- reads ------------------------------------------------------------

    def all(self) -> QuerySet[ModelT]:
        return self.get_queryset()

    def filter(self, **kwargs: Any) -> QuerySet[ModelT]:
        return self.get_queryset().filter(**kwargs)

    def get(self, **kwargs: Any) -> ModelT:
        """Fetch one row. Raises `Model.DoesNotExist` — callers translate it."""
        return self.get_queryset().get(**kwargs)

    def get_or_none(self, **kwargs: Any) -> ModelT | None:
        return self.get_queryset().filter(**kwargs).first()

    def exists(self, **kwargs: Any) -> bool:
        return self.model._default_manager.filter(**kwargs).exists()

    def count(self, **kwargs: Any) -> int:
        return self.model._default_manager.filter(**kwargs).count()

    # --- writes -----------------------------------------------------------

    def create(self, **kwargs: Any) -> ModelT:
        return self.model._default_manager.create(**kwargs)

    def bulk_create(self, objects: Iterable[ModelT], **kwargs: Any) -> list[ModelT]:
        return self.model._default_manager.bulk_create(list(objects), **kwargs)

    def update(self, instance: ModelT, **fields: Any) -> ModelT:
        """Assign and save only the fields given, so concurrent writes to other
        columns are not clobbered."""
        for name, value in fields.items():
            setattr(instance, name, value)
        if fields:
            update_fields = [*fields]
            if any(f.name == "updated_at" for f in instance._meta.fields):
                update_fields.append("updated_at")
            instance.save(update_fields=update_fields)
        return instance

    def delete(self, instance: ModelT) -> None:
        instance.delete()
