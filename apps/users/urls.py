from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from apps.users.views import (
    ChangePasswordView,
    LoginView,
    LogoutView,
    MeView,
    RegisterView,
)

app_name = "users"

urlpatterns = [
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("token/verify/", TokenVerifyView.as_view(), name="token-verify"),
    path("me/", MeView.as_view(), name="me"),
    path("password/change/", ChangePasswordView.as_view(), name="password-change"),
]
