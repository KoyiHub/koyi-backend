"""Shared login machinery for the two credentialled surfaces.

School management and teachers both authenticate as `users.User` rows with JWT,
so the only thing that differs between their login endpoints is which role is
allowed through and what profile is echoed back. `RoleScopedTokenSerializer`
captures that difference in two class attributes.

Refusing the wrong role *at login* rather than only at the permission layer
matters: a teacher who posts their credentials to the school login must not
walk away holding a token at all, even one that every school view would later
reject.
"""

from typing import TYPE_CHECKING, ClassVar

from django.contrib.auth import get_user_model
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

if TYPE_CHECKING:
    from apps.users.models import User as UserType

User = get_user_model()


class RoleScopedTokenSerializer(TokenObtainPairSerializer):
    """Issues a token pair only to users holding `allowed_role`."""

    allowed_role: ClassVar[str] = ""
    #: Reverse accessor for the profile that must exist, e.g. "school".
    profile_attribute: ClassVar[str] = ""
    #: Deliberately identical for "wrong password", "wrong portal" and "no
    #: profile" so the endpoint cannot be used to enumerate account types.
    invalid_credentials_message: ClassVar[str] = "No active account found with these credentials."

    if TYPE_CHECKING:
        # `TokenObtainPairSerializer` types this as optional, but it is always
        # populated by the time `validate` runs — the parent raises otherwise.
        user: "UserType"

    def validate(self, attrs: dict) -> dict:
        data: dict = dict(super().validate(attrs))

        if self.user.role != self.allowed_role:
            raise AuthenticationFailed(self.invalid_credentials_message, "no_active_account")
        if self.profile_attribute and not hasattr(self.user, self.profile_attribute):
            raise AuthenticationFailed(self.invalid_credentials_message, "no_active_account")

        data["user"] = {
            "id": str(self.user.pk),
            "email": self.user.email,
            "role": self.user.role,
            "email_verified": self.user.email_verified,
        }
        data.update(self.get_profile_payload())
        return data

    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Informational only — permissions re-read the role from the database
        # so a revoked role does not survive until the token expires.
        token["role"] = user.role
        token["email"] = user.email
        return token

    def get_profile_payload(self) -> dict:
        """Extra keys merged into the login response. Override per portal."""
        return {}
