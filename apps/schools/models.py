"""Tenants and the people in them.

`School` is the tenant boundary: almost every other row in the product reaches
a school through a foreign key, and the portal mixins in
`apps.common.permissions` filter on that path. `Grade` and `SchoolClass` are
deliberately shared reference data — they describe the national year
structure, not one school's private taxonomy.
"""

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Upper
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel
from apps.schools.enums import ClassSystem, Gender, GuardianRelationship

#: Abbreviations become the visible prefix of every student/teacher id, so they
#: are constrained to something safe to print, type and search on.
ABBREVIATION_VALIDATOR = RegexValidator(
    regex=r"^[A-Z0-9]{2,12}$",
    message=_("Use 2-12 uppercase letters or digits, e.g. GHS or ROYAL01."),
)

PHONE_VALIDATOR = RegexValidator(
    regex=r"^\+?[0-9]{7,15}$",
    message=_("Enter a phone number in international or local digits, 7-15 characters."),
)


class AcademicSession(BaseModel):
    """A school year, e.g. 2024/2025.

    Shared across schools: everyone runs the same calendar year pair, so a
    per-school copy would only duplicate rows without adding information.
    """

    start_year = models.PositiveSmallIntegerField(
        _("start year"), validators=[MinValueValidator(1900)]
    )
    end_year = models.PositiveSmallIntegerField(_("end year"), validators=[MinValueValidator(1901)])

    class Meta:
        verbose_name = _("session")
        verbose_name_plural = _("sessions")
        ordering = ["-start_year"]
        constraints = [
            models.UniqueConstraint(
                fields=["start_year", "end_year"], name="session_year_pair_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(end_year__gt=models.F("start_year")),
                name="session_end_year_after_start",
            ),
        ]

    def __str__(self) -> str:
        return self.label

    @property
    def label(self) -> str:
        return f"{self.start_year}/{self.end_year}"


class Grade(BaseModel):
    """A year group — "Grade 1", "Primary 4". Shared reference data."""

    name = models.CharField(_("name"), max_length=64)

    class Meta:
        verbose_name = _("grade")
        verbose_name_plural = _("grades")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Upper("name"), name="grade_name_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.name


