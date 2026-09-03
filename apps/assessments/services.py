"""Business rules for building an assessment.

The shape is draft-then-publish. While a paper is a draft the teacher adds and
edits sections and questions freely, one small request at a time, so nothing is
lost if they leave and come back. `publish` is the one-way door: it validates,
snapshots, mints the code and locks the paper, all in a single transaction,
because from that moment children may sit it and what they were asked must
stop moving.

Views call these; they never touch the ORM directly.
"""

import secrets
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.assessments.dto import (
    AssessmentCoverage,
    CoverageCell,
    CreateAssessmentInput,
    CreateSectionInput,
    QuestionInput,
    SectionCoverage,
    UpdateAssessmentInput,
    UpdateSectionInput,
)
from apps.assessments.enums import AssessmentStatus
from apps.assessments.models import (
    Assessment,
    AssessmentQuestion,
    AssessmentQuestionAnswer,
    AssessmentQuestionContent,
    AssessmentQuestionOption,
    AssessmentSection,
)
from apps.assessments.repositories import (
    AssessmentQuestionRepository,
    AssessmentRepository,
    SectionRepository,
    TaxonomyRepository,
)
from apps.common.enums import ActivityAction, QuestionLayout, QuestionType
from apps.common.services import BaseService, NotFoundError, ValidationError
from apps.media_assets.models import MediaAsset

#: Unambiguous in print and on a phone screen: no O/0, I/1, S/5, Z/2. A child
#: reads this off a board and types it, so a misread character is a failed
#: sitting rather than a typo they can shrug off.
CODE_ALPHABET = "ABCDEFGHJKLMNPQRTUVWXY346789"
CODE_LENGTH = 6
CODE_ATTEMPTS = 5


