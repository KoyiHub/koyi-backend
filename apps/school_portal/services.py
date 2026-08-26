"""Business rules for the school management dashboard.

Views call these; they never touch the ORM directly. Anything that creates a
`User` alongside a domain row happens in a single transaction here, so a failed
teacher creation can never leave a login account with nothing behind it.
"""

from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q

from apps.assessments.enums import AssessmentStatus, ResultStatus
from apps.common.enums import UserRole
from apps.common.services import BaseService, NotFoundError, ValidationError
from apps.school_portal.repositories import (
    AssessmentOversightRepository,
    ReferenceDataRepository,
    SchoolRepository,
    StudentRepository,
    TeacherRepository,
)
from apps.schools.models import School, Student, Teacher
from apps.schools.utils import generate_student_id, generate_teacher_id
from apps.users.models import User

#: How many times to retry an id collision before giving up. Collisions need
#: two admissions to interleave inside the same millisecond, so one retry
#: already covers realistic contention; three makes it a non-event.
ID_GENERATION_ATTEMPTS = 3


class SchoolRegistrationService(BaseService):
    """Signs a school up: one `User` (the credentials) + one `School` (the tenant)."""

    def __init__(self) -> None:
        self.schools = SchoolRepository()

    @transaction.atomic
    def register(
        self,
        *,
        email: str,
        password: str,
        name: str,
        abbreviation: str,
        class_system: str,
        current_session=None,
        logo=None,
    ) -> School:
        abbreviation = abbreviation.upper().strip()
        if self.schools.abbreviation_taken(abbreviation):
            raise ValidationError(
                "That abbreviation is already in use.",
                detail={"abbreviation": ["Choose a different abbreviation."]},
            )

        user = User.objects.create_user(
            email=email,
            password=password,
            role=UserRole.SCHOOL,
            first_name=name[:150],
        )
        return School.objects.create(
            user=user,
            name=name,
            abbreviation=abbreviation,
            class_system=class_system,
            current_session=current_session,
            logo=logo,
        )


class SchoolProfileService(BaseService):
    """Reads and updates the acting school's own record."""

    def __init__(self, school: School) -> None:
        self.school = school
        self.schools = SchoolRepository()
        self.reference = ReferenceDataRepository()

    def update(self, **fields) -> School:
        if "abbreviation" in fields:
            # The abbreviation is baked into every issued student and teacher
            # id; changing it would orphan them from their school.
            raise ValidationError(
                "The abbreviation cannot be changed after registration.",
                detail={"abbreviation": ["This field is immutable."]},
            )
        return self.schools.update(self.school, **fields)

    def set_current_session(self, session_id) -> School:
        session = self.reference.get_session(session_id)
        if session is None:
            raise NotFoundError("No such academic session.")
        return self.schools.update(self.school, current_session=session)