class SchoolClass(BaseModel):
    """A stream within a grade — "Grade 1" arm "A".

    Schools create their own classes against the shared `Grade` list, so this
    is tenant-scoped: two schools naming a class the same thing is normal, and
    uniqueness only has to hold within one school.

    Named `SchoolClass` because `class` is a Python keyword, which would make
    the foreign keys pointing here unnameable.
    """

    school = models.ForeignKey(
        "schools.School",
        on_delete=models.CASCADE,
        related_name="classes",
        verbose_name=_("school"),
    )
    grade = models.ForeignKey(
        Grade, on_delete=models.PROTECT, related_name="classes", verbose_name=_("grade")
    )
    name = models.CharField(
        _("name"),
        max_length=16,
        help_text=_('The arm or stream, e.g. "A" or "2".'),
    )

    class Meta:
        verbose_name = _("class")
        verbose_name_plural = _("classes")
        ordering = ["grade__name", "name"]
        constraints = [
            models.UniqueConstraint(
                "school", "grade", Upper("name"), name="class_school_grade_name_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["school", "grade"], name="class_school_grade_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.grade.name} {self.name}"


class School(BaseModel):
    """A tenant.

    Credentials live on the linked `users.User` (email, password, verification,
    throttling, JWT) rather than being duplicated here; `email` and
    `email_verified` below are read-through properties so callers that think in
    terms of a school still read naturally.
    """

    user = models.OneToOneField(
        "users.User",
        on_delete=models.CASCADE,
        related_name="school",
        verbose_name=_("login account"),
    )
    name = models.CharField(_("name"), max_length=255, db_index=True)
    logo = models.ForeignKey(
        "media_assets.MediaAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="school_logos",
        verbose_name=_("logo"),
    )
    abbreviation = models.CharField(
        _("abbreviation"), max_length=12, validators=[ABBREVIATION_VALIDATOR]
    )
    class_system = models.CharField(
        _("class system"),
        max_length=16,
        choices=ClassSystem.choices,
        default=ClassSystem.GRADE,
    )
    current_session = models.ForeignKey(
        AcademicSession,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schools",
        verbose_name=_("current session"),
    )

    class Meta:
        verbose_name = _("school")
        verbose_name_plural = _("schools")
        ordering = ["name"]
        constraints = [
            # The abbreviation prefixes every student and teacher id, so it has
            # to be globally unique for those ids to stay meaningful.
            models.UniqueConstraint(Upper("abbreviation"), name="school_abbreviation_ci_unique"),
        ]

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.abbreviation = self.abbreviation.upper().strip()
        return super().save(*args, **kwargs)

    @property
    def email(self) -> str:
        return self.user.email

    @property
    def email_verified(self) -> bool:
        return self.user.email_verified


class Teacher(BaseModel):
    """A member of teaching staff. Logs in; owns assessments."""

    user = models.OneToOneField(
        "users.User",
        on_delete=models.CASCADE,
        related_name="teacher",
        verbose_name=_("login account"),
    )
    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="teachers", verbose_name=_("school")
    )
    teacher_id = models.CharField(
        _("teacher id"),
        max_length=32,
        help_text=_("Human-facing identifier, prefixed with the school abbreviation."),
    )
    first_name = models.CharField(_("first name"), max_length=150)
    last_name = models.CharField(_("last name"), max_length=150)
    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="teachers",
        verbose_name=_("class"),
    )

    class Meta:
        verbose_name = _("teacher")
        verbose_name_plural = _("teachers")
        ordering = ["last_name", "first_name"]
        constraints = [
            models.UniqueConstraint(Upper("teacher_id"), name="teacher_id_ci_unique"),
        ]
        indexes = [
            models.Index(fields=["school", "school_class"], name="teacher_school_class_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.teacher_id})"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Student(BaseModel):
    """A learner.

    Has no `User`: students never sign in with a password. They are admitted to
    an assessment by `apps.student_portal`, which mints a short-lived,
    student-scoped token.
    """

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="students", verbose_name=_("school")
    )
    first_name = models.CharField(_("first name"), max_length=150)
    last_name = models.CharField(_("last name"), max_length=150)
    date_of_birth = models.DateField(_("date of birth"))
    gender = models.CharField(_("gender"), max_length=16, choices=Gender.choices)
    school_class = models.ForeignKey(
        SchoolClass,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="students",
        verbose_name=_("class"),
        help_text=_("Only a disabled student may be left without a class."),
    )
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=_("Students hold no login, so disabling one is a flag here."),
    )
    student_id = models.CharField(
        _("student id"),
        max_length=32,
        help_text=_("Human-facing identifier, prefixed with the school abbreviation."),
    )
    guardian_name = models.CharField(_("guardian name"), max_length=255)
    guardian_phone_number = models.CharField(
        _("guardian phone number"), max_length=20, validators=[PHONE_VALIDATOR]
    )
    guardian_relationship = models.CharField(
        _("guardian relationship"), max_length=32, choices=GuardianRelationship.choices
    )

    class Meta:
        verbose_name = _("student")
        verbose_name_plural = _("students")
        ordering = ["last_name", "first_name"]
        constraints = [
            # Globally unique because it is what a student types to sign in to
            # an assessment — no school context is available at that point.
            models.UniqueConstraint(Upper("student_id"), name="student_id_ci_unique"),
            # An active student always belongs to a class; only a disabled one
            # may sit outside the structure, e.g. between sessions.
            models.CheckConstraint(
                condition=models.Q(school_class__isnull=False) | models.Q(is_active=False),
                name="student_active_requires_class",
            ),
        ]
        indexes = [
            models.Index(fields=["school", "school_class"], name="student_school_class_idx"),
            models.Index(fields=["school", "last_name"], name="student_school_name_idx"),
            models.Index(fields=["school", "is_active"], name="student_school_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.full_name} ({self.student_id})"

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


__all__ = [
    "ABBREVIATION_VALIDATOR",
    "AcademicSession",
    "Grade",
    "School",
    "SchoolClass",
    "Student",
    "Teacher",
]
