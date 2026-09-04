"""Helpers for the human-facing identifiers schools hand out.

Ids are `<ABBREVIATION>-<sequence>` for students and `<ABBREVIATION>-T<sequence>`
for teachers. The `T` keeps the two from colliding in a search box, which they
otherwise would for a small school.

These read-then-increment, so two concurrent admissions can pick the same
number; the unique constraint on the column is what actually guarantees
correctness, and the calling service retries on `IntegrityError`.
"""


def _next_sequence(existing_ids, prefix: str) -> int:
    """One past the highest numeric suffix already issued under `prefix`.

    Derived from the maximum rather than from a row count so that deleting a
    student never causes the next one to reuse a retired id.
    """
    cut = len(prefix)
    numbers = [
        int(value[cut:])
        for value in existing_ids
        if value[:cut].upper() == prefix and value[cut:].isdigit()
    ]
    return max(numbers, default=0) + 1


def generate_student_id(school) -> str:
    """e.g. `GHS-0042` for the 42nd student admitted to school `GHS`."""
    from apps.schools.models import Student

    prefix = f"{school.abbreviation.upper()}-"
    # `all_objects`, so a soft-deleted child still holds their number. Their
    # row is still in the table until the purge runs, and the unique constraint
    # does not know the difference.
    existing = Student.all_objects.filter(student_id__istartswith=prefix).values_list(
        "student_id", flat=True
    )
    return f"{prefix}{_next_sequence(existing, prefix):04d}"


def generate_teacher_id(school) -> str:
    """e.g. `GHS-T007` for the 7th teacher added to school `GHS`."""
    from apps.schools.models import Teacher

    prefix = f"{school.abbreviation.upper()}-T"
    existing = Teacher.all_objects.filter(teacher_id__istartswith=prefix).values_list(
        "teacher_id", flat=True
    )
    return f"{prefix}{_next_sequence(existing, prefix):03d}"
