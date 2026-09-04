"""Wire format for groups and lesson plans."""

from rest_framework import serializers

from apps.common.enums import Domain
from apps.instruction.enums import Comparator, CriterionType, ResourceTier
from apps.instruction.models import Group, GroupCriterion, GroupMembership, LessonPlan


class CriterionSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupCriterion
        fields = ["id", "type", "comparator", "level", "skill", "subskill", "school_class"]
        read_only_fields = ["id"]


class CriterionWriteSerializer(serializers.Serializer):
    """One rule. Rules are ANDed, and any field may be absent."""

    type = serializers.ChoiceField(choices=CriterionType.choices)
    comparator = serializers.ChoiceField(
        choices=Comparator.choices, required=False, allow_blank=True, default=""
    )
    level = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=5)
    skill = serializers.UUIDField(required=False, allow_null=True)
    subskill = serializers.UUIDField(required=False, allow_null=True)
    school_class = serializers.UUIDField(required=False, allow_null=True)

    def validate(self, attrs: dict) -> dict:
        """A rule that names nothing would silently match everyone."""
        required = {
            CriterionType.LEVEL: "level",
            CriterionType.SKILL: "skill",
            CriterionType.SUBSKILL: "subskill",
            CriterionType.CLASS: "school_class",
        }[attrs["type"]]
        if attrs.get(required) is None:
            raise serializers.ValidationError(
                {required: [f"A {attrs['type']} rule needs a {required}."]}
            )
        return attrs


class MembershipSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.full_name", read_only=True)
    student_code = serializers.CharField(source="student.student_id", read_only=True)

    class Meta:
        model = GroupMembership
        fields = [
            "id",
            "student",
            "student_name",
            "student_code",
            "joined_at",
            "left_at",
            "join_reason",
            "leave_reason",
        ]
        read_only_fields = fields


class GroupSerializer(serializers.ModelSerializer):
    criteria = CriterionSerializer(many=True, read_only=True)
    size = serializers.IntegerField(read_only=True)
    members = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = [
            "id",
            "name",
            "domain",
            "origin",
            "is_primary",
            "status",
            "resource_tier",
            "stable_until",
            "size",
            "criteria",
            "members",
            "created_at",
        ]
        read_only_fields = ["id", "origin", "status", "stable_until", "created_at"]

    def get_members(self, obj: Group) -> list:
        """Current members only. History is on the membership endpoint."""
        return list(
            MembershipSerializer(
                obj.memberships.filter(left_at__isnull=True).select_related("student"),
                many=True,
            ).data
        )


class GroupWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    domain = serializers.ChoiceField(
        choices=Domain.choices, required=False, allow_blank=True, default=""
    )
    resource_tier = serializers.ChoiceField(
        choices=ResourceTier.choices, required=False, default=ResourceTier.BASIC
    )
    is_primary = serializers.BooleanField(required=False, default=False)
    criteria = CriterionWriteSerializer(many=True, required=False, default=list)


class AddMemberSerializer(serializers.Serializer):
    """A teacher putting a child in a group by hand.

    Such a membership is never closed by the rules engine - a teacher's
    judgement outranks a criterion they did not write.
    """

    student = serializers.UUIDField()


class LessonPlanSerializer(serializers.ModelSerializer):
    """What a teacher reads.

    `status` matters: `fallback` means the canonical plan is being served
    because adaptation did not work, and `failed` means there is nothing to
    show. Both are honest states rather than errors.
    """

    group_name = serializers.CharField(source="group.name", read_only=True, default=None)
    student_name = serializers.CharField(source="student.full_name", read_only=True, default=None)

    class Meta:
        model = LessonPlan
        fields = [
            "id",
            "group",
            "group_name",
            "student",
            "student_name",
            "content",
            "status",
            "valid_from",
            "valid_until",
            "was_helpful",
            "opened_at",
            "created_at",
        ]
        read_only_fields = [f for f in fields if f != "was_helpful"]


class PlanFeedbackSerializer(serializers.Serializer):
    """The only feedback there is. Plans are advice, not documents to revise."""

    was_helpful = serializers.BooleanField()
