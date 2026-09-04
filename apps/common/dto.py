"""The data-transfer convention.

Serializers live at the HTTP boundary: they validate on the way in and render
on the way out. Everything between a view and the database speaks these —
frozen dataclasses defined in each app's own `dto.py`.

The point is that a service takes and returns plain objects. It can be called
and tested without a request factory, and it cannot drift into depending on a
serializer's field names.

    # apps/assessments/dto.py
    @dataclass(frozen=True, slots=True)
    class CreateSectionInput:
        domain: str
        name: str
        timer: timedelta | None = None
        covers: tuple[UUID, ...] = ()

    # apps/assessments/services.py
    class AssessmentDraftService:
        def add_section(self, data: CreateSectionInput) -> SectionDraft: ...

    # apps/teacher_portal/views.py
    serializer.is_valid(raise_exception=True)
    section = service.add_section(CreateSectionInput(**serializer.validated_data))
    return Response(SectionSerializer(section).data, status=201)

Rules that keep this honest:

* `frozen=True` — a service must not mutate its own input.
* `slots=True` — a typo becomes an AttributeError rather than a silent no-op.
* Defaults on every optional field, so a partial update is expressible.
* No Django objects in an input DTO; pass ids and let the service resolve them
  through a repository, which is where tenant scoping is enforced.
* Output DTOs may carry model instances — the caller is about to render them.
"""

from dataclasses import dataclass, field, replace

__all__ = ["dataclass", "field", "replace"]
