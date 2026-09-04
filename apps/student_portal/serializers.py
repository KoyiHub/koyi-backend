"""Wire format for the assessment runner.

Everything here renders for a child on a tablet, so it stays close to what a
screen needs: which section is open, what the question shows, what can be
tapped. Nothing exposes whether an option is correct.
"""

from rest_framework import serializers

from apps.assessments.models import AssessmentQuestion, AssessmentSection, AssessmentSectionResult


class VerifySerializer(serializers.Serializer):
    """The form a child fills in to open a paper.

    Two codes, both case insensitive: the assessment's says which paper, the
    personal one says it is theirs. A guardian link fills in both.
    """

    assessment_code = serializers.CharField(max_length=16)
    code = serializers.CharField(max_length=16)


class SittingQuestionSerializer(serializers.ModelSerializer):
    """A question as the child sees it.

    `is_correct` is deliberately absent from the options: the runner must not
    ship the answer key to the device.
    """

    contents = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()
    subskill_name = serializers.CharField(source="subskill.name", read_only=True)

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
            "subskill_name",
            "contents",
            "options",
        ]

    def get_contents(self, obj: AssessmentQuestion) -> list[dict]:
        return [
            {
                "type": block.type,
                "display_order": block.display_order,
                "text_content": block.text_content,
                "media": _media(block.media),
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
                "media": _media(option.media),
            }
            for option in obj.options.all()
        ]


class SectionProgressSerializer(serializers.ModelSerializer):
    """One row on the instruction page: a section and whether it is open."""

    id = serializers.UUIDField(source="section_id", read_only=True)
    name = serializers.CharField(source="section.name", read_only=True)
    domain = serializers.CharField(source="section.domain", read_only=True)
    instructions = serializers.CharField(source="section.instructions", read_only=True)
    order = serializers.IntegerField(source="section.order", read_only=True)
    timer = serializers.DurationField(source="section.timer", read_only=True)
    question_count = serializers.SerializerMethodField()

    class Meta:
        model = AssessmentSectionResult
        fields = [
            "id",
            "name",
            "domain",
            "instructions",
            "order",
            "timer",
            "status",
            "question_count",
            "started_at",
            "submitted_at",
            "expires_at",
        ]

    def get_question_count(self, obj: AssessmentSectionResult) -> int:
        return obj.section.questions.count()


class SittingOverviewSerializer(serializers.Serializer):
    """The instruction page: the paper, and which section is open."""

    assessment_id = serializers.UUIDField()
    name = serializers.CharField()
    instructions = serializers.CharField()
    code = serializers.CharField()
    student_name = serializers.CharField()
    status = serializers.CharField()
    sections = SectionProgressSerializer(many=True)


class SaveResponseSerializer(serializers.Serializer):
    """One answer. Sent on every change, so it has to be idempotent."""

    text_value = serializers.CharField(required=False, allow_blank=True, default="")
    media_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    option_ids = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=True, default=list
    )


def _media(asset) -> dict | None:
    if asset is None:
        return None
    return {"id": str(asset.pk), "url": asset.url, "type": asset.type}


def section_payload(section: AssessmentSection) -> dict:
    return {"id": str(section.pk), "name": section.name, "domain": section.domain}
