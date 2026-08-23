import pytest
from django.urls import reverse

from apps.users.factories import UserFactory

pytestmark = pytest.mark.django_db


class TestRegister:
    url = reverse("v1:users:register")

    def test_creates_user(self, api_client):
        response = api_client.post(
            self.url,
            {
                "email": "new@example.com",
                "first_name": "New",
                "last_name": "User",
                "password": "a-strong-password-1",
                "password_confirm": "a-strong-password-1",
            },
        )

        assert response.status_code == 201
        assert response.data["email"] == "new@example.com"
        assert "password" not in response.data

    def test_rejects_mismatched_passwords(self, api_client):
        response = api_client.post(
            self.url,
            {
                "email": "new@example.com",
                "password": "a-strong-password-1",
                "password_confirm": "something-else-2",
            },
        )

        assert response.status_code == 400
        assert response.data["error"]["type"] == "validation_error"

    def test_rejects_weak_password(self, api_client):
        response = api_client.post(
            self.url,
            {"email": "new@example.com", "password": "pass", "password_confirm": "pass"},
        )

        assert response.status_code == 400

    def test_rejects_duplicate_email_case_insensitively(self, api_client):
        UserFactory(email="taken@example.com")

        response = api_client.post(
            self.url,
            {
                "email": "TAKEN@example.com",
                "password": "a-strong-password-1",
                "password_confirm": "a-strong-password-1",
            },
        )

        assert response.status_code == 400


class TestLogin:
    url = reverse("v1:users:login")

    def test_returns_token_pair_and_user(self, api_client, user, password):
        response = api_client.post(self.url, {"email": user.email, "password": password})

        assert response.status_code == 200
        assert "access" in response.data
        assert "refresh" in response.data
        assert response.data["user"]["email"] == user.email

    def test_rejects_bad_credentials(self, api_client, user):
        response = api_client.post(self.url, {"email": user.email, "password": "wrong"})

        assert response.status_code == 401

    def test_rejects_inactive_user(self, api_client, password):
        inactive = UserFactory(is_active=False)

        response = api_client.post(self.url, {"email": inactive.email, "password": password})

        assert response.status_code == 401


class TestMe:
    url = reverse("v1:users:me")

    def test_requires_authentication(self, api_client):
        response = api_client.get(self.url)

        assert response.status_code == 401
        assert response.data["error"]["type"] == "not_authenticated"

    def test_returns_current_user(self, auth_client, user):
        response = auth_client.get(self.url)

        assert response.status_code == 200
        assert response.data["email"] == user.email

    def test_updates_own_profile(self, auth_client, user):
        response = auth_client.patch(self.url, {"first_name": "Renamed"})

        assert response.status_code == 200
        user.refresh_from_db()
        assert user.first_name == "Renamed"

    def test_email_is_read_only(self, auth_client, user):
        original = user.email

        auth_client.patch(self.url, {"email": "hijack@example.com"})

        user.refresh_from_db()
        assert user.email == original


class TestChangePassword:
    url = reverse("v1:users:password-change")

    def test_changes_password(self, auth_client, user, password):
        response = auth_client.post(
            self.url,
            {"current_password": password, "new_password": "another-strong-pw-9"},
        )

        assert response.status_code == 204
        user.refresh_from_db()
        assert user.check_password("another-strong-pw-9")

    def test_rejects_wrong_current_password(self, auth_client, user):
        response = auth_client.post(
            self.url,
            {"current_password": "nope", "new_password": "another-strong-pw-9"},
        )

        assert response.status_code == 400
        user.refresh_from_db()
        assert not user.check_password("another-strong-pw-9")


class TestLogout:
    url = reverse("v1:users:logout")

    def test_blacklists_refresh_token(self, api_client, user, password):
        tokens = api_client.post(
            reverse("v1:users:login"), {"email": user.email, "password": password}
        ).data
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        response = api_client.post(self.url, {"refresh": tokens["refresh"]})
        assert response.status_code == 205

        # The blacklisted refresh token can no longer be exchanged.
        refresh = api_client.post(reverse("v1:users:token-refresh"), {"refresh": tokens["refresh"]})
        assert refresh.status_code == 401

    def test_requires_refresh_token(self, auth_client):
        response = auth_client.post(self.url, {})

        assert response.status_code == 400
