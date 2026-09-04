"""Wire format for the teacher dashboard.

Serializers validate on the way in and render on the way out; they hand
`apps.assessments.dto` objects to the services and never call the ORM
themselves.
"""

from decimal import Decimal

from rest_framework import serializers

from apps.assessments.models import (
    Assessment,
    AssessmentAssignment,
    AssessmentQuestion,
    AssessmentQuestionContent,
    AssessmentQuestionOption,
    AssessmentSection,
)
from apps.common.enums import ContentBlockType, Domain, OptionType, QuestionLayout, QuestionType
from apps.curriculum.models import Question, Skill, Subskill
from apps.media_assets.models import MediaAsset

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


class SubskillSerializer(serializers.ModelSerializer):
    level_range = serializers.SerializerMethodField()

    class Meta:
        model = Subskill
        fields = ["id", "code", "name", "min_level", "max_level", "level_range"]

    def get_level_range(self, obj: Subskill) -> list[int]:
        """The narrowest applicable range, so the client can bound its picker."""
        return list(obj.level_range)


class SkillSerializer(serializers.ModelSerializer):
    subskills = SubskillSerializer(many=True, read_only=True)

    class Meta:
        model = Skill
        fields = [
            "id",
            "code",
            "name",
            "domain",
            "min_level",
            "max_level",
            "is_core",
            "subskills",
        ]


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


class MediaAssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaAsset
        fields = [
            "id",
            "type",
            "url",
            "mime_type",
            "original_filename",
            "size_bytes",
            "duration_seconds",
        ]
        read_only_fields = ["id"]


# ---------------------------------------------------------------------------
# Question bank (read-only)
# ---------------------------------------------------------------------------


class BankQuestionSerializer(serializers.ModelSerializer):
    """A bank question, complete enough for the client to prefill the form."""

    subskill = SubskillSerializer(read_only=True)
    skill_name = serializers.CharField(source="subskill.skill.name", read_only=True)
    domain = serializers.CharField(source="subskill.skill.domain", read_only=True)
    contents = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = [
            "id",
            "content",
            "type",
            "layout",
            "fln_level",
            "subskill",
            "skill_name",
            "domain",
            "contents",
            "options",
        ]

    def get_contents(self, obj: Question) -> list[dict]:
        return [
            {
                "type": block.type,
                "display_order": block.display_order,
                "text_content": block.text_content,
                "media_id": str(block.media_id) if block.media_id else None,
                "alt_text": block.alt_text,
                "caption": block.caption,
            }
            for block in obj.contents.all()
        ]

    def get_options(self, obj: Question) -> list[dict]:
        return [
            {
                "type": option.option_type,
                "value": option.content,
                "is_correct": option.is_correct,
            }
            for option in obj.options.all()
        ]


# ---------------------------------------------------------------------------
# Assessment authoring
# ---------------------------------------------------------------------------


class AssessmentWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    instructions = serializers.CharField(required=False, allow_blank=True, default="")
    session_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    opens_at = serializers.DateTimeField(required=False, allow_null=True, default=None)
    closes_at = serializers.DateTimeField(required=False, allow_null=True, default=None)


class AssessmentUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    instructions = serializers.CharField(required=False, allow_blank=True)
    session_id = serializers.UUIDField(required=False, allow_null=True)
    opens_at = serializers.DateTimeField(required=False, allow_null=True)
    closes_at = serializers.DateTimeField(required=False, allow_null=True)


class SectionSerializer(serializers.ModelSerializer):
    covers = serializers.SerializerMethodField()
    question_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = AssessmentSection
        fields = [
            "id",
            "domain",
            "name",
            "instructions",
            "order",
            "timer",
            "covers",
            "question_count",
        ]
        read_only_fields = ["id", "order"]

    def get_covers(self, obj: AssessmentSection) -> list[dict]:
        return [{"id": str(s.pk), "code": s.code, "name": s.name} for s in obj.covers.all()]