class AssessmentDraftService(BaseService):
    """Creates and mutates a draft paper."""

    def __init__(self, school, teacher=None) -> None:
        self.school = school
        self.teacher = teacher
        self.assessments = AssessmentRepository(school)
        self.taxonomy = TaxonomyRepository()

    # --- the paper --------------------------------------------------------

    def create(self, data: CreateAssessmentInput) -> Assessment:
        assessment = Assessment.objects.create(
            school=self.school,
            teacher=self.teacher,
            session_id=data.session_id,
            name=data.name,
            instructions=data.instructions,
            opens_at=data.opens_at,
            closes_at=data.closes_at,
            status=AssessmentStatus.DRAFT,
        )
        self._log(ActivityAction.ASSESSMENT_CREATED, assessment, f'Created "{assessment.name}"')
        return assessment

    def get(self, pk) -> Assessment:
        assessment = self.assessments.get_or_none(pk=pk)
        if assessment is None:
            raise NotFoundError("No such assessment in this school.")
        return assessment

    def update(self, assessment: Assessment, data: UpdateAssessmentInput) -> Assessment:
        self._require_editable(assessment)
        fields = {
            name: value
            for name, value in (
                ("name", data.name),
                ("instructions", data.instructions),
                ("session_id", data.session_id),
                ("opens_at", data.opens_at),
                ("closes_at", data.closes_at),
            )
            if value is not None
        }
        return self.assessments.update(assessment, **fields)

    def delete(self, assessment: Assessment) -> None:
        # A published paper may already have been sat; deleting it would take
        # the evidence a child's placement rests on with it.
        self._require_editable(assessment)
        assessment.delete()

    # --- sections ---------------------------------------------------------

    def add_section(self, assessment: Assessment, data: CreateSectionInput) -> AssessmentSection:
        self._require_editable(assessment)
        sections = SectionRepository(assessment)
        section = AssessmentSection.objects.create(
            assessment=assessment,
            domain=data.domain,
            name=data.name,
            instructions=data.instructions,
            timer=data.timer,
            order=sections.next_order(),
        )
        if data.covers:
            section.covers.set(self._resolve_subskills(data.covers, data.domain))
        return section

    def get_section(self, assessment: Assessment, pk) -> AssessmentSection:
        section = SectionRepository(assessment).get_or_none(pk=pk)
        if section is None:
            raise NotFoundError("No such section on this assessment.")
        return section

    def update_section(
        self, section: AssessmentSection, data: UpdateSectionInput
    ) -> AssessmentSection:
        self._require_editable(section.assessment)
        fields = {
            name: value
            for name, value in (
                ("name", data.name),
                ("instructions", data.instructions),
                ("timer", data.timer),
                ("order", data.order),
            )
            if value is not None
        }
        if fields:
            SectionRepository(section.assessment).update(section, **fields)
        if data.covers is not None:
            section.covers.set(self._resolve_subskills(data.covers, section.domain))
        return section

    def delete_section(self, section: AssessmentSection) -> None:
        self._require_editable(section.assessment)
        section.delete()

    # --- questions --------------------------------------------------------

    @transaction.atomic
    def set_questions(
        self, section: AssessmentSection, questions: list[QuestionInput]
    ) -> list[AssessmentQuestion]:
        """Replace the section's questions wholesale.

        Idempotent by design: the client owns the ordered list and posts it
        entire, so a retry after a dropped connection cannot leave a section
        holding two copies of the same item.
        """
        self._require_editable(section.assessment)
        section.questions.all().delete()
        return [
            self._create_question(section, data, order)
            for order, data in enumerate(questions, start=1)
        ]

    def _create_question(
        self, section: AssessmentSection, data: QuestionInput, order: int
    ) -> AssessmentQuestion:
        subskill = self.taxonomy.get_subskill(data.subskill_id)
        if subskill is None:
            raise ValidationError(
                "Unknown subskill.", detail={"subskill_id": [str(data.subskill_id)]}
            )
        self._check_level_range(subskill, data.fln_level)
        self._check_domain(section, subskill)
        self._check_layout(data)

        question = AssessmentQuestion.objects.create(
            section=section,
            assessment=section.assessment,
            source_question_id=data.source_question_id,
            subskill=subskill,
            skill=subskill.skill,
            fln_level=data.fln_level,
            text=data.text,
            description=data.description,
            question_type=data.question_type,
            layout=data.layout,
            point=data.point or Decimal("1"),
            order=order,
        )
        # Resolved once for the whole question: contents, options and the
        # answer can all point at the same asset, and each lookup is a query.
        media = self._media_by_id(
            [block.media_id for block in data.contents]
            + [option.media_id for option in data.options]
            + [data.answer.media_id if data.answer else None]
        )
        self._create_contents(question, data, media)
        self._create_options(question, data, media)
        self._create_answer(question, data, media)
        return question

    def _create_contents(
        self, question: AssessmentQuestion, data: QuestionInput, media: dict
    ) -> None:
        AssessmentQuestionContent.objects.bulk_create(
            AssessmentQuestionContent(
                assessment_question=question,
                type=block.type,
                display_order=block.display_order,
                text_content=block.text_content,
                media=media.get(block.media_id),
                alt_text=block.alt_text,
                caption=block.caption,
            )
            for block in data.contents
        )

    def _create_options(
        self, question: AssessmentQuestion, data: QuestionInput, media: dict
    ) -> None:
        AssessmentQuestionOption.objects.bulk_create(
            AssessmentQuestionOption(
                assessment_question=question,
                type=option.type,
                value=option.value,
                media=media.get(option.media_id),
                is_correct=option.is_correct,
            )
            for option in data.options
        )

    def _create_answer(
        self, question: AssessmentQuestion, data: QuestionInput, media: dict
    ) -> None:
        if data.answer is None:
            return
        AssessmentQuestionAnswer.objects.create(
            assessment_question=question,
            value=data.answer.value,
            media=media.get(data.answer.media_id),
        )

    # --- validation helpers ----------------------------------------------

    def _check_level_range(self, subskill, level: int) -> None:
        """The level range is enforced, not advisory.

        This is the main defence against mis-tagging: an item tagged to a level
        its subskill is never assessed at would feed placement silently and
        pull a child to the wrong level.
        """
        if not subskill.covers_level(level):
            low, high = subskill.level_range
            raise ValidationError(
                f'"{subskill.name}" is only assessed at levels {low} to {high}.',
                detail={"fln_level": [f"Outside the range for {subskill.code}."]},
            )

    def _check_domain(self, section: AssessmentSection, subskill) -> None:
        if subskill.skill.domain != section.domain:
            raise ValidationError(
                f'"{subskill.name}" is a {subskill.skill.domain} subskill, '
                f"but this section is {section.domain}.",
                detail={"subskill_id": ["Wrong domain for this section."]},
            )

    def _check_layout(self, data: QuestionInput) -> None:
        """Layouts imply constraints on the item; publish is too late to find out.

        A speech prompt carrying four options, or a comparison panel with
        seven, is an authoring slip that would otherwise surface as a broken
        screen in front of a child mid-sitting.
        """
        if not data.layout:
            return
        option_based = data.question_type in QuestionType.option_based()
        if data.layout == QuestionLayout.SPEECH_RESPONSE_PROMPT:
            if data.options:
                raise ValidationError(
                    "A speech prompt cannot carry answer options.",
                    detail={"options": ["Remove the options or change the layout."]},
                )
            if data.question_type != QuestionType.AUDIO:
                raise ValidationError(
                    "A speech prompt expects an audio question type.",
                    detail={"question_type": ["Use 'audio' with this layout."]},
                )
            return
        if data.layout in QuestionLayout.option_layouts() and not option_based:
            raise ValidationError(
                f"The {data.layout} layout renders options, "
                f"but {data.question_type} questions have none.",
                detail={"layout": ["Layout and question type disagree."]},
            )
        if (
            data.layout == QuestionLayout.COMPARISON_PANEL_CHOICE
            and not 2 <= len(data.options) <= 3
        ):
            raise ValidationError(
                "A comparison panel compares two or three things.",
                detail={"options": [f"Got {len(data.options)}."]},
            )

    def _resolve_subskills(self, ids, domain: str):
        found = self.taxonomy.subskills_by_id(ids)
        missing = set(ids) - set(found)
        if missing:
            raise ValidationError(
                "Unknown subskill.", detail={"covers": [str(pk) for pk in missing]}
            )
        wrong_domain = [s.name for s in found.values() if s.skill.domain != domain]
        if wrong_domain:
            raise ValidationError(
                f"These are not {domain} subskills: {', '.join(wrong_domain)}.",
                detail={"covers": wrong_domain},
            )
        return list(found.values())

    def _media_by_id(self, ids) -> dict:
        wanted = {pk for pk in ids if pk}
        if not wanted:
            return {}
        found = {asset.pk: asset for asset in MediaAsset.objects.filter(pk__in=wanted)}
        missing = wanted - set(found)
        if missing:
            raise ValidationError(
                "Unknown media asset.", detail={"media_id": [str(pk) for pk in missing]}
            )
        return found

    def _require_editable(self, assessment: Assessment) -> None:
        if not assessment.is_editable:
            raise ValidationError(
                "A published assessment cannot be changed.",
                detail={"status": [f"This assessment is {assessment.status}."]},
            )

    def _log(self, action: str, assessment: Assessment, label: str) -> None:
        from apps.common.services import ActivityService

        ActivityService().record(
            school=self.school,
            action=action,
            label=label,
            teacher=self.teacher,
            assessment=assessment,
            actor_user=self.teacher.user if self.teacher else None,
        )