class TeacherManagementService(BaseService):
    """Creates and maintains teaching staff for one school."""

    def __init__(self, school: School) -> None:
        self.school = school
        self.teachers = TeacherRepository(school)
        self.reference = ReferenceDataRepository()

    def list(self, *, search: str = "", school_class_id=None):
        queryset = self.teachers.search(search)
        if school_class_id:
            queryset = queryset.filter(school_class_id=school_class_id)
        return queryset

    def get(self, teacher_pk) -> Teacher:
        teacher = self.teachers.get_or_none(pk=teacher_pk)
        if teacher is None:
            raise NotFoundError("No such teacher in this school.")
        return teacher

    @transaction.atomic
    def create(
        self,
        *,
        email: str,
        password: str,
        first_name: str,
        last_name: str,
        school_class=None,
    ) -> Teacher:
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(
                "That email address already has an account.",
                detail={"email": ["This email is already in use."]},
            )
        if school_class is not None and not self._class_is_usable(school_class):
            raise ValidationError(
                "That class does not exist.", detail={"school_class": ["Unknown."]}
            )

        user = User.objects.create_user(
            email=email,
            password=password,
            role=UserRole.TEACHER,
            first_name=first_name,
            last_name=last_name,
            # Staff accounts are created by an authenticated school, so the
            # address is already vouched for; no second verification round-trip.
            email_verified=True,
        )
        return self._create_with_generated_id(
            user=user,
            first_name=first_name,
            last_name=last_name,
            school_class=school_class,
        )

    def _create_with_generated_id(self, **fields) -> Teacher:
        last_error: IntegrityError | None = None
        for _attempt in range(ID_GENERATION_ATTEMPTS):
            try:
                # Savepoint so a collision does not poison the outer atomic block.
                with transaction.atomic():
                    return Teacher.objects.create(
                        school=self.school,
                        teacher_id=generate_teacher_id(self.school),
                        **fields,
                    )
            except IntegrityError as exc:
                last_error = exc
        raise ValidationError("Could not allocate a teacher id, please retry.") from last_error

    def update(self, teacher: Teacher, **fields) -> Teacher:
        if fields.get("school_class") is not None and not self._class_is_usable(
            fields["school_class"]
        ):
            raise ValidationError("That class does not exist.")
        return self.teachers.update(teacher, **fields)

    @transaction.atomic
    def deactivate(self, teacher: Teacher) -> Teacher:
        """Revokes the login without destroying authored assessments."""
        teacher.user.is_active = False
        teacher.user.save(update_fields=["is_active"])
        return teacher

    def _class_is_usable(self, school_class) -> bool:
        return self.reference.get_class(school_class.pk) is not None


class StudentManagementService(BaseService):
    """Admits and maintains learners for one school."""

    def __init__(self, school: School) -> None:
        self.school = school
        self.students = StudentRepository(school)

    def list(self, *, search: str = "", school_class_id=None):
        queryset = self.students.search(search)
        if school_class_id:
            queryset = queryset.filter(school_class_id=school_class_id)
        return queryset

    def get(self, student_pk) -> Student:
        student = self.students.get_or_none(pk=student_pk)
        if student is None:
            raise NotFoundError("No such student in this school.")
        return student

    def create(self, **fields) -> Student:
        last_error: IntegrityError | None = None
        for _attempt in range(ID_GENERATION_ATTEMPTS):
            try:
                with transaction.atomic():
                    return Student.objects.create(
                        school=self.school,
                        student_id=generate_student_id(self.school),
                        **fields,
                    )
            except IntegrityError as exc:
                last_error = exc
        raise ValidationError("Could not allocate a student id, please retry.") from last_error

    def update(self, student: Student, **fields) -> Student:
        # `student_id` is what the learner types to sit an assessment; it is
        # issued once and never edited.
        fields.pop("student_id", None)
        return self.students.update(student, **fields)

    def delete(self, student: Student) -> None:
        self.students.delete(student)


class SchoolOverviewService(BaseService):
    """The dashboard's headline numbers, in as few queries as possible."""

    def __init__(self, school: School) -> None:
        self.school = school
        self.students = StudentRepository(school)
        self.teachers = TeacherRepository(school)
        self.assessments = AssessmentOversightRepository(school)

    def summary(self) -> dict:
        results = self.assessments.get_queryset().aggregate(
            total=Count("id", distinct=True),
            active=Count("id", filter=Q(status=AssessmentStatus.ACTIVE), distinct=True),
        )
        graded = self.assessments.get_queryset().aggregate(
            average=Avg("results__score", filter=Q(results__status=ResultStatus.GRADED))
        )["average"]
        return {
            "students": self.students.count(school=self.school),
            "teachers": self.teachers.count(school=self.school),
            "assessments": results["total"],
            "active_assessments": results["active"],
            "assessment_status_breakdown": self.assessments.status_breakdown(),
            "average_graded_score": graded,
            "current_session": (
                self.school.current_session.label if self.school.current_session else None
            ),
        }
