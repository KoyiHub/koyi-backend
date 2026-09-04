"""Business rules for the school management dashboard.

Views call these; they never touch the ORM directly. Anything that creates a
`User` alongside a domain row happens in a single transaction here, so a failed
teacher creation can never leave a login account with nothing behind it.
"""

from collections.abc import Sequence

from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q

from apps.assessments.enums import AssessmentStatus, ResultStatus
from apps.common.enums import FLN_LEVELS, ActivityAction, Domain, UserRole
from apps.common.services import (
    ActivityService,
    AuthenticationError,
    BaseService,
    NotFoundError,
    ValidationError,
)
from apps.school_portal.authentication import SchoolTokenObtainPairSerializer
from apps.school_portal.repositories import (
    ActivityRepository,
    AssessmentOversightRepository,
    ReferenceDataRepository,
    SchoolClassRepository,
    SchoolRepository,
    StudentRepository,
    TeacherRepository,
)
from apps.schools.models import School, SchoolClass, Student, StudentProfile, Teacher
from apps.schools.utils import generate_student_id, generate_teacher_id
from apps.users.enums import VerificationPurpose
from apps.users.models import User
from apps.users.verification import IssuedCode, VerificationService

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


class SchoolAuthService(BaseService):
    """Signing in, and every step that surrounds it.

    School management holds the keys to a school's whole record, so both
    registration and sign-in are two-step: credentials first, then a code sent
    to the address on file. The second step is what proves the person is at
    that mailbox, which is the only thing a password on its own cannot show.

    Every failure here reads the same. A wrong password, an address nobody has
    registered, a teacher trying the school door - all produce one message, so
    the endpoint cannot be turned into a directory of which schools use Koyi.
    """

    def __init__(self) -> None:
        self.codes = VerificationService()
        self.schools = SchoolRepository()

    # --- registration -----------------------------------------------------

    def register(self, **fields) -> School:
        """Create the school, then email the code that activates it."""
        school = SchoolRegistrationService().register(**fields)
        self.codes.issue(user=school.user, purpose=VerificationPurpose.REGISTER)
        return school

    def verify_registration(self, *, email: str, code: str) -> dict:
        user = self._user_for(email)
        if user is None:
            # Same shape as a wrong code, so a failed verification cannot be
            # used to test whether an address is registered.
            raise ValidationError("That code is not valid. Request a new one.")

        self.codes.verify(user=user, purpose=VerificationPurpose.REGISTER, code=code)
        self._mark_verified(user)
        return SchoolTokenObtainPairSerializer.issue_for(user)

    def resend_registration_code(self, *, email: str) -> None:
        """Always succeeds from the caller's point of view.

        Nothing is sent to an address that is not registered, or to one that is
        already verified, but the response does not say which - the form must
        read "if that address is registered, a code is on its way".
        """
        user = self._user_for(email)
        if user is None or user.email_verified:
            return
        self.codes.issue(user=user, purpose=VerificationPurpose.REGISTER)

    # --- sign-in ----------------------------------------------------------

    def start_login(self, *, email: str, password: str) -> IssuedCode:
        """Check the password, email a code, and hand back the challenge.

        The challenge is what the client returns in step two. It carries no
        information about the account, which is why the second step does not
        need the address or the password again.
        """
        user = authenticate(username=email, password=password)
        if user is None:
            raise AuthenticationError(SchoolTokenObtainPairSerializer.invalid_credentials_message)
        SchoolTokenObtainPairSerializer.check_eligible(user)

        return self.codes.issue(user=user, purpose=VerificationPurpose.LOGIN)

    def complete_login(self, *, challenge: str, code: str) -> dict:
        row = self.codes.verify(purpose=VerificationPurpose.LOGIN, challenge=challenge, code=code)
        user = row.user
        # Reaching a code at that address is the same proof registration asks
        # for, so a school that lost the signup email is not stranded.
        self._mark_verified(user)
        return SchoolTokenObtainPairSerializer.issue_for(user)

    # --- password recovery ------------------------------------------------

    def request_password_reset(self, *, email: str) -> None:
        """Silent about whether the address exists. Always reports success."""
        user = self._user_for(email)
        if user is None:
            return
        self.codes.issue(user=user, purpose=VerificationPurpose.PASSWORD_RESET)

    def verify_password_reset(self, *, email: str, code: str) -> IssuedCode:
        user = self._user_for(email)
        if user is None:
            raise ValidationError("That code is not valid. Request a new one.")

        self.codes.verify(user=user, purpose=VerificationPurpose.PASSWORD_RESET, code=code)
        # The six digits are spent here. What the confirm step carries is a
        # fresh handle nobody could have read over a shoulder, so a code
        # glimpsed in a notification cannot be turned into a password change.
        return self.codes.issue_handle(
            user=user, purpose=VerificationPurpose.PASSWORD_RESET_CONFIRM
        )

    @transaction.atomic
    def confirm_password_reset(self, *, reset_token: str, password: str) -> None:
        row = self.codes.redeem_handle(
            purpose=VerificationPurpose.PASSWORD_RESET_CONFIRM, challenge=reset_token
        )
        user = row.user
        user.set_password(password)
        user.save(update_fields=["password"])

    def change_password(self, *, user: User, current_password: str, new_password: str) -> None:
        if not user.check_password(current_password):
            raise ValidationError(
                "That is not your current password.",
                detail={"current_password": ["Incorrect."]},
            )
        user.set_password(new_password)
        user.save(update_fields=["password"])

    # --- internals --------------------------------------------------------

    def _user_for(self, email: str) -> User | None:
        return User.objects.filter(
            email__iexact=email.strip(), role=UserRole.SCHOOL, is_active=True
        ).first()

    def _mark_verified(self, user: User) -> None:
        if not user.email_verified:
            user.email_verified = True
            user.save(update_fields=["email_verified"])


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


