"""Classes, staff, learners and the audit log, over HTTP.

The theme is tenancy and consequence. Nothing here may reach another school's
rows, and nothing that destroys a term of a child's work happens on one click.
"""

import pytest
from django.core import mail
from django.urls import reverse

from apps.activities.models import Activity
from apps.common.enums import ActivityAction
from apps.schools.factories import (
    GradeFactory,
    SchoolClassFactory,
    SchoolFactory,
    StudentFactory,
    TeacherFactory,
)
from apps.schools.models import Student, Teacher
from apps.users.enums import VerificationPurpose
from apps.users.models import VerificationCode

pytestmark = pytest.mark.django_db


def emailed_code() -> str:
    for message in reversed(mail.outbox):
        for token in message.body.split():
            if token.isdigit() and len(token) == 6:
                return token
    raise AssertionError("no code was emailed")


@pytest.fixture
def school_class(school):
    return SchoolClassFactory(school=school)


class TestClasses:
    def test_a_school_names_its_own_arm_within_one_of_our_grades(self, school_client):
        grade = GradeFactory(name="Grade 3")

        response = school_client.post(
            reverse("v1:school_portal:class-list"), {"grade": str(grade.pk), "name": "B"}
        )

        assert response.status_code == 201
        assert response.data["label"] == "Grade 3 B"

    def test_the_same_arm_twice_is_refused(self, school_client, school_class):
        response = school_client.post(
            reverse("v1:school_portal:class-list"),
            {"grade": str(school_class.grade.pk), "name": school_class.name},
        )
        assert response.status_code == 400

    def test_two_schools_may_both_have_a_grade_1_a(self, school_client, school_class):
        other = SchoolClassFactory(
            school=SchoolFactory(), grade=school_class.grade, name=school_class.name
        )
        assert other.pk != school_class.pk

    def test_a_class_with_students_in_it_cannot_be_deleted(self, school_client, school_class):
        StudentFactory(school=school_class.school, school_class=school_class)

        response = school_client.delete(
            reverse("v1:school_portal:class-detail", args=[school_class.pk])
        )

        assert response.status_code == 400
        assert "students" in response.data["error"]["message"]

    def test_an_empty_class_is_deleted(self, school_client, school_class):
        response = school_client.delete(
            reverse("v1:school_portal:class-detail", args=[school_class.pk])
        )
        assert response.status_code == 204

    def test_another_school_s_class_is_not_found(self, school_client):
        foreign = SchoolClassFactory(school=SchoolFactory())

        response = school_client.delete(reverse("v1:school_portal:class-detail", args=[foreign.pk]))
        assert response.status_code == 404


