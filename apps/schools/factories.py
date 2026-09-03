"""Factories for schools, staff and learners."""

import factory
from factory.django import DjangoModelFactory

from apps.common.enums import UserRole
from apps.schools.models import AcademicSession, Grade, School, SchoolClass, Student, Teacher
from apps.users.factories import UserFactory


class SchoolUserFactory(UserFactory):
    role = UserRole.SCHOOL


class TeacherUserFactory(UserFactory):
    role = UserRole.TEACHER


class AcademicSessionFactory(DjangoModelFactory):
    class Meta:
        model = AcademicSession
        django_get_or_create = ["start_year", "end_year"]

    start_year = 2025
    end_year = 2026


class GradeFactory(DjangoModelFactory):
    class Meta:
        model = Grade
        django_get_or_create = ["name"]

    name = factory.Sequence(lambda n: f"Grade {n + 1}")


class SchoolFactory(DjangoModelFactory):
    class Meta:
        model = School

    user = factory.SubFactory(SchoolUserFactory)
    name = factory.Sequence(lambda n: f"Test School {n}")
    abbreviation = factory.Sequence(lambda n: f"TS{n:04d}")


class SchoolClassFactory(DjangoModelFactory):
    class Meta:
        model = SchoolClass

    school = factory.SubFactory(SchoolFactory)
    grade = factory.SubFactory(GradeFactory)
    name = "A"


class TeacherFactory(DjangoModelFactory):
    class Meta:
        model = Teacher

    user = factory.SubFactory(TeacherUserFactory)
    school = factory.SubFactory(SchoolFactory)
    teacher_id = factory.Sequence(lambda n: f"TS-T-{n:05d}")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")


class StudentFactory(DjangoModelFactory):
    class Meta:
        model = Student

    school = factory.SubFactory(SchoolFactory)
    school_class = factory.SubFactory(SchoolClassFactory)
    student_id = factory.Sequence(lambda n: f"TS-S-{n:05d}")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    date_of_birth = factory.Faker("date_of_birth", minimum_age=6, maximum_age=12)
    gender = "female"
    guardian_name = factory.Faker("name")
    guardian_phone_number = "08012345678"
    guardian_relationship = "mother"
