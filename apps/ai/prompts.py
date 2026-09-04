"""Assembling what a job sends.

Guidance for a job comes from `AIPromptDocument` rows, which are seeded from
markdown in `apps/ai/documents/`. Two rules shape how it is put together.

Documents are scoped by job, so a marking call never carries lesson-planning
pedagogy it will not use. And the guidance goes in the system message with the
per-call payload after it, because providers that cache do so on a stable
prefix - anything varying per call has to come last or the cache never hits.

There is no retrieval step. The corpus is bounded and each document is already
addressed by job, so selecting it is a query rather than a search, with no
retrieval-miss failure mode where the right guidance quietly does not arrive.
"""

from functools import lru_cache

from apps.ai.client import PromptBundle
from apps.ai.enums import JobType
from apps.ai.models import AIPromptDocument

#: Bumped when the fallback text below changes, so a generation made without
#: seeded documents is still traceable to what produced it.
FALLBACK_VERSION = "fallback-1"


def build(job: JobType | str, *, payload: str) -> PromptBundle:
    """Guidance for `job`, with the call's own payload after it."""
    system, version = _guidance(str(job))
    return PromptBundle(system=system, user=payload, prompt_version=version)


@lru_cache(maxsize=32)
def _guidance(job: str) -> tuple[str, str]:
    """The active documents for a job, joined in order.

    Cached because it is the same text on every call and re-reading it per
    response would be a query per marked answer. `reset_cache` clears it after
    a reseed.
    """
    documents = list(
        AIPromptDocument.objects.filter(job_type=job, is_active=True).order_by(
            "display_order", "name"
        )
    )
    if not documents:
        return _fallback(job), FALLBACK_VERSION

    system = "\n\n---\n\n".join(doc.content.strip() for doc in documents)
    version = ",".join(f"{doc.name}@{doc.version}" for doc in documents)
    return system, version


def reset_cache() -> None:
    """Call after seeding, or the process keeps serving the old guidance."""
    _guidance.cache_clear()


def _fallback(job: str) -> str:
    """Enough to be useful when nothing is seeded.

    Deliberately terse. A job running on this is running degraded, and the
    recorded `prompt_version` says so, so a run of odd results can be traced
    back here rather than blamed on the model.
    """
    return (
        f"You are performing the task '{job}' for a Nigerian primary school "
        "assessment platform for children aged 5 to 12. Reply only with JSON "
        "matching the requested schema. Judge what the child demonstrated, not "
        "how neatly they presented it. Where you are unsure, say so through a "
        "low confidence rather than guessing."
    )


__all__ = ["FALLBACK_VERSION", "build", "reset_cache"]