class SectionWriteSerializer(serializers.Serializer):
    domain = serializers.ChoiceField(choices=Domain.choices)
    name = serializers.CharField(max_length=255)
    instructions = serializers.CharField(required=False, allow_blank=True, default="")
    timer = serializers.DurationField(required=False, allow_null=True, default=None)
    covers = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True, default=list
    )


class SectionUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    instructions = serializers.CharField(required=False, allow_blank=True)
    timer = serializers.DurationField(required=False, allow_null=True)
    order = serializers.IntegerField(required=False, min_value=1)
    covers = serializers.ListField(child=serializers.UUIDField(), required=False)


class AssessmentSerializer(serializers.ModelSerializer):
    sections = SectionSerializer(many=True, read_only=True)
    teacher_name = serializers.CharField(source="teacher.full_name", read_only=True, default=None)

    class Meta:
        model = Assessment
        fields = [
            "id",
            "name",
            "instructions",
            "code",
            "status",
            "opens_at",
            "closes_at",
            "published_at",
            "teacher_name",
            "sections",
            "created_at",
        ]
        read_only_fields = ["id", "code", "status", "published_at", "created_at"]


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


class ContentBlockSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=ContentBlockType.choices)
    display_order = serializers.IntegerField(min_value=1)
    text_content = serializers.CharField(required=False, allow_blank=True, default="")
    media_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    alt_text = serializers.CharField(required=False, allow_blank=True, default="")
    caption = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs: dict) -> dict:
        # Mirrors the database constraint, so the client gets a field error
        # rather than a 500 from an IntegrityError.
        if not attrs.get("text_content") and not attrs.get("media_id"):
            raise serializers.ValidationError("A block needs either text or media.")
        return attrs


class OptionSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=OptionType.choices)
    value = serializers.CharField(required=False, allow_blank=True, default="")
    media_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    is_correct = serializers.BooleanField(default=False)

    def validate(self, attrs: dict) -> dict:
        if not attrs.get("value") and not attrs.get("media_id"):
            raise serializers.ValidationError("An option needs either a value or media.")
        return attrs


class AnswerSerializer(serializers.Serializer):
    value = serializers.CharField(required=False, allow_blank=True, default="")
    media_id = serializers.UUIDField(required=False, allow_null=True, default=None)


class QuestionWriteSerializer(serializers.Serializer):
    """One question, whether selected from the bank or authored.

    The payload is the same either way: picking from the bank prefills this
    form in the client, so the server always receives complete content.
    `source_question_id` is what records the difference.
    """

    subskill_id = serializers.UUIDField()
    fln_level = serializers.IntegerField(min_value=1, max_value=5)
    question_type = serializers.ChoiceField(choices=QuestionType.choices)
    text = serializers.CharField()
    layout = serializers.ChoiceField(
        choices=QuestionLayout.choices, required=False, allow_blank=True, default=""
    )
    description = serializers.CharField(required=False, allow_blank=True, default="")
    point = serializers.DecimalField(
        max_digits=6, decimal_places=2, required=False, default=Decimal("1"), min_value=0
    )
    source_question_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    contents = ContentBlockSerializer(many=True, required=False, default=list)
    options = OptionSerializer(many=True, required=False, default=list)
    answer = AnswerSerializer(required=False, allow_null=True, default=None)

    def validate(self, attrs: dict) -> dict:
        option_based = attrs["question_type"] in QuestionType.option_based()
        options = attrs.get("options") or []
        if option_based and not options:
            raise serializers.ValidationError(
                {"options": ["An option-based question needs at least two options."]}
            )
        if option_based and not any(option["is_correct"] for option in options):
            raise serializers.ValidationError({"options": ["Mark one option as correct."]})
        if not option_based and options:
            raise serializers.ValidationError(
                {"options": [f"A {attrs['question_type']} question takes no options."]}
            )
        return attrs