class SchoolClassManagementService(BaseService):
    """Classes, which a school creates for itself against the shared grades.

    Grades are ours and classes are theirs: a school picks "Grade 2" from our
    list and names its own arm within it, so uniqueness only has to hold
    inside one school.
    """

    def __init__(self, school: School) -> None:
        self.school = school
        self.classes = SchoolClassRepository(school)
        self.reference = ReferenceDataRepository()

    def list(self):
        return self.classes.all()

    def get(self, pk) -> SchoolClass:
        school_class = self.classes.get_or_none(pk=pk)
        if school_class is None:
            raise NotFoundError("No such class in this school.")
        return school_class

    def create(self, *, grade_id, name: str) -> SchoolClass:
        grade = self.reference.get_grade(grade_id)
        if grade is None:
            raise ValidationError("That grade does not exist.", detail={"grade": ["Unknown."]})
        if self.classes.name_taken(grade, name):
            raise ValidationError(
                "That class already exists.",
                detail={"name": [f"{grade.name} {name} is already on your list."]},
            )
        return SchoolClass.objects.create(school=self.school, grade=grade, name=name.strip())

    def delete(self, school_class: SchoolClass) -> None:
        """Refused while anyone is still in it.

        `Student.school_class` is PROTECT, so the database would refuse this
        anyway; catching it here turns an integrity error into an answer the
        dashboard can act on - transfer them first.
        """
        if self.classes.has_students(school_class):
            raise ValidationError(
                "That class still has students in it.",
                detail={"school_class": ["Transfer or remove its students first."]},
            )
        school_class.delete()


class ConfirmedDeletion:
    """The two steps between clicking delete and the row going away.

    Removing a teacher or a child is the one thing in this dashboard that
    cannot be undone from the dashboard, so it asks for a code sent to the
    administrator's own address. That is not about proving who they are - they
    are already signed in - it is about making the action deliberate, and about
    making a stolen session unable to quietly empty a school.

    The code goes to the person doing the removing, never to the person being
    removed.
    """

    def __init__(self, purpose: str, noun: str) -> None:
        self.purpose = purpose
        self.noun = noun
        self.codes = VerificationService()

    def request(self, *, actor: User, subject) -> None:
        self.codes.issue(user=actor, purpose=self.purpose, subject_id=subject.pk)

    def confirm(self, *, actor: User, subject, code: str) -> None:
        row = self.codes.verify(user=actor, purpose=self.purpose, code=code)
        if row.subject_id != subject.pk:
            # A live code for a different row. Requesting a second deletion
            # retires the first code, so this is someone confirming the wrong
            # one - which must not be allowed to succeed by accident.
            raise ValidationError(
                f"That code was sent for a different {self.noun}.",
                detail={"code": ["Request the code again for this record."]},
            )


