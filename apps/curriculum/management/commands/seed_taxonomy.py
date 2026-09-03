"""Load the FLN skill taxonomy from its fixture.

The fixture is the source of truth and lives in version control; this command
mirrors it into the database. It is idempotent — matching on `code` — so it
can run on every deploy without duplicating rows or disturbing the questions
already pointing at a subskill.
"""

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.curriculum.models import Skill, Subskill

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "taxonomy.json"


class Command(BaseCommand):
    help = "Seed or refresh the skill taxonomy from apps/curriculum/fixtures/taxonomy.json"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--path",
            default=str(FIXTURE),
            help="Fixture to load (defaults to the bundled taxonomy).",
        )
        parser.add_argument(
            "--prune",
            action="store_true",
            help="Delete skills and subskills absent from the fixture. Refuses if in use.",
        )

    @transaction.atomic
    def handle(self, **options: Any) -> None:
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"No fixture at {path}")

        payload = json.loads(path.read_text())
        skills = payload.get("skills", [])
        if not skills:
            raise CommandError("Fixture contains no skills.")

        seen_skills: set[str] = set()
        seen_subskills: set[str] = set()
        created = updated = 0

        for entry in skills:
            skill, was_created = Skill.objects.update_or_create(
                code=entry["code"],
                defaults={
                    "name": entry["name"],
                    "domain": entry["domain"],
                    "min_level": entry["min_level"],
                    "max_level": entry["max_level"],
                    "is_core": entry.get("is_core", True),
                    "display_order": entry.get("display_order", 0),
                },
            )
            seen_skills.add(skill.code)
            created += was_created
            updated += not was_created

            for sub in entry.get("subskills", []):
                subskill, sub_created = Subskill.objects.update_or_create(
                    code=sub["code"],
                    defaults={
                        "skill": skill,
                        "name": sub["name"],
                        "min_level": sub.get("min_level"),
                        "max_level": sub.get("max_level"),
                        "display_order": sub.get("display_order", 0),
                    },
                )
                seen_subskills.add(subskill.code)
                created += sub_created
                updated += not sub_created

        if options["prune"]:
            self._prune(seen_skills, seen_subskills)

        self.stdout.write(
            self.style.SUCCESS(
                f"Taxonomy seeded: {created} created, {updated} updated "
                f"({len(seen_skills)} skills, {len(seen_subskills)} subskills)."
            )
        )
        self._report_applicability()

    def _prune(self, seen_skills: set[str], seen_subskills: set[str]) -> None:
        # A subskill with questions attached is protected by the FK; surface
        # that as a readable error rather than an IntegrityError.
        stale = Subskill.objects.exclude(code__in=seen_subskills)
        in_use = stale.filter(questions__isnull=False).distinct()
        if in_use.exists():
            names = ", ".join(in_use.values_list("code", flat=True))
            raise CommandError(f"Refusing to prune subskills still carrying questions: {names}")
        removed_subs = stale.delete()[0]
        removed_skills = Skill.objects.exclude(code__in=seen_skills).delete()[0]
        if removed_subs or removed_skills:
            self.stdout.write(f"Pruned {removed_skills} skills and {removed_subs} subskills.")

    def _report_applicability(self) -> None:
        """Print how many skills gate each level.

        Worth seeing on every seed: the placement threshold is a fraction of
        the skills applicable at a level, so these counts are what decide how
        strict each level actually is.
        """
        self.stdout.write("\nSkills applicable per level:")
        for level in range(1, 6):
            row = []
            for domain in ("literacy", "numeracy"):
                count = Skill.objects.filter(
                    domain=domain, is_core=True, min_level__lte=level, max_level__gte=level
                ).count()
                row.append(f"{domain} {count}")
            self.stdout.write(f"  L{level}: " + "  ".join(row))