class QuestionListWriteSerializer(serializers.Serializer):
    """The whole ordered list for a section, replaced wholesale."""

    questions = QuestionWriteSerializer(many=True)


class AssessmentQuestionSerializer(serializers.ModelSerializer):
    subskill = SubskillSerializer(read_only=True)
    skill_name = serializers.CharField(source="skill.name", read_only=True)
    contents = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()

    class Meta:
        model = AssessmentQuestion
        fields = [
            "id",
            "order",
            "text",
            "description",
            "question_type",
            "layout",
            "point",
            "fln_level",
            "subskill",
            "skill_name",
            "source_question",
            "contents",
            "options",
        ]

    def get_contents(self, obj: AssessmentQuestion) -> list[dict]:
        return [
            {
                "type": block.type,
                "display_order": block.display_order,
                "text_content": block.text_content,
                "media": _media(block),
                "alt_text": block.alt_text,
                "caption": block.caption,
            }
            for block in obj.contents.all()
        ]

    def get_options(self, obj: AssessmentQuestion) -> list[dict]:
        return [
            {
                "id": str(option.pk),
                "type": option.type,
                "value": option.value,
                "media": _media(option),
                "is_correct": option.is_correct,
            }
            for option in obj.options.all()
        ]


def _media(obj: AssessmentQuestionContent | AssessmentQuestionOption) -> dict | None:
    media = obj.media
    if media is None:
        return None
    return {"id": str(media.pk), "url": media.url, "type": media.type}


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


class CoverageCellSerializer(serializers.Serializer):
    subskill_id = serializers.UUIDField()
    subskill_name = serializers.CharField()
    skill_id = serializers.UUIDField()
    skill_name = serializers.CharField()
    domain = serializers.CharField()
    fln_level = serializers.IntegerField()
    item_count = serializers.IntegerField()


class SectionCoverageSerializer(serializers.Serializer):
    section_id = serializers.UUIDField()
    section_name = serializers.CharField()
    domain = serializers.CharField()
    question_count = serializers.IntegerField()
    cells = CoverageCellSerializer(many=True)
    gaps = serializers.ListField(child=serializers.CharField())


class AssessmentCoverageSerializer(serializers.Serializer):
    """What the paper can establish, and what it cannot."""

    assessment_id = serializers.UUIDField()
    question_count = serializers.IntegerField()
    sections = SectionCoverageSerializer(many=True)
    domains = serializers.ListField(child=serializers.CharField())
    levels_probed = serializers.ListField(child=serializers.IntegerField())
    warnings = serializers.ListField(child=serializers.CharField())


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


class AssignmentSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_id = serializers.CharField(source="student.student_id", read_only=True)
    school_class = serializers.SerializerMethodField()

    class Meta:
        model = AssessmentAssignment
        fields = [
            "id",
            "student",
            "student_name",
            "student_id",
            "school_class",
            # The child's personal code. Readable by their teacher on purpose —
            # a child who has lost theirs needs someone able to tell them.
            "code",
            "status",
            "started_at",
            "submitted_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_school_class(self, obj: AssessmentAssignment) -> str | None:
        school_class = obj.student.school_class
        return str(school_class) if school_class else None


class AssignSerializer(serializers.Serializer):
    """Who should sit this paper.

    Three ways to say it, because a teacher thinks in whole classes far more
    often than in individual children. At least one must be given.
    """

    student_ids = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)
    class_ids = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)
    all_my_students = serializers.BooleanField(default=False)

    def validate(self, attrs: dict) -> dict:
        if not (attrs["student_ids"] or attrs["class_ids"] or attrs["all_my_students"]):
            raise serializers.ValidationError(
                "Give student_ids, class_ids, or set all_my_students."
            )
        return attrs


class AssignmentRosterSerializer(serializers.Serializer):
    """One row of the printable code sheet.

    The classroom path needs this: with a personal code per child, a teacher
    can no longer write one thing on a board, so they need something to hand
    out or read from.
    """

    student_name = serializers.CharField()
    student_id = serializers.CharField()
    school_class = serializers.CharField(allow_null=True)
    code = serializers.CharField()
    status = serializers.CharField()