class TestTeacherLifecycle:
    def test_disabling_revokes_the_login_and_keeps_the_record(self, school_client, school):
        teacher = TeacherFactory(school=school)

        response = school_client.post(
            reverse("v1:school_portal:teacher-disable", args=[teacher.pk])
        )

        assert response.status_code == 200
        teacher.user.refresh_from_db()
        assert teacher.user.is_active is False
        assert Teacher.objects.filter(pk=teacher.pk).exists()

    def test_enabling_puts_them_back(self, school_client, school):
        teacher = TeacherFactory(school=school)
        school_client.post(reverse("v1:school_portal:teacher-disable", args=[teacher.pk]))

        school_client.post(reverse("v1:school_portal:teacher-enable", args=[teacher.pk]))

        teacher.user.refresh_from_db()
        assert teacher.user.is_active is True

    def test_deletion_takes_two_steps(self, school_client, school):
        teacher = TeacherFactory(school=school)

        requested = school_client.post(
            reverse("v1:school_portal:teacher-delete-request", args=[teacher.pk])
        )
        assert requested.status_code == 200

        confirmed = school_client.post(
            reverse("v1:school_portal:teacher-delete-confirm", args=[teacher.pk]),
            {"code": emailed_code()},
        )
        assert confirmed.status_code == 204

        # Hidden, not destroyed - the purge finishes the job later.
        assert not Teacher.objects.filter(pk=teacher.pk).exists()
        assert Teacher.all_objects.filter(pk=teacher.pk).exists()

    def test_the_confirmation_code_goes_to_the_administrator_not_the_teacher(
        self, school_client, school
    ):
        teacher = TeacherFactory(school=school)

        school_client.post(reverse("v1:school_portal:teacher-delete-request", args=[teacher.pk]))

        assert mail.outbox[-1].to == [school.email]

    def test_a_code_for_one_teacher_will_not_delete_another(self, school_client, school):
        first = TeacherFactory(school=school)
        second = TeacherFactory(school=school)

        school_client.post(reverse("v1:school_portal:teacher-delete-request", args=[first.pk]))
        code = emailed_code()

        response = school_client.post(
            reverse("v1:school_portal:teacher-delete-confirm", args=[second.pk]),
            {"code": code},
        )

        assert response.status_code == 400
        assert Teacher.objects.filter(pk=second.pk).exists()

    def test_deletion_without_a_code_is_refused(self, school_client, school):
        teacher = TeacherFactory(school=school)

        response = school_client.post(
            reverse("v1:school_portal:teacher-delete-confirm", args=[teacher.pk]),
            {"code": "000000"},
        )

        assert response.status_code == 400
        assert Teacher.objects.filter(pk=teacher.pk).exists()

    def test_an_admin_can_send_a_teacher_a_reset_code(self, school_client, school):
        teacher = TeacherFactory(school=school)

        response = school_client.post(
            reverse("v1:school_portal:teacher-password-reset", args=[teacher.pk])
        )

        assert response.status_code == 200
        assert mail.outbox[-1].to == [teacher.user.email]
        assert VerificationCode.objects.filter(
            user=teacher.user, purpose=VerificationPurpose.PASSWORD_RESET
        ).exists()


class TestStudentLifecycle:
    def test_disabling_takes_a_child_off_the_roll(self, school_client, school, school_class):
        student = StudentFactory(school=school, school_class=school_class)

        response = school_client.post(
            reverse("v1:school_portal:student-disable", args=[student.pk])
        )

        assert response.status_code == 200
        student.refresh_from_db()
        assert student.is_active is False

    def test_re_enabling_needs_a_class(self, school_client, school, school_class):
        student = StudentFactory(school=school, school_class=school_class)
        school_client.post(reverse("v1:school_portal:student-disable", args=[student.pk]))
        Student.objects.filter(pk=student.pk).update(school_class=None)

        response = school_client.post(reverse("v1:school_portal:student-enable", args=[student.pk]))

        assert response.status_code == 400

    def test_deletion_takes_two_steps_and_hides_rather_than_destroys(
        self, school_client, school, school_class
    ):
        student = StudentFactory(school=school, school_class=school_class)

        school_client.post(reverse("v1:school_portal:student-delete-request", args=[student.pk]))
        response = school_client.post(
            reverse("v1:school_portal:student-delete-confirm", args=[student.pk]),
            {"code": emailed_code()},
        )

        assert response.status_code == 204
        assert not Student.objects.filter(pk=student.pk).exists()
        assert Student.all_objects.filter(pk=student.pk).exists()

    def test_a_removed_child_keeps_their_number(self, school_client, school, school_class):
        student = StudentFactory(school=school, school_class=school_class)
        school_client.post(reverse("v1:school_portal:student-delete-request", args=[student.pk]))
        school_client.post(
            reverse("v1:school_portal:student-delete-confirm", args=[student.pk]),
            {"code": emailed_code()},
        )

        created = school_client.post(
            reverse("v1:school_portal:student-list"),
            {
                "first_name": "New",
                "last_name": "Child",
                "date_of_birth": "2018-04-01",
                "gender": "male",
                "school_class": str(school_class.pk),
                "guardian_name": "A Guardian",
                "guardian_phone_number": "08012345678",
                "guardian_relationship": "mother",
            },
        )

        assert created.status_code == 201
        assert created.data["student_id"] != student.student_id

    def test_the_fln_view_shows_levels_not_a_diagnosis(self, school_client, school, school_class):
        from apps.schools.models import StudentProfile

        student = StudentFactory(school=school, school_class=school_class)
        StudentProfile.objects.create(student=student, literacy_level=2, numeracy_level=4)

        response = school_client.get(reverse("v1:school_portal:student-fln", args=[student.pk]))

        assert response.status_code == 200
        assert response.data["literacy_level"] == 2
        assert response.data["numeracy_level"] == 4
        assert "recent_results" in response.data


