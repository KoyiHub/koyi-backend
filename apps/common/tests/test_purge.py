"""The retention purge.

A delete in the dashboard hides the row; this is what eventually makes it true.
The two halves of that are what the tests here pin down: nothing inside the
window is touched, and nothing outside it survives.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.common.tasks import purge_deleted_rows_task
from apps.schools.factories import SchoolClassFactory, SchoolFactory, StudentFactory, TeacherFactory
from apps.schools.models import Student, Teacher
from apps.users.models import User

pytestmark = pytest.mark.django_db


def deleted_days_ago(instance, days: int) -> None:
    instance.delete()
    type(instance).all_objects.filter(pk=instance.pk).update(
        deleted_at=timezone.now() - timedelta(days=days)
    )


@pytest.fixture
def school():
    return SchoolFactory()


class TestPurge:
    def test_a_recent_deletion_is_left_alone(self, school, settings):
        student = StudentFactory(school=school, school_class=SchoolClassFactory(school=school))
        deleted_days_ago(student, settings.DELETED_ROW_RETENTION_DAYS - 1)

        purge_deleted_rows_task()

        assert Student.all_objects.filter(pk=student.pk).exists()

    def test_an_old_deletion_is_destroyed(self, school, settings):
        student = StudentFactory(school=school, school_class=SchoolClassFactory(school=school))
        deleted_days_ago(student, settings.DELETED_ROW_RETENTION_DAYS + 1)

        purged = purge_deleted_rows_task()

        assert purged["students"] == 1
        assert not Student.all_objects.filter(pk=student.pk).exists()

    def test_a_live_row_is_never_touched(self, school):
        student = StudentFactory(school=school, school_class=SchoolClassFactory(school=school))

        purge_deleted_rows_task()

        assert Student.objects.filter(pk=student.pk).exists()

    def test_purging_a_teacher_takes_their_login_with_them(self, school, settings):
        teacher = TeacherFactory(school=school)
        user_id = teacher.user_id
        deleted_days_ago(teacher, settings.DELETED_ROW_RETENTION_DAYS + 1)

        purge_deleted_rows_task()

        assert not Teacher.all_objects.filter(pk=teacher.pk).exists()
        # Nothing left to authenticate, and keeping it would hold their address
        # long after the record it belonged to is gone.
        assert not User.objects.filter(pk=user_id).exists()

    def test_a_purged_teacher_leaves_their_assessments_behind(self, school, settings):
        from apps.assessments.models import Assessment

        teacher = TeacherFactory(school=school)
        paper = Assessment.objects.create(school=school, teacher=teacher, name="Term 1 baseline")
        deleted_days_ago(teacher, settings.DELETED_ROW_RETENTION_DAYS + 1)

        purge_deleted_rows_task()

        paper.refresh_from_db()
        assert paper.teacher_id is None
        assert paper.name == "Term 1 baseline"

    def test_running_twice_is_harmless(self, school, settings):
        student = StudentFactory(school=school, school_class=SchoolClassFactory(school=school))
        deleted_days_ago(student, settings.DELETED_ROW_RETENTION_DAYS + 1)

        first = purge_deleted_rows_task()
        second = purge_deleted_rows_task()

        assert first["students"] == 1
        assert second["students"] == 0


class TestVisibility:
    def test_a_soft_deleted_child_is_gone_from_ordinary_queries(self, school):
        student = StudentFactory(school=school, school_class=SchoolClassFactory(school=school))
        student.delete()

        assert not Student.objects.filter(pk=student.pk).exists()
        assert Student.all_objects.filter(pk=student.pk).exists()

    def test_restoring_brings_them_back(self, school):
        student = StudentFactory(school=school, school_class=SchoolClassFactory(school=school))
        student.delete()

        Student.all_objects.get(pk=student.pk).restore()

        assert Student.objects.filter(pk=student.pk).exists()
