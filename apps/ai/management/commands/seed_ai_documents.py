"""Load the prompt corpus from markdown into the database.

Git is the source of truth; this is the runtime copy. Keeping both means
guidance can be reviewed in a pull request and still be changed without a
deploy, and a generation can be pinned to the version that produced it.

Version is the content hash, so re-running after an edit creates a new row
rather than mutating the one older generations point at.
"""

import hashlib
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.ai.enums import JobType
from apps.ai.models import AIPromptDocument
from apps.ai.prompts import reset_cache

DOCUMENTS = Path(__file__).resolve().parents[2] / "documents"


class Command(BaseCommand):
    help = "Seed AI prompt documents from apps/ai/documents/*.md"

    def add_arguments(self, parser) -> None:
        parser.add_argument("--path", default=str(DOCUMENTS))

    @transaction.atomic
    def handle(self, **options: Any) -> None:
        root = Path(options["path"])
        if not root.exists():
            self.stdout.write(self.style.WARNING(f"No document directory at {root}"))
            return

        jobs = set(JobType.values)
        written = unchanged = 0
        skipped: list[str] = []

        for path in sorted(root.glob("*.md")):
            job = path.stem
            if job not in jobs:
                # A file named after nothing would silently never be loaded.
                skipped.append(path.name)
                continue

            content = path.read_text().strip()
            version = hashlib.sha256(content.encode()).hexdigest()[:12]

            _, created = AIPromptDocument.objects.get_or_create(
                job_type=job,
                name=path.stem,
                version=version,
                defaults={"content": content, "is_active": True},
            )
            if created:
                # Retire older versions so only one is active per job/name.
                AIPromptDocument.objects.filter(job_type=job, name=path.stem).exclude(
                    version=version
                ).update(is_active=False)
                written += 1
            else:
                unchanged += 1

        reset_cache()
        self.stdout.write(
            self.style.SUCCESS(f"AI documents: {written} new, {unchanged} unchanged.")
        )
        if skipped:
            self.stdout.write(
                self.style.WARNING(
                    "Ignored - filename does not match a job type: " + ", ".join(skipped)
                )
            )

        missing = sorted(jobs - set(AIPromptDocument.objects.values_list("job_type", flat=True)))
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    "No guidance yet, running on the terse fallback:\n  " + "\n  ".join(missing)
                )
            )
