"""Factories for the taxonomy and question bank."""

import factory
from factory.django import DjangoModelFactory

from apps.common.enums import Domain, QuestionType
from apps.curriculum.models import Option, Question, Skill, Subskill


class SkillFactory(DjangoModelFactory):
    class Meta:
        model = Skill
        django_get_or_create = ["code"]

    code = factory.Sequence(lambda n: f"skill_{n}")
    name = factory.Sequence(lambda n: f"Skill {n}")
    domain = Domain.LITERACY
    min_level = 1
    max_level = 5
    is_core = True


class SubskillFactory(DjangoModelFactory):
    class Meta:
        model = Subskill
        django_get_or_create = ["code"]

    skill = factory.SubFactory(SkillFactory)
    code = factory.Sequence(lambda n: f"subskill_{n}")
    name = factory.Sequence(lambda n: f"Subskill {n}")


class QuestionFactory(DjangoModelFactory):
    class Meta:
        model = Question

    subskill = factory.SubFactory(SubskillFactory)
    fln_level = 1
    content = factory.Sequence(lambda n: f"What is question {n}?")
    type = QuestionType.SINGLE_CHOICE


class OptionFactory(DjangoModelFactory):
    class Meta:
        model = Option

    question = factory.SubFactory(QuestionFactory)
    option_type = "text"
    content = "An option"
    is_correct = False
