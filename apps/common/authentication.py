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
from django.contrib.auth.models import update_last_login
from rest_framework_simplejwt.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.settings import api_settings

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
        self.check_eligible(self.user)

        data["user"] = _identity(self.user)
        data.update(self.get_profile_payload())
        return data

    @classmethod
    def check_eligible(cls, user: "UserType") -> None:
        """The role and profile checks, without the password step.

        Split out so a flow that established the identity some other way - a
        second factor, say - applies exactly the same rules as a password
        login rather than a similar-looking copy of them.
        """
        if user.role != cls.allowed_role:
            raise AuthenticationFailed(cls.invalid_credentials_message, "no_active_account")
        if cls.profile_attribute and not hasattr(user, cls.profile_attribute):
            raise AuthenticationFailed(cls.invalid_credentials_message, "no_active_account")

    @classmethod
    def issue_for(cls, user: "UserType") -> dict:
        """The response a successful login returns, for a caller that has
        already proved who the user is.

        Both steps of a two-step sign-in end up here, so the client sees one
        payload shape whether or not a second factor stood in the way.
        """
        cls.check_eligible(user)

        serializer = cls()
        serializer.user = user
        refresh = cls.get_token(user)
        if api_settings.UPDATE_LAST_LOGIN:
            # simplejwt calls it with the same `None` sender; the stub is
            # stricter than the signal actually is.
            update_last_login(None, user)  # type: ignore[arg-type]

        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "user": _identity(user),
            **serializer.get_profile_payload(),
        }

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


def _identity(user: "UserType") -> dict:
    return {
        "id": str(user.pk),
        "email": user.email,
        "role": user.role,
        "email_verified": user.email_verified,
    }
