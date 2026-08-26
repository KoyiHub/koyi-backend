"""Login for the school management dashboard.

Authentication itself is stock `rest_framework_simplejwt.JWTAuthentication`
(configured project-wide in settings); what lives here is the *authorisation at
the door* — only a `school`-role user with a `School` profile gets a token.
"""

from apps.common.authentication import RoleScopedTokenSerializer
from apps.common.enums import UserRole


class SchoolTokenObtainPairSerializer(RoleScopedTokenSerializer):
    allowed_role = UserRole.SCHOOL
    profile_attribute = "school"

    def get_profile_payload(self) -> dict:
        school = self.user.school
        return {
            "school": {
                "id": str(school.pk),
                "name": school.name,
                "abbreviation": school.abbreviation,
                "class_system": school.class_system,
            }
        }