class TestTransfers:
    def test_named_children_move_into_one_class(self, school_client, school, school_class):
        target = SchoolClassFactory(school=school, grade=school_class.grade, name="C")
        students = [StudentFactory(school=school, school_class=school_class) for _ in range(3)]

        response = school_client.post(
            reverse("v1:school_portal:student-transfer"),
            {
                "student_ids": [str(s.pk) for s in students],
                "to_class": str(target.pk),
            },
            format="json",
        )

        assert response.status_code == 200
        assert response.data["moved"] == 3
        assert Student.objects.filter(school_class=target).count() == 3

    def test_a_transfer_cannot_reach_another_school_s_child(
        self, school_client, school, school_class
    ):
        foreign = StudentFactory(school=SchoolFactory())

        response = school_client.post(
            reverse("v1:school_portal:student-transfer"),
            {"student_ids": [str(foreign.pk)], "to_class": str(school_class.pk)},
            format="json",
        )

        assert response.status_code == 400
        foreign.refresh_from_db()
        assert foreign.school_class_id != school_class.pk

    def test_a_transfer_cannot_target_another_school_s_class(
        self, school_client, school, school_class
    ):
        student = StudentFactory(school=school, school_class=school_class)
        foreign_class = SchoolClassFactory(school=SchoolFactory())

        response = school_client.post(
            reverse("v1:school_portal:student-transfer"),
            {"student_ids": [str(student.pk)], "to_class": str(foreign_class.pk)},
            format="json",
        )
        assert response.status_code == 400

    def test_a_whole_class_moves_at_once(self, school_client, school, school_class):
        target = SchoolClassFactory(school=school, grade=school_class.grade, name="D")
        for _ in range(4):
            StudentFactory(school=school, school_class=school_class)

        response = school_client.post(
            reverse("v1:school_portal:student-transfer-class"),
            {"from_class": str(school_class.pk), "to_class": str(target.pk)},
        )

        assert response.status_code == 200
        assert response.data["moved"] == 4
        assert not Student.objects.filter(school_class=school_class).exists()

    def test_a_whole_class_move_is_one_entry_in_the_feed(self, school_client, school, school_class):
        target = SchoolClassFactory(school=school, grade=school_class.grade, name="E")
        for _ in range(4):
            StudentFactory(school=school, school_class=school_class)

        school_client.post(
            reverse("v1:school_portal:student-transfer-class"),
            {"from_class": str(school_class.pk), "to_class": str(target.pk)},
        )

        entries = Activity.objects.filter(school=school, action=ActivityAction.STUDENT_TRANSFERRED)
        assert entries.count() == 1
        assert "4 students moved" in entries.first().description

    def test_moving_to_the_same_class_is_refused(self, school_client, school_class):
        response = school_client.post(
            reverse("v1:school_portal:student-transfer-class"),
            {"from_class": str(school_class.pk), "to_class": str(school_class.pk)},
        )
        assert response.status_code == 400