class AssessmentRosterSerializer(serializers.Serializer):
    assessment_id = serializers.UUIDField()
    assessment_name = serializers.CharField()
    assessment_code = serializers.CharField()
    opens_at = serializers.DateTimeField(allow_null=True)
    closes_at = serializers.DateTimeField(allow_null=True)
    rows = AssignmentRosterSerializer(many=True)


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


class MarkingStatusSerializer(serializers.Serializer):
    """How much of the paper is actually marked.

    Rendered wherever figures are shown. Free-form answers are marked
    asynchronously, so a teacher opening this early is looking at numbers that
    will still move.
    """

    total = serializers.IntegerField()
    marked = serializers.IntegerField()
    pending = serializers.IntegerField()
    complete = serializers.BooleanField()


class SkillCellSerializer(serializers.Serializer):
    skill_id = serializers.CharField()
    skill_name = serializers.CharField()
    domain = serializers.CharField()
    fln_level = serializers.IntegerField()
    passed = serializers.IntegerField()
    total = serializers.IntegerField()
    pass_rate = serializers.FloatField()


class MissedSubskillSerializer(serializers.Serializer):
    subskill_id = serializers.CharField()
    subskill_name = serializers.CharField()
    skill_name = serializers.CharField()
    domain = serializers.CharField()
    fln_level = serializers.IntegerField()
    failed = serializers.IntegerField()
    total = serializers.IntegerField()
    failed_pct = serializers.IntegerField()


class NarrativeSerializer(serializers.Serializer):
    summary = serializers.CharField(allow_blank=True)
    attention = serializers.CharField(allow_blank=True)
    strength = serializers.CharField(allow_blank=True)


class AssessmentAnalyticsSerializer(serializers.Serializer):
    """Lead with `level_distribution`, not `average_percentage`.

    How many children sit at each level is what a teacher acts on; an average
    across two independent domains describes neither.
    """

    assessment_id = serializers.CharField()
    name = serializers.CharField()
    code = serializers.CharField()
    marking_status = MarkingStatusSerializer()
    level_distribution = serializers.DictField()
    participation = serializers.DictField()
    section_completion = serializers.ListField(child=serializers.DictField())
    skill_matrix = SkillCellSerializer(many=True)
    most_missed = MissedSubskillSerializer(many=True)
    average_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    warnings = serializers.ListField(child=serializers.CharField())
    narrative = NarrativeSerializer(allow_null=True, required=False)


class RosterEntrySerializer(serializers.Serializer):
    student_id = serializers.CharField()
    full_name = serializers.CharField()
    school_class = serializers.CharField(allow_null=True)
    literacy_level = serializers.IntegerField(allow_null=True)
    numeracy_level = serializers.IntegerField(allow_null=True)
    weak_subskills = serializers.ListField(child=serializers.CharField())


class SkillStandingSerializer(serializers.Serializer):
    skill_id = serializers.CharField()
    skill_name = serializers.CharField()
    domain = serializers.CharField()
    highest_level_passed = serializers.IntegerField(allow_null=True)
    broke_down_at = serializers.IntegerField(allow_null=True)
    weak_subskills = serializers.ListField(child=serializers.CharField())


class LevelMovementSerializer(serializers.Serializer):
    domain = serializers.CharField()
    previous = serializers.IntegerField(allow_null=True)
    current = serializers.IntegerField(allow_null=True)
    direction = serializers.CharField()


class StudentBreakdownSerializer(serializers.Serializer):
    """By skill, with level context. A percentage alone says nothing."""

    student_id = serializers.CharField()
    full_name = serializers.CharField()
    school_class = serializers.CharField(allow_null=True)
    literacy_level = serializers.IntegerField(allow_null=True)
    numeracy_level = serializers.IntegerField(allow_null=True)
    last_assessed_at = serializers.DateTimeField(allow_null=True)
    skills = SkillStandingSerializer(many=True)
    movement = LevelMovementSerializer(many=True)
    narrative = NarrativeSerializer(allow_null=True, required=False)