class TeacherManagementService(BaseService):
    """Creates and maintains teaching staff for one school."""

    def __init__(self, school: School) -> None:
        self.school = school
        self.teachers = TeacherRepository(school)
        self.classes = SchoolClassRepository(school)
        self.deletion = ConfirmedDeletion(VerificationPurpose.DELETE_TEACHER, "teacher")

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
    def set_active(self, teacher: Teacher, *, active: bool, actor: User | None = None) -> Teacher:
        """Revokes or restores the login without touching anything they wrote.

        This is what "remove a teacher" should almost always mean. Their
        assessments, groups and plans stay exactly where they are, and the only
        change is that they can no longer sign in - which is reversible on the
        day they come back.
        """
        teacher.user.is_active = active
        teacher.user.save(update_fields=["is_active"])
        if not active:
            self._log(
                ActivityAction.TEACHER_DISABLED,
                f"Teacher disabled: {teacher.full_name}",
                f"{teacher.teacher_id} can no longer sign in.",
                teacher=teacher,
                actor=actor,
            )
        return teacher

    def send_password_reset(self, teacher: Teacher) -> None:
        """Email the teacher a code they can set a new password with.

        Admin-triggered rather than self-service, because a teacher who cannot
        sign in is standing in front of someone who can.
        """
        VerificationService().issue(user=teacher.user, purpose=VerificationPurpose.PASSWORD_RESET)

    def request_delete(self, teacher: Teacher, *, actor: User) -> None:
        self.deletion.request(actor=actor, subject=teacher)

    @transaction.atomic
    def delete(self, teacher: Teacher, *, actor: User, code: str) -> None:
        """Remove the teacher, keeping everything they authored.

        The row is soft-deleted and the login revoked; assessments hold their
        author until the purge, and lose it rather than being destroyed with
        them. What the school sees immediately is that the teacher is gone.
        """
        self.deletion.confirm(actor=actor, subject=teacher, code=code)
        teacher.user.is_active = False
        teacher.user.save(update_fields=["is_active"])
        teacher.delete()
        self._log(
            ActivityAction.TEACHER_DISABLED,
            f"Teacher removed: {teacher.full_name}",
            f"{teacher.teacher_id} was removed from the school.",
            teacher=None,
            actor=actor,
        )

    def _class_is_usable(self, school_class) -> bool:
        # Scoped lookup: a class from another school must not validate here.
        return self.classes.get_or_none(pk=school_class.pk) is not None

    def _log(self, action, label, description, *, teacher, actor) -> None:
        ActivityService().record(
            school=self.school,
            action=action,
            label=label,
            description=description,
            actor_user=actor,
            teacher=teacher,
        )


class StudentManagementService(BaseService):
    """Admits and maintains learners for one school."""

    def __init__(self, school: School) -> None:
        self.school = school
        self.students = StudentRepository(school)
        self.classes = SchoolClassRepository(school)
        self.deletion = ConfirmedDeletion(VerificationPurpose.DELETE_STUDENT, "student")

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

    @transaction.atomic
    def set_active(self, student: Student, *, active: bool, actor: User | None = None) -> Student:
        """Disable or re-enable a child.

        A child holds no login, so this is a flag rather than a revoked
        credential: it takes them out of class lists, groups and assignment,
        without touching the record of what they have already done.
        """
        if active and student.school_class is None:
            # The constraint would refuse this anyway. Saying so plainly is
            # more useful than surfacing an integrity error.
            raise ValidationError(
                "Give the student a class before re-enabling them.",
                detail={"school_class": ["An active student always has a class."]},
            )
        student.is_active = active
        student.save(update_fields=["is_active", "updated_at"])
        if not active:
            self._log(
                ActivityAction.STUDENT_DISABLED,
                f"Student disabled: {student.full_name}",
                f"{student.student_id} was taken off the active roll.",
                student=student,
                actor=actor,
            )
        return student

    @transaction.atomic
    def transfer(
        self, *, student_ids, to_class: SchoolClass, actor: User | None = None
    ) -> Sequence[Student]:
        """Move named children into one class.

        Scoped twice over: the class is fetched through this school's
        repository, and so are the children, so neither half of the move can
        reach another tenant even if both ids are guessed.
        """
        target = self._usable_class(to_class)
        students = list(self.students.filter(pk__in=list(student_ids)))
        if not students:
            raise ValidationError(
                "None of those students are in this school.",
                detail={"student_ids": ["Unknown."]},
            )

        moved = []
        for student in students:
            if student.school_class_id == target.pk:
                continue
            was = str(student.school_class) if student.school_class else "no class"
            student.school_class = target
            student.save(update_fields=["school_class", "updated_at"])
            self._log(
                ActivityAction.STUDENT_TRANSFERRED,
                f"Student transferred: {student.full_name}",
                f"Moved from {was} to {target}.",
                student=student,
                actor=actor,
                school_class=target,
            )
            moved.append(student)
        return moved

    @transaction.atomic
    def transfer_class(
        self, *, from_class: SchoolClass, to_class: SchoolClass, actor: User | None = None
    ) -> int:
        """Move a whole class at once - what happens at the end of a year.

        Logged as one entry rather than one per child. Forty rows saying the
        same thing would bury the rest of the feed, and the thing that
        happened really was a single act.
        """
        source = self._usable_class(from_class)
        target = self._usable_class(to_class)
        if source.pk == target.pk:
            raise ValidationError(
                "That is the same class.", detail={"to_class": ["Choose a different class."]}
            )

        moved = self.students.for_class(source).update(school_class=target)
        if moved:
            self._log(
                ActivityAction.STUDENT_TRANSFERRED,
                f"Class transferred: {source} to {target}",
                f"{moved} students moved.",
                student=None,
                actor=actor,
                school_class=target,
            )
        return moved

    def request_delete(self, student: Student, *, actor: User) -> None:
        self.deletion.request(actor=actor, subject=student)

    @transaction.atomic
    def delete(self, student: Student, *, actor: User, code: str) -> None:
        """Remove a child, once the code confirms it was meant.

        Soft-deleted: their results cascade off this row, so an immediate hard
        delete would destroy a term of diagnosis on a misclick with nothing to
        restore from. The purge finishes the job when the retention window
        closes.
        """
        self.deletion.confirm(actor=actor, subject=student, code=code)
        student.delete()
        self._log(
            ActivityAction.STUDENT_DISABLED,
            f"Student removed: {student.full_name}",
            f"{student.student_id} was removed from the school.",
            student=None,
            actor=actor,
        )

    def fln(self, student: Student) -> dict:
        """What management sees of a child: two levels and a score history.

        Deliberately not the diagnostic breakdown - which subskills are weak
        and what to do about them is the teacher's view, and is read on a
        different page by a different person.
        """
        profile = getattr(student, "profile", None)
        results = (
            student.results.select_related("assessment")
            .filter(status=ResultStatus.GRADED)
            .order_by("-marked_at", "-created_at")[:10]
        )
        return {
            "student": student,
            "literacy_level": profile.literacy_level if profile else None,
            "numeracy_level": profile.numeracy_level if profile else None,
            "last_assessed_at": profile.last_assessed_at if profile else None,
            "recent_results": [
                {
                    "assessment": row.assessment.name,
                    "date": row.marked_at or row.created_at,
                    "percentage": row.percentage,
                    "status": row.status,
                }
                for row in results
            ],
        }

    def _usable_class(self, school_class: SchoolClass) -> SchoolClass:
        scoped = self.classes.get_or_none(pk=school_class.pk)
        if scoped is None:
            raise ValidationError(
                "That class does not exist.", detail={"school_class": ["Unknown."]}
            )
        return scoped

    def _log(self, action, label, description, *, student, actor, school_class=None) -> None:
        ActivityService().record(
            school=self.school,
            action=action,
            label=label,
            description=description,
            actor_user=actor,
            student=student,
            school_class=school_class,
        )


