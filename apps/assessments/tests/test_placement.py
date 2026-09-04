"""The rules that decide where a child is placed.

Every case here is deterministic on purpose. A placement has to be
reproducible and explainable, so if any of these needed a model call to pass,
the design would be wrong.
"""

import pytest

from apps.assessments.enums import AssessmentStatus, CellOutcome, GradedBy, ResultStatus
from apps.assessments.models import (
    Assessment,
    AssessmentQuestion,
    AssessmentQuestionAnswer,
    AssessmentQuestionOption,
    AssessmentQuestionResponse,
    AssessmentQuestionResponseOption,
    AssessmentResult,
    AssessmentSection,
    Placement,
    PlacementRule,
    SkillLevelResult,
)
from apps.assessments.placement import (
    DiagnosisService,
    MarkingService,
    MatrixService,
    PlacementService,
)
from apps.common.enums import Domain, QuestionType, SkillStateStatus
from apps.curriculum.factories import SkillFactory, SubskillFactory
from apps.schools.factories import SchoolFactory, StudentFactory, TeacherFactory
from apps.schools.models import StudentProfile, StudentSkillState

pytestmark = pytest.mark.django_db


@pytest.fixture
def school():
    return SchoolFactory()


@pytest.fixture
def teacher(school):
    return TeacherFactory(school=school)


@pytest.fixture
def student(school):
    return StudentFactory(school=school)


@pytest.fixture
def paper(school, teacher):
    assessment = Assessment.objects.create(
        school=school,
        teacher=teacher,
        name="Baseline",
        status=AssessmentStatus.PUBLISHED,
        code="TESTAA",
    )
    return assessment


@pytest.fixture
def result(paper, student):
    return AssessmentResult.objects.create(
        assessment=paper, student=student, status=ResultStatus.FINISHED
    )


def make_skill(domain, code, low=1, high=5, core=True):
    return SkillFactory(domain=domain, code=code, min_level=low, max_level=high, is_core=core)


def rule(domain, level, required, applicable=None):
    PlacementRule.objects.update_or_create(
        domain=domain,
        fln_level=level,
        defaults={
            "required_skills": required,
            "applicable_skills": applicable or required,
        },
    )


def answer(paper, student, subskill, level, *, correct: bool, marked=True, with_options=False):
    """One marked response, so the matrix has something to read.

    `with_options` builds a real answer key too, for the tests that exercise
    marking rather than assuming it.
    """
    section, _ = AssessmentSection.objects.get_or_create(
        assessment=paper,
        order=1,
        defaults={"domain": subskill.skill.domain, "name": "Section"},
    )
    order = AssessmentQuestion.objects.filter(section=section).count() + 1
    question = AssessmentQuestion.objects.create(
        section=section,
        assessment=paper,
        subskill=subskill,
        skill=subskill.skill,
        fln_level=level,
        text=f"Q{order}",
        question_type=QuestionType.SINGLE_CHOICE,
        order=order,
        point=1,
    )
    response = AssessmentQuestionResponse.objects.create(
        assessment_question=question,
        student=student,
        assessment=paper,
        type=QuestionType.SINGLE_CHOICE,
        is_correct=correct if marked else None,
        graded_by=GradedBy.AUTO if marked else "",
    )
    if with_options:
        right = AssessmentQuestionOption.objects.create(
            assessment_question=question, type="text", value="right", is_correct=True
        )
        wrong = AssessmentQuestionOption.objects.create(
            assessment_question=question, type="text", value="wrong", is_correct=False
        )
        AssessmentQuestionResponseOption.objects.create(
            assessment_question_response=response,
            assessment_question_option=right if correct else wrong,
        )
    return question


class TestCellThreshold:
    """70% of a subskill's items at a level."""

    def test_seventy_percent_passes(self, paper, student):
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "cell_a"))
        for correct in (True, True, True, True, True, True, True, False, False, False):
            answer(paper, student, sub, 1, correct=correct)

        MatrixService().build(paper, student)
        cell = SkillLevelResult.objects.get(student=student, subskill=sub, fln_level=1)
        assert cell.items_attempted == 10
        assert cell.items_correct == 7
        assert cell.outcome == CellOutcome.PASS

    def test_just_below_fails(self, paper, student):
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "cell_b"))
        for correct in (True, True, False):
            answer(paper, student, sub, 1, correct=correct)

        MatrixService().build(paper, student)
        assert SkillLevelResult.objects.get(student=student).outcome == CellOutcome.FAIL

    def test_unmarked_responses_are_ignored(self, paper, student):
        """Pending is not wrong. A half-marked paper gives a partial diagnosis."""
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "cell_c"))
        answer(paper, student, sub, 1, correct=True)
        answer(paper, student, sub, 1, correct=False, marked=False)

        MatrixService().build(paper, student)
        cell = SkillLevelResult.objects.get(student=student)
        assert cell.items_attempted == 1
        assert cell.outcome == CellOutcome.PASS


