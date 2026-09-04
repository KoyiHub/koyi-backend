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