class AssessmentCoverageService(BaseService):
    """What a paper can actually establish about a child.

    Placement reads a (skill x level) matrix, so a paper that probes only one
    level cannot place anyone no matter how many questions it carries. This
    turns that into something the teacher sees while authoring rather than
    something discovered after thirty children have sat it.
    """

    def __init__(self, assessment: Assessment) -> None:
        self.assessment = assessment

    def build(self) -> AssessmentCoverage:
        sections: list[SectionCoverage] = []
        levels: set[int] = set()
        domains: list[str] = []
        total = 0

        for section in self.assessment.sections.prefetch_related("covers").all():
            rows = AssessmentQuestionRepository(section).coverage_rows()
            cells = tuple(
                CoverageCell(
                    subskill_id=row["subskill_id"],
                    subskill_name=row["subskill__name"],
                    skill_id=row["skill_id"],
                    skill_name=row["skill__name"],
                    domain=section.domain,
                    fln_level=row["fln_level"],
                    item_count=row["item_count"],
                )
                for row in rows
            )
            probed = {cell.subskill_id for cell in cells}
            gaps = tuple(
                subskill.name for subskill in section.covers.all() if subskill.pk not in probed
            )
            count = sum(cell.item_count for cell in cells)
            total += count
            levels.update(cell.fln_level for cell in cells)
            if section.domain not in domains:
                domains.append(section.domain)
            sections.append(
                SectionCoverage(
                    section_id=section.pk,
                    section_name=section.name,
                    domain=section.domain,
                    question_count=count,
                    cells=cells,
                    gaps=gaps,
                )
            )

        return AssessmentCoverage(
            assessment_id=self.assessment.pk,
            question_count=total,
            sections=tuple(sections),
            domains=tuple(domains),
            levels_probed=tuple(sorted(levels)),
            warnings=self._warnings(sections, sorted(levels), total),
        )

    def _warnings(
        self, sections: list[SectionCoverage], levels: list[int], total: int
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        if total == 0:
            warnings.append("This assessment has no questions.")
        empty = [s.section_name for s in sections if s.question_count == 0]
        if empty:
            warnings.append(f"No questions in: {', '.join(empty)}.")
        if len(levels) == 1:
            warnings.append(
                f"Every question is at level {levels[0]}. A paper that probes one "
                "level can confirm it but cannot find where a child actually sits."
            )
        for section in sections:
            if section.gaps:
                warnings.append(
                    f'"{section.section_name}" declares it covers '
                    f"{', '.join(section.gaps)} but has no questions for them."
                )
        return tuple(warnings)


class AssessmentPublishService(BaseService):
    """The one-way door from draft to sittable.

    Everything happens in one transaction: validate, mint the code, stamp the
    status. After this the paper is immutable, because children may start
    sitting it the moment the code is out.
    """

    def __init__(self, school, teacher=None) -> None:
        self.school = school
        self.teacher = teacher
        self.assessments = AssessmentRepository(school)

    @transaction.atomic
    def publish(self, assessment: Assessment) -> Assessment:
        if assessment.status != AssessmentStatus.DRAFT:
            raise ValidationError(
                "Only a draft can be published.",
                detail={"status": [f"This assessment is already {assessment.status}."]},
            )
        self._validate(assessment)

        assessment.code = self._mint_code()
        assessment.status = AssessmentStatus.PUBLISHED
        assessment.published_at = timezone.now()
        assessment.save(update_fields=["code", "status", "published_at", "updated_at"])

        from apps.common.services import ActivityService

        ActivityService().record(
            school=self.school,
            action=ActivityAction.ASSESSMENT_PUBLISHED,
            label=f"Assessment published #{assessment.code}",
            description=f'"{assessment.name}" was published and can now be assigned.',
            teacher=self.teacher,
            assessment=assessment,
            actor_user=self.teacher.user if self.teacher else None,
        )
        return assessment

    def _validate(self, assessment: Assessment) -> None:
        sections = list(assessment.sections.all())
        if not sections:
            raise ValidationError(
                "An assessment needs at least one section.",
                detail={"sections": ["Add a section before publishing."]},
            )

        empty = [s.name for s in sections if not s.questions.exists()]
        if empty:
            raise ValidationError(
                f"These sections have no questions: {', '.join(empty)}.",
                detail={"sections": empty},
            )

        # Both tags are optional while drafting so authoring stays fluid, and
        # required here, because an untagged item contributes nothing to the
        # matrix placement is computed from.
        untagged = AssessmentQuestion.objects.filter(assessment=assessment).filter(
            subskill__isnull=True
        )
        if untagged.exists():
            raise ValidationError(
                "Every question needs a subskill and level before publishing.",
                detail={"questions": ["Some questions are untagged."]},
            )

        if (
            assessment.opens_at
            and assessment.closes_at
            and assessment.closes_at <= assessment.opens_at
        ):
            raise ValidationError(
                "The closing time must be after the opening time.",
                detail={"closes_at": ["Must be later than opens_at."]},
            )

    def _mint_code(self) -> str:
        """A short, unambiguous code, unique across every school.

        Unscoped uniqueness is deliberate: a child types the code with no
        school context available, so it has to identify the paper on its own.
        """
        for _attempt in range(CODE_ATTEMPTS):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if not self.assessments.code_taken(code):
                return code
        raise ValidationError("Could not allocate an assessment code, please retry.")
