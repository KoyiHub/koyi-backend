"""The rules that decide whether a paper can place a child."""

import pytest
from django.utils import timezone

from apps.assessments.dto import (
    CreateAssessmentInput,
    CreateSectionInput,
    OptionInput,
    QuestionInput,
)
from apps.assessments.enums import AssessmentStatus
from apps.assessments.services import (
    AssessmentCoverageService,
    AssessmentDraftService,
    AssessmentPublishService,
)
from apps.common.enums import Domain, QuestionLayout, QuestionType
from apps.common.services import ValidationError
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.schools.factories import SchoolFactory, TeacherFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def school():
    return SchoolFactory()


@pytest.fixture
def teacher(school):
    return TeacherFactory(school=school)


@pytest.fixture
def drafts(school, teacher):
    return AssessmentDraftService(school, teacher)


@pytest.fixture
def literacy_subskill():
    """Phonics-like: only assessed at levels 1 to 3."""
    skill = SkillFactory(domain=Domain.LITERACY, min_level=1, max_level=3, code="phonics")
    return SubskillFactory(skill=skill, code="letter_sounds", name="Letter sounds")


@pytest.fixture
def numeracy_subskill():
    skill = SkillFactory(domain=Domain.NUMERACY, min_level=1, max_level=2, code="counting")
    return SubskillFactory(skill=skill, code="numeral_recognition", name="Numeral recognition")


def choice_question(subskill, level=1, **overrides):
    defaults = {
        "subskill_id": subskill.pk,
        "fln_level": level,
        "question_type": QuestionType.SINGLE_CHOICE,
        "text": "Which letter makes this sound?",
        "options": (
            OptionInput(type="text", value="B", is_correct=True),
            OptionInput(type="text", value="D"),
        ),
    }
    return QuestionInput(**{**defaults, **overrides})


@pytest.fixture
def draft(drafts):
    return drafts.create(CreateAssessmentInput(name="Term baseline"))


@pytest.fixture
def section(drafts, draft):
    return drafts.add_section(draft, CreateSectionInput(domain=Domain.LITERACY, name="Reading"))


class TestLevelRange:
    """The range is the main defence against a mis-tagged item."""

    def test_rejects_a_level_the_subskill_is_never_assessed_at(
        self, drafts, section, literacy_subskill
    ):
        with pytest.raises(ValidationError, match="only assessed at levels 1 to 3"):
            drafts.set_questions(section, [choice_question(literacy_subskill, level=5)])

    def test_accepts_a_level_inside_the_range(self, drafts, section, literacy_subskill):
        questions = drafts.set_questions(section, [choice_question(literacy_subskill, level=3)])
        assert questions[0].fln_level == 3

    def test_a_narrower_subskill_range_wins_over_its_skill(self, drafts, section):
        skill = SkillFactory(domain=Domain.LITERACY, min_level=1, max_level=3, code="alphabetic")
        subskill = SubskillFactory(skill=skill, code="letter_naming", max_level=1)
        with pytest.raises(ValidationError, match="levels 1 to 1"):
            drafts.set_questions(section, [choice_question(subskill, level=2)])


class TestDomainAgreement:
    def test_rejects_a_subskill_from_the_other_domain(self, drafts, section, numeracy_subskill):
        with pytest.raises(ValidationError, match="is a numeracy subskill"):
            drafts.set_questions(section, [choice_question(numeracy_subskill)])


class TestLayoutRules:
    """A layout that disagrees with its item breaks in front of a child."""

    def test_speech_prompt_cannot_carry_options(self, drafts, section, literacy_subskill):
        question = choice_question(literacy_subskill, layout=QuestionLayout.SPEECH_RESPONSE_PROMPT)
        with pytest.raises(ValidationError, match="cannot carry answer options"):
            drafts.set_questions(section, [question])

    def test_speech_prompt_expects_an_audio_question(self, drafts, section, literacy_subskill):
        question = QuestionInput(
            subskill_id=literacy_subskill.pk,
            fln_level=1,
            question_type=QuestionType.TEXT,
            text="Say the word",
            layout=QuestionLayout.SPEECH_RESPONSE_PROMPT,
        )
        with pytest.raises(ValidationError, match="expects an audio question type"):
            drafts.set_questions(section, [question])

    def test_comparison_panel_compares_two_or_three(self, drafts, section, literacy_subskill):
        question = choice_question(
            literacy_subskill,
            layout=QuestionLayout.COMPARISON_PANEL_CHOICE,
            options=(
                OptionInput(type="text", value="A", is_correct=True),
                OptionInput(type="text", value="B"),
                OptionInput(type="text", value="C"),
                OptionInput(type="text", value="D"),
            ),
        )
        with pytest.raises(ValidationError, match="two or three things"):
            drafts.set_questions(section, [question])

    def test_option_layout_rejects_a_non_option_question(self, drafts, section, literacy_subskill):
        question = QuestionInput(
            subskill_id=literacy_subskill.pk,
            fln_level=1,
            question_type=QuestionType.TEXT,
            text="Write the word",
            layout=QuestionLayout.MEDIA_GRID_CHOICE,
        )
        with pytest.raises(ValidationError, match="renders options"):
            drafts.set_questions(section, [question])