class TestSkillThreshold:
    """80% of the subskills probed for a skill at a level."""

    def test_four_of_five_passes(self, paper, student):
        skill = make_skill(Domain.LITERACY, "skill_a", 1, 1)
        rule(Domain.LITERACY, 1, required=1)
        subs = [SubskillFactory(skill=skill, code=f"s_a{i}") for i in range(5)]
        for index, sub in enumerate(subs):
            answer(paper, student, sub, 1, correct=index < 4)

        MatrixService().build(paper, student)
        placement = PlacementService().place(paper, student)[0]
        assert placement.level == 1  # passed level 1, no higher level probed

    def test_three_of_five_fails(self, paper, student):
        skill = make_skill(Domain.LITERACY, "skill_b", 1, 1)
        rule(Domain.LITERACY, 1, required=1)
        subs = [SubskillFactory(skill=skill, code=f"s_b{i}") for i in range(5)]
        for index, sub in enumerate(subs):
            answer(paper, student, sub, 1, correct=index < 3)

        MatrixService().build(paper, student)
        placement = PlacementService().place(paper, student)[0]
        assert placement.level == 1  # level 1 not passed, so it is what to teach


class TestLevelThreshold:
    def test_uses_the_configured_rule(self, paper, student):
        rule(Domain.LITERACY, 1, required=2, applicable=3)
        skills = [make_skill(Domain.LITERACY, f"lvl_{i}", 1, 1) for i in range(3)]
        subs = [SubskillFactory(skill=s, code=f"lvl_s{i}") for i, s in enumerate(skills)]
        for index, sub in enumerate(subs):
            answer(paper, student, sub, 1, correct=index < 2)

        MatrixService().build(paper, student)
        # Two of three passed and two were required, so level 1 holds.
        assert PlacementService().place(paper, student)[0].level == 1

    def test_a_tighter_rule_refuses_the_same_evidence(self, paper, student):
        rule(Domain.LITERACY, 1, required=3, applicable=3)
        rule(Domain.LITERACY, 2, required=1, applicable=3)
        skills = [make_skill(Domain.LITERACY, f"tight_{i}", 1, 2) for i in range(3)]
        subs = [SubskillFactory(skill=s, code=f"tight_s{i}") for i, s in enumerate(skills)]
        for index, sub in enumerate(subs):
            answer(paper, student, sub, 1, correct=index < 2)
            answer(paper, student, sub, 2, correct=True)

        MatrixService().build(paper, student)
        placement = PlacementService().place(paper, student)[0]
        # Level 1 now needs all three. It is the lowest they did not pass.
        assert placement.level == 1

    def test_enrichment_skills_never_gate_a_level(self, paper, student):
        rule(Domain.LITERACY, 1, required=1, applicable=1)
        core = make_skill(Domain.LITERACY, "core_only", 1, 1)
        extra = make_skill(Domain.LITERACY, "enrichment", 1, 1, core=False)
        answer(paper, student, SubskillFactory(skill=core, code="core_s"), 1, correct=True)
        answer(paper, student, SubskillFactory(skill=extra, code="extra_s"), 1, correct=False)

        MatrixService().build(paper, student)
        assert PlacementService().place(paper, student)[0].level == 1