class TestActivityFeed:
    def test_the_feed_is_scoped_to_the_acting_school(self, school_client, school, school_class):
        StudentFactory(school=school, school_class=school_class)
        other = SchoolFactory()
        Activity.objects.create(
            school=other,
            action=ActivityAction.STUDENT_CREATED,
            label="Somebody else's business",
            occurred_at="2026-01-01T00:00:00Z",
        )
        Activity.objects.create(
            school=school,
            action=ActivityAction.STUDENT_CREATED,
            label="Ours",
            occurred_at="2026-01-01T00:00:00Z",
        )

        response = school_client.get(reverse("v1:school_portal:activity"))

        assert response.status_code == 200
        labels = [row["label"] for row in response.data["results"]]
        assert labels == ["Ours"]

    def test_the_feed_narrows_by_student(self, school_client, school, school_class):
        student = StudentFactory(school=school, school_class=school_class)
        Activity.objects.create(
            school=school,
            action=ActivityAction.STUDENT_PLACED,
            label="About this child",
            student=student,
            occurred_at="2026-01-02T00:00:00Z",
        )
        Activity.objects.create(
            school=school,
            action=ActivityAction.STUDENT_PLACED,
            label="About nobody in particular",
            occurred_at="2026-01-01T00:00:00Z",
        )

        response = school_client.get(
            reverse("v1:school_portal:activity"), {"student": str(student.pk)}
        )

        labels = [row["label"] for row in response.data["results"]]
        assert labels == ["About this child"]

    def test_an_entry_survives_the_row_it_describes_being_removed(
        self, school_client, school, school_class
    ):
        student = StudentFactory(school=school, school_class=school_class)
        school_client.post(reverse("v1:school_portal:student-delete-request", args=[student.pk]))
        school_client.post(
            reverse("v1:school_portal:student-delete-confirm", args=[student.pk]),
            {"code": emailed_code()},
        )

        response = school_client.get(reverse("v1:school_portal:activity"))
        labels = [row["label"] for row in response.data["results"]]

        assert any("Student removed" in label for label in labels)

    def test_a_bad_filter_is_a_four_hundred_rather_than_being_ignored(self, school_client):
        response = school_client.get(
            reverse("v1:school_portal:activity"), {"action": "not-an-action"}
        )
        assert response.status_code == 400


class TestOverview:
    def test_it_leads_with_level_distribution(self, school_client, school, school_class):
        from apps.schools.models import StudentProfile

        for literacy, numeracy in ((1, 3), (1, 4), (2, None)):
            student = StudentFactory(school=school, school_class=school_class)
            StudentProfile.objects.create(
                student=student, literacy_level=literacy, numeracy_level=numeracy
            )
        StudentFactory(school=school, school_class=school_class)  # never assessed

        response = school_client.get(reverse("v1:school_portal:overview"))

        assert response.status_code == 200
        levels = response.data["level_distribution"]["levels"]
        assert levels["literacy"]["1"] == 2
        assert levels["literacy"]["2"] == 1
        assert levels["numeracy"]["3"] == 1
        # Every level is keyed even at zero, so a chart cannot misread the
        # spread as narrower than it is.
        assert set(levels["literacy"]) == {"1", "2", "3", "4", "5"}

    def test_it_counts_the_children_nothing_has_reached_yet(
        self, school_client, school, school_class
    ):
        from apps.schools.models import StudentProfile

        placed = StudentFactory(school=school, school_class=school_class)
        StudentProfile.objects.create(student=placed, literacy_level=2, numeracy_level=None)
        StudentFactory(school=school, school_class=school_class)

        response = school_client.get(reverse("v1:school_portal:overview"))
        unplaced = response.data["level_distribution"]["unplaced"]

        assert unplaced["literacy"] == 1
        assert unplaced["numeracy"] == 2

    def test_another_school_s_children_are_not_counted(self, school_client, school, school_class):
        from apps.schools.models import StudentProfile

        foreign = StudentFactory(school=SchoolFactory())
        StudentProfile.objects.create(student=foreign, literacy_level=5, numeracy_level=5)

        response = school_client.get(reverse("v1:school_portal:overview"))

        assert response.data["level_distribution"]["levels"]["literacy"]["5"] == 0
