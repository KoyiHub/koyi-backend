"""Fixtures available to every test in the repo."""

import pytest
from rest_framework.test import APIClient

from apps.users.factories import DEFAULT_PASSWORD, StaffUserFactory, SuperUserFactory, UserFactory


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    """Keep uploads out of the working tree; wiped after each test."""
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def password() -> str:
    return DEFAULT_PASSWORD


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def staff_user(db):
    return StaffUserFactory()


@pytest.fixture
def superuser(db):
    return SuperUserFactory()


@pytest.fixture
def api_client() -> APIClient:
    """Unauthenticated DRF client."""
    return APIClient()


@pytest.fixture
def auth_client(api_client, user) -> APIClient:
    """DRF client carrying a valid JWT for `user`."""
    from rest_framework_simplejwt.tokens import RefreshToken

    token = RefreshToken.for_user(user).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api_client


@pytest.fixture
def as_user(api_client):
    """Factory fixture: `client = as_user(some_user)`."""
    from rest_framework_simplejwt.tokens import RefreshToken

    def _login(target):
        token = RefreshToken.for_user(target).access_token
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return api_client

    return _login