class TestPlacement:
    def test_the_lowest_unpassed_level_is_what_to_teach(self, paper, student):
        for level in (1, 2, 3):
            rule(Domain.LITERACY, level, required=1, applicable=1)
        skill = make_skill(Domain.LITERACY, "ladder", 1, 3)
        sub = SubskillFactory(skill=skill, code="ladder_s")
        answer(paper, student, sub, 1, correct=True)
        answer(paper, student, sub, 2, correct=True)
        answer(paper, student, sub, 3, correct=False)

        MatrixService().build(paper, student)
        assert PlacementService().place(paper, student)[0].level == 3

    def test_passing_four_while_failing_three_places_at_three(self, paper, student):
        """Children do not fail tidily. The rule handles it without special-casing."""
        for level in (1, 2, 3, 4):
            rule(Domain.LITERACY, level, required=1, applicable=1)
        skill = make_skill(Domain.LITERACY, "nonmono", 1, 4)
        sub = SubskillFactory(skill=skill, code="nonmono_s")
        answer(paper, student, sub, 1, correct=True)
        answer(paper, student, sub, 2, correct=True)
        answer(paper, student, sub, 3, correct=False)
        answer(paper, student, sub, 4, correct=True)

        MatrixService().build(paper, student)
        placement = PlacementService().place(paper, student)[0]
        assert placement.level == 3
        # The level-4 pass is kept as evidence but confers nothing.
        assert (
            SkillLevelResult.objects.get(student=student, fln_level=4).outcome == CellOutcome.PASS
        )

    def test_only_probed_levels_count(self, paper, student):
        """A paper covering 3 to 5 says nothing about 1 and 2.

        Treating silence as failure would place every child who sat a
        levels-3-to-5 paper at the bottom, which is the scenario a teacher
        building a narrow section creates on purpose.
        """
        for level in (3, 4, 5):
            rule(Domain.LITERACY, level, required=1, applicable=1)
        skill = make_skill(Domain.LITERACY, "narrow", 3, 5)
        sub = SubskillFactory(skill=skill, code="narrow_s")
        answer(paper, student, sub, 3, correct=True)
        answer(paper, student, sub, 4, correct=False)

        MatrixService().build(paper, student)
        placement = PlacementService().place(paper, student)[0]
        assert placement.level == 4
        assert placement.levels_probed == [3, 4]

    def test_passing_everything_probed_places_at_the_ceiling_reached(self, paper, student):
        for level in (1, 2):
            rule(Domain.LITERACY, level, required=1, applicable=1)
        skill = make_skill(Domain.LITERACY, "topped", 1, 2)
        sub = SubskillFactory(skill=skill, code="topped_s")
        answer(paper, student, sub, 1, correct=True)
        answer(paper, student, sub, 2, correct=True)

        MatrixService().build(paper, student)
        # The paper found no ceiling; the highest it reached is the best answer.
        assert PlacementService().place(paper, student)[0].level == 2

    def test_a_domain_with_no_items_gets_no_placement(self, paper, student):
        rule(Domain.LITERACY, 1, required=1, applicable=1)
        skill = make_skill(Domain.LITERACY, "lit_only", 1, 1)
        answer(paper, student, SubskillFactory(skill=skill, code="lit_only_s"), 1, correct=True)

        MatrixService().build(paper, student)
        placements = PlacementService().place(paper, student)
        assert [p.domain for p in placements] == [Domain.LITERACY]

    def test_the_two_domains_are_placed_independently(self, paper, student):
        for level in (1, 2):
            rule(Domain.LITERACY, level, required=1, applicable=1)
            rule(Domain.NUMERACY, level, required=1, applicable=1)
        lit = SubskillFactory(skill=make_skill(Domain.LITERACY, "ind_lit", 1, 2), code="il")
        num = SubskillFactory(skill=make_skill(Domain.NUMERACY, "ind_num", 1, 2), code="in")
        answer(paper, student, lit, 1, correct=False)
        answer(paper, student, num, 1, correct=True)
        answer(paper, student, num, 2, correct=True)

        MatrixService().build(paper, student)
        PlacementService().place(paper, student)

        profile = StudentProfile.objects.get(student=student)
        assert profile.literacy_level == 1
        assert profile.numeracy_level == 2


class TestAbsolutePlacement:
    def test_a_later_assessment_can_lower_a_level(self, school, teacher, student):
        """No promotion ladder. The newest reading replaces the last, downward."""
        rule(Domain.LITERACY, 1, required=1, applicable=1)
        rule(Domain.LITERACY, 2, required=1, applicable=1)
        skill = make_skill(Domain.LITERACY, "absolute", 1, 2)
        sub = SubskillFactory(skill=skill, code="absolute_s")

        strong = Assessment.objects.create(
            school=school,
            teacher=teacher,
            name="March",
            status=AssessmentStatus.PUBLISHED,
            code="ABSAAA",
        )
        AssessmentResult.objects.create(assessment=strong, student=student)
        answer(strong, student, sub, 1, correct=True)
        answer(strong, student, sub, 2, correct=True)
        MatrixService().build(strong, student)
        PlacementService().place(strong, student)
        assert StudentProfile.objects.get(student=student).literacy_level == 2

        weak = Assessment.objects.create(
            school=school,
            teacher=teacher,
            name="June",
            status=AssessmentStatus.PUBLISHED,
            code="ABSBBB",
        )
        AssessmentResult.objects.create(assessment=weak, student=student)
        answer(weak, student, sub, 1, correct=False)
        MatrixService().build(weak, student)
        PlacementService().place(weak, student)

        assert StudentProfile.objects.get(student=student).literacy_level == 1