# ---------------------------------------------------------------------------
# Response review
# ---------------------------------------------------------------------------


class ReviewedQuestionSerializer(serializers.ModelSerializer):
    """One question as the child saw it, annotated with what happened.

    Options carry both `is_correct` and `was_selected`, so the client can
    render the green and red highlighting without a second request or any
    cross-referencing of its own. This is the one place the answer key is
    served - to a teacher, after the fact, never to the runner.
    """

    contents = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()
    response = serializers.SerializerMethodField()
    subskill_name = serializers.CharField(source="subskill.name", read_only=True)
    skill_name = serializers.CharField(source="skill.name", read_only=True)
    section_name = serializers.CharField(source="section.name", read_only=True)

    class Meta:
        model = AssessmentQuestion
        fields = [
            "id",
            "order",
            "text",
            "description",
            "question_type",
            "layout",
            "point",
            "fln_level",
            "subskill_name",
            "skill_name",
            "section_name",
            "contents",
            "options",
            "response",
        ]

    def get_contents(self, obj: AssessmentQuestion) -> list[dict]:
        return [
            {
                "type": block.type,
                "display_order": block.display_order,
                "text_content": block.text_content,
                "media": _media(block),
                "alt_text": block.alt_text,
                "caption": block.caption,
            }
            for block in obj.contents.all()
        ]

    def get_options(self, obj: AssessmentQuestion) -> list[dict]:
        selected = self._selected_option_ids(obj)
        return [
            {
                "id": str(option.pk),
                "type": option.type,
                "value": option.value,
                "media": _media(option),
                "is_correct": option.is_correct,
                "was_selected": option.pk in selected,
            }
            for option in obj.options.all()
        ]

    def get_response(self, obj: AssessmentQuestion) -> dict | None:
        """What the child gave, and how it was marked.

        `is_correct` null means pending, not wrong - the AI marker has not
        reached it, its confidence was too low to act on, or a recording
        failed. The UI should offer a teacher the decision rather than
        rendering it as an error.
        """
        response = self._response(obj)
        if response is None:
            return None
        return {
            "id": str(response.pk),
            "text_value": response.text_value,
            "transcript": response.transcript,
            "media": (
                {"id": str(response.media_value_id), "url": response.media_value.url}
                if response.media_value_id
                else None
            ),
            "is_correct": response.is_correct,
            "awarded_points": response.awarded_points,
            "graded_by": response.graded_by,
            "grading_confidence": response.grading_confidence,
            "error_type": response.error_type,
            "observation_note": response.observation_note,
        }

    def _response(self, obj: AssessmentQuestion):
        """The acting child's response, from the view's `to_attr` prefetch.

        Read through `getattr` because the attribute is attached by the
        queryset rather than declared on the model, so a serializer used
        without that prefetch degrades to "no response" instead of raising.
        """
        return next(iter(getattr(obj, "student_responses", [])), None)

    def _selected_option_ids(self, obj: AssessmentQuestion) -> set:
        response = self._response(obj)
        if response is None:
            return set()
        return {row.assessment_question_option_id for row in response.selected_options.all()}


class ResponseReviewSerializer(serializers.Serializer):
    """Everything a teacher needs to walk one child's paper."""

    student_id = serializers.CharField()
    full_name = serializers.CharField()
    assessment_id = serializers.CharField()
    assessment_name = serializers.CharField()
    status = serializers.CharField()
    items_attempted = serializers.IntegerField()
    items_correct = serializers.IntegerField()
    pending = serializers.IntegerField()
    percentage = serializers.DecimalField(max_digits=5, decimal_places=2, allow_null=True)
    questions = ReviewedQuestionSerializer(many=True)
