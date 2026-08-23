from django.contrib.auth import get_user_model
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView as BaseTokenObtainPairView

from apps.users.serializers import (
    ChangePasswordSerializer,
    LogoutSerializer,
    RegisterSerializer,
    UserSerializer,
)

User = get_user_model()


@extend_schema(tags=["auth"])
class RegisterView(generics.CreateAPIView):
    """Create a new account."""

    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def create(self, request: Request, *_args, **_kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["auth"])
class LoginView(BaseTokenObtainPairView):
    """Exchange credentials for an access/refresh token pair."""

    permission_classes = (AllowAny,)  # type: ignore[assignment]


@extend_schema(
    tags=["auth"],
    responses={205: OpenApiResponse(description="Refresh token blacklisted")},
)
class LogoutView(generics.GenericAPIView):
    """Blacklist the supplied refresh token so it cannot be reused."""

    serializer_class = LogoutSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError as exc:
            raise ValidationError({"refresh": "Token is invalid or expired."}) from exc
        return Response(status=status.HTTP_205_RESET_CONTENT)


@extend_schema(tags=["users"])
class MeView(generics.RetrieveUpdateAPIView):
    """Read or update the authenticated user's own profile."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


@extend_schema(
    tags=["users"],
    responses={204: OpenApiResponse(description="Password changed")},
)
class ChangePasswordView(generics.GenericAPIView):
    serializer_class = ChangePasswordSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)