class TestRerunnable:
    def test_rebuilding_the_matrix_does_not_duplicate(self, paper, student):
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "rerun"), code="rerun_s")
        answer(paper, student, sub, 1, correct=True)

        MatrixService().build(paper, student)
        MatrixService().build(paper, student)
        assert SkillLevelResult.objects.filter(student=student).count() == 1

    def test_placement_is_upserted_not_appended(self, paper, student):
        rule(Domain.LITERACY, 1, required=1, applicable=1)
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "upsert", 1, 1), code="upsert_s")
        answer(paper, student, sub, 1, correct=True)

        MatrixService().build(paper, student)
        PlacementService().place(paper, student)
        PlacementService().place(paper, student)
        assert Placement.objects.filter(student=student, assessment=paper).count() == 1

    def test_changing_a_threshold_and_re_placing_moves_the_child(self, paper, student):
        """Re-running after a tuning change is the intended way to apply one."""
        rule(Domain.LITERACY, 1, required=1, applicable=2)
        rule(Domain.LITERACY, 2, required=1, applicable=2)
        a = make_skill(Domain.LITERACY, "tune_a", 1, 2)
        b = make_skill(Domain.LITERACY, "tune_b", 1, 2)
        for level in (1, 2):
            answer(paper, student, SubskillFactory(skill=a, code=f"ta{level}"), level, correct=True)
            answer(
                paper, student, SubskillFactory(skill=b, code=f"tb{level}"), level, correct=False
            )

        MatrixService().build(paper, student)
        assert PlacementService().place(paper, student)[0].level == 2

        rule(Domain.LITERACY, 1, required=2, applicable=2)
        assert PlacementService().place(paper, student)[0].level == 1


class TestSkillStates:
    def test_mastery_needs_more_than_one_good_day(self, school, teacher, student):
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "mastery"), code="mastery_s")
        rule(Domain.LITERACY, 1, required=1, applicable=1)

        for name, code in (("One", "MASAAA"), ("Two", "MASBBB")):
            paper = Assessment.objects.create(
                school=school,
                teacher=teacher,
                name=name,
                status=AssessmentStatus.PUBLISHED,
                code=code,
            )
            AssessmentResult.objects.create(assessment=paper, student=student)
            answer(paper, student, sub, 1, correct=True)
            MatrixService().build(paper, student)
            PlacementService().place(paper, student)

            state = StudentSkillState.objects.get(student=student, subskill=sub)
            if name == "One":
                assert state.status == SkillStateStatus.DEVELOPING
                assert state.evidence_count == 1

        state = StudentSkillState.objects.get(student=student, subskill=sub)
        assert state.status == SkillStateStatus.MASTERED
        assert state.evidence_count == 2

    def test_a_failed_cell_marks_the_subskill_weak(self, paper, student):
        rule(Domain.LITERACY, 1, required=1, applicable=1)
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "weak"), code="weak_s")
        answer(paper, student, sub, 1, correct=False)

        MatrixService().build(paper, student)
        PlacementService().place(paper, student)
        assert (
            StudentSkillState.objects.get(student=student, subskill=sub).status
            == SkillStateStatus.WEAK
        )


class TestDiagnosisRollUp:
    def test_it_marks_places_and_grades_in_one_pass(self, paper, student, result):
        rule(Domain.LITERACY, 1, required=1, applicable=1)
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "roll", 1, 1), code="roll_s")
        answer(paper, student, sub, 1, correct=True, with_options=True)
        answer(paper, student, sub, 1, correct=False, with_options=True)

        placements = DiagnosisService().run(paper, student)
        assert len(placements) == 1

        result.refresh_from_db()
        assert result.status == ResultStatus.GRADED
        assert result.items_attempted == 2
        assert result.items_correct == 1
        assert result.percentage == pytest.approx(50, abs=0.01)
        assert result.marked_at is not None