class TestSetQuestionsIsIdempotent:
    def test_posting_the_same_list_twice_does_not_duplicate(
        self, drafts, section, literacy_subskill
    ):
        payload = [choice_question(literacy_subskill), choice_question(literacy_subskill, level=2)]
        drafts.set_questions(section, payload)
        drafts.set_questions(section, payload)
        assert section.questions.count() == 2

    def test_questions_are_numbered_from_one(self, drafts, section, literacy_subskill):
        questions = drafts.set_questions(
            section, [choice_question(literacy_subskill) for _ in range(3)]
        )
        assert [q.order for q in questions] == [1, 2, 3]

    def test_assessment_is_denormalised_onto_each_question(
        self, drafts, section, literacy_subskill
    ):
        questions = drafts.set_questions(section, [choice_question(literacy_subskill)])
        assert questions[0].assessment_id == section.assessment_id
        assert questions[0].skill_id == literacy_subskill.skill_id


class TestPublish:
    def test_refuses_an_assessment_with_no_sections(self, school, teacher, draft):
        with pytest.raises(ValidationError, match="at least one section"):
            AssessmentPublishService(school, teacher).publish(draft)

    def test_refuses_a_section_with_no_questions(self, school, teacher, draft, section):
        with pytest.raises(ValidationError, match="no questions"):
            AssessmentPublishService(school, teacher).publish(draft)

    def test_refuses_a_closing_time_before_opening(
        self, school, teacher, drafts, draft, section, literacy_subskill
    ):
        drafts.set_questions(section, [choice_question(literacy_subskill)])
        now = timezone.now()
        draft.opens_at = now
        draft.closes_at = now - timezone.timedelta(days=1)
        draft.save(update_fields=["opens_at", "closes_at"])
        with pytest.raises(ValidationError, match="after the opening time"):
            AssessmentPublishService(school, teacher).publish(draft)

    def test_mints_a_code_and_locks_the_paper(
        self, school, teacher, drafts, draft, section, literacy_subskill
    ):
        drafts.set_questions(section, [choice_question(literacy_subskill)])
        published = AssessmentPublishService(school, teacher).publish(draft)

        assert published.status == AssessmentStatus.PUBLISHED
        assert len(published.code) == 6
        assert published.published_at is not None
        assert not published.is_editable
        assert published.is_sittable

    def test_code_avoids_ambiguous_characters(
        self, school, teacher, drafts, draft, section, literacy_subskill
    ):
        drafts.set_questions(section, [choice_question(literacy_subskill)])
        published = AssessmentPublishService(school, teacher).publish(draft)
        # A child reads this off a board; O/0 and I/1 would cost them a sitting.
        assert not set(published.code) & set("OI0125SZ")

    def test_a_published_paper_cannot_be_edited(
        self, school, teacher, drafts, draft, section, literacy_subskill
    ):
        drafts.set_questions(section, [choice_question(literacy_subskill)])
        AssessmentPublishService(school, teacher).publish(draft)
        draft.refresh_from_db()
        with pytest.raises(ValidationError, match="cannot be changed"):
            drafts.set_questions(section, [choice_question(literacy_subskill)])

    def test_publishing_twice_is_refused(
        self, school, teacher, drafts, draft, section, literacy_subskill
    ):
        drafts.set_questions(section, [choice_question(literacy_subskill)])
        service = AssessmentPublishService(school, teacher)
        service.publish(draft)
        draft.refresh_from_db()
        with pytest.raises(ValidationError, match="Only a draft"):
            service.publish(draft)


class TestCoverage:
    def test_warns_when_every_question_sits_at_one_level(
        self, drafts, draft, section, literacy_subskill
    ):
        drafts.set_questions(
            section, [choice_question(literacy_subskill), choice_question(literacy_subskill)]
        )
        coverage = AssessmentCoverageService(draft).build()

        assert coverage.levels_probed == (1,)
        assert any("cannot find where a child actually sits" in w for w in coverage.warnings)

    def test_reports_a_declared_subskill_with_no_questions(self, drafts, draft, literacy_subskill):
        other = SubskillFactory(skill=literacy_subskill.skill, code="blending", name="Blending")
        section = drafts.add_section(
            draft,
            CreateSectionInput(
                domain=Domain.LITERACY,
                name="Reading",
                covers=(literacy_subskill.pk, other.pk),
            ),
        )
        drafts.set_questions(section, [choice_question(literacy_subskill)])
        coverage = AssessmentCoverageService(draft).build()

        assert any("Blending" in w for w in coverage.warnings)

    def test_counts_items_per_cell_across_levels(self, drafts, draft, section, literacy_subskill):
        drafts.set_questions(
            section,
            [
                choice_question(literacy_subskill, level=1),
                choice_question(literacy_subskill, level=1),
                choice_question(literacy_subskill, level=3),
            ],
        )
        coverage = AssessmentCoverageService(draft).build()

        assert coverage.question_count == 3
        assert coverage.levels_probed == (1, 3)
        counts = {cell.fln_level: cell.item_count for cell in coverage.sections[0].cells}
        assert counts == {1: 2, 3: 1}
