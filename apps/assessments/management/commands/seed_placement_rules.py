"""Seed the per-level placement thresholds from the taxonomy.

Each rule says how many core skills must pass for one level to count as
passed. The default is three quarters of the skills that cover that level,
rounded up - but the point of storing it per level is that the default is not
right everywhere, and this command prints where it bites so the numbers can be
tuned rather than discovered later.

Re-run after changing which levels a skill covers; `applicable_skills` is
recorded so drift between the rules and the taxonomy is visible.
"""

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.assessments.models import PlacementRule
from apps.common.enums import FLN_LEVELS, Domain
from apps.curriculum.models import Skill


class Command(BaseCommand):
    help = "Seed or refresh placement thresholds from the current taxonomy."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Overwrite thresholds that have been tuned by hand.",
        )

    @transaction.atomic
    def handle(self, **options: Any) -> None:
        reset: bool = options["reset"]
        written = kept = 0
        strict: list[str] = []

        for domain in Domain.values:
            for level in FLN_LEVELS:
                applicable = Skill.objects.filter(
                    domain=domain, is_core=True, min_level__lte=level, max_level__gte=level
                ).count()
                if not applicable:
                    continue

                default = -(-applicable * 3 // 4)  # ceil(3/4 * n)
                rule = PlacementRule.objects.filter(domain=domain, fln_level=level).first()

                if rule and not reset:
                    # Keep a hand-tuned number, but keep the drift visible.
                    if rule.applicable_skills != applicable:
                        rule.applicable_skills = applicable
                        rule.save(update_fields=["applicable_skills", "updated_at"])
                    kept += 1
                    if rule.required_skills >= applicable:
                        strict.append(f"{domain} L{level}: {rule.required_skills}/{applicable}")
                    continue

                PlacementRule.objects.update_or_create(
                    domain=domain,
                    fln_level=level,
                    defaults={"required_skills": default, "applicable_skills": applicable},
                )
                written += 1
                if default >= applicable:
                    strict.append(f"{domain} L{level}: {default}/{applicable}")

        self.stdout.write(
            self.style.SUCCESS(f"Placement rules: {written} written, {kept} left as tuned.")
        )
        if strict:
            self.stdout.write(
                self.style.WARNING(
                    "\nAll-or-nothing levels - every applicable skill must pass:\n  "
                    + "\n  ".join(strict)
                    + "\n\nA fraction cannot discretise at small N. These are defensible "
                    "where only two skills define a level, and worth a second look "
                    "where more do."
                )
            )