class TestMarking:
    def test_a_single_choice_answer_is_marked(self, paper, student):
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "mark_a"), code="mark_a_s")
        question = answer(paper, student, sub, 1, correct=True, with_options=True, marked=False)

        MarkingService().mark(paper, student)
        response = AssessmentQuestionResponse.objects.get(assessment_question=question)
        assert response.is_correct is True
        assert response.graded_by == GradedBy.AUTO
        assert response.awarded_points == question.point

    def test_a_wrong_choice_scores_nothing(self, paper, student):
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "mark_b"), code="mark_b_s")
        question = answer(paper, student, sub, 1, correct=False, with_options=True, marked=False)

        MarkingService().mark(paper, student)
        response = AssessmentQuestionResponse.objects.get(assessment_question=question)
        assert response.is_correct is False
        assert response.awarded_points == 0

    def test_a_question_with_no_answer_key_is_left_pending(self, paper, student):
        """An authoring fault must not land in a child's diagnosis."""
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "mark_c"), code="mark_c_s")
        question = answer(paper, student, sub, 1, correct=True, marked=False)

        MarkingService().mark(paper, student)
        response = AssessmentQuestionResponse.objects.get(assessment_question=question)
        assert response.is_correct is None

    def test_free_text_is_left_for_the_ai_marker(self, paper, student):
        sub = SubskillFactory(skill=make_skill(Domain.LITERACY, "mark_d"), code="mark_d_s")
        section = AssessmentSection.objects.create(
            assessment=paper, domain=Domain.LITERACY, name="Written", order=9
        )
        question = AssessmentQuestion.objects.create(
            section=section,
            assessment=paper,
            subskill=sub,
            skill=sub.skill,
            fln_level=1,
            text="Write the word",
            question_type=QuestionType.TEXT,
            order=1,
            point=1,
        )
        AssessmentQuestionResponse.objects.create(
            assessment_question=question,
            student=student,
            assessment=paper,
            type=QuestionType.TEXT,
            text_value="cat",
        )

        marked = MarkingService().mark(paper, student)
        assert marked == 0
        assert (
            AssessmentQuestionResponse.objects.get(assessment_question=question).is_correct is None
        )

    def test_a_number_answer_is_compared_numerically(self, paper, student):
        sub = SubskillFactory(skill=make_skill(Domain.NUMERACY, "mark_n"), code="mark_n_s")
        section = AssessmentSection.objects.create(
            assessment=paper, domain=Domain.NUMERACY, name="Sums", order=8
        )
        question = AssessmentQuestion.objects.create(
            section=section,
            assessment=paper,
            subskill=sub,
            skill=sub.skill,
            fln_level=1,
            text="15 + 7",
            question_type=QuestionType.NUMBER,
            order=1,
            point=1,
        )
        AssessmentQuestionAnswer.objects.create(assessment_question=question, value="22")
        AssessmentQuestionResponse.objects.create(
            assessment_question=question,
            student=student,
            assessment=paper,
            type=QuestionType.NUMBER,
            text_value=" 22 ",
        )

        MarkingService().mark(paper, student)
        assert (
            AssessmentQuestionResponse.objects.get(assessment_question=question).is_correct is True
        )

    def test_words_where_a_number_belongs_are_wrong_not_an_error(self, paper, student):
        sub = SubskillFactory(skill=make_skill(Domain.NUMERACY, "mark_w"), code="mark_w_s")
        section = AssessmentSection.objects.create(
            assessment=paper, domain=Domain.NUMERACY, name="Sums", order=7
        )
        question = AssessmentQuestion.objects.create(
            section=section,
            assessment=paper,
            subskill=sub,
            skill=sub.skill,
            fln_level=1,
            text="15 + 7",
            question_type=QuestionType.NUMBER,
            order=1,
            point=1,
        )
        AssessmentQuestionAnswer.objects.create(assessment_question=question, value="22")
        AssessmentQuestionResponse.objects.create(
            assessment_question=question,
            student=student,
            assessment=paper,
            type=QuestionType.NUMBER,
            text_value="twenty two",
        )

        MarkingService().mark(paper, student)
        assert (
            AssessmentQuestionResponse.objects.get(assessment_question=question).is_correct is False
        )