class ActivityFeedService(BaseService):
    """The school's audit log, narrowed.

    Thin over the repository on purpose - there are no rules here, only
    filters. It exists so the view keeps its habit of calling a service rather
    than reaching for a queryset, which is what stops tenant scoping from
    drifting into the view layer one endpoint at a time.
    """

    def __init__(self, school: School) -> None:
        self.school = school
        self.activities = ActivityRepository(school)

    def feed(self, **filters):
        return self.activities.feed(**filters)


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
            active=Count("id", filter=Q(status__in=AssessmentStatus.sittable()), distinct=True),
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
            "level_distribution": self._level_distribution(),
            "average_graded_score": graded,
            "current_session": (
                self.school.current_session.label if self.school.current_session else None
            ),
        }

    def _level_distribution(self) -> dict:
        """How many children in the school currently sit at each level.

        This is the number a head teacher acts on, and it is why the average
        below it is close to meaningless: literacy and numeracy move
        independently, so a figure averaged across both describes neither.

        Every level is keyed even at zero - a chart that omits empty levels
        reads as a narrower spread than the school actually has - and
        `unplaced` counts the children no assessment has reached yet, which is
        usually the most actionable number on the page.
        """
        counts: dict = {
            domain: dict.fromkeys((str(level) for level in FLN_LEVELS), 0)
            for domain in Domain.values
        }
        unplaced: dict[str, int] = dict.fromkeys(Domain.values, 0)

        rows = StudentProfile.objects.filter(
            student__school=self.school, student__is_active=True
        ).values_list("literacy_level", "numeracy_level")
        placed: dict[str, int] = dict.fromkeys(Domain.values, 0)
        for literacy, numeracy in rows:
            pairs = ((Domain.LITERACY.value, literacy), (Domain.NUMERACY.value, numeracy))
            for domain, level in pairs:
                if level is None:
                    continue
                counts[domain][str(level)] += 1
                placed[domain] += 1

        active = self.students.count(school=self.school, is_active=True)
        for name in Domain.values:
            unplaced[name] = active - placed[name]
        return {"levels": counts, "unplaced": unplaced}
