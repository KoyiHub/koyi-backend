"""Registration, sign-in and password recovery for school management.

The behaviour under test here is mostly what the endpoints *refuse* to say. A
form that answers differently for a registered and an unregistered address is a
directory of which schools use Koyi, and the tests that pin that down are the
ones worth having.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from apps.users.enums import VerificationPurpose
from apps.users.factories import DEFAULT_PASSWORD
from apps.users.models import VerificationCode
from apps.users.verification import MAX_ATTEMPTS, VerificationService

pytestmark = pytest.mark.django_db


def registration_payload(**overrides) -> dict:
    return {
        "email": "head@greenwood.example.com",
        "password": DEFAULT_PASSWORD,
        "password_confirm": DEFAULT_PASSWORD,
        "name": "Greenwood Primary School",
        "abbreviation": "GWD",
        **overrides,
    }


def latest_code(user, purpose) -> str:
    """The plaintext is never stored, so read it out of the sent email."""
    row = VerificationCode.objects.filter(user=user, purpose=purpose).latest("created_at")
    assert row is not None
    for message in reversed(mail.outbox):
        for token in message.body.split():
            if token.isdigit() and len(token) == 6:
                return token
    raise AssertionError("no code was emailed")


class TestRegistration:
    def test_signup_creates_the_school_but_issues_no_token(self, api_client):
        response = api_client.post(reverse("v1:school_portal:register"), registration_payload())

        assert response.status_code == 201
        assert response.data["otp_sent"] is True
        # The account exists and is unusable until the code comes back.
        assert "access" not in response.data
        assert len(mail.outbox) == 1

    def test_the_code_finishes_signup_and_verifies_the_address(self, api_client):
        api_client.post(reverse("v1:school_portal:register"), registration_payload())
        user = _user("head@greenwood.example.com")
        code = latest_code(user, VerificationPurpose.REGISTER)

        response = api_client.post(
            reverse("v1:school_portal:register-verify"),
            {"email": user.email, "code": code},
        )

        assert response.status_code == 200
        assert "access" in response.data
        assert response.data["school"]["abbreviation"] == "GWD"
        user.refresh_from_db()
        assert user.email_verified is True

    def test_a_code_cannot_be_spent_twice(self, api_client):
        api_client.post(reverse("v1:school_portal:register"), registration_payload())
        user = _user("head@greenwood.example.com")
        code = latest_code(user, VerificationPurpose.REGISTER)
        body = {"email": user.email, "code": code}

        assert api_client.post(reverse("v1:school_portal:register-verify"), body).status_code == 200
        assert api_client.post(reverse("v1:school_portal:register-verify"), body).status_code == 400

    def test_resending_retires_the_previous_code(self, api_client):
        api_client.post(reverse("v1:school_portal:register"), registration_payload())
        user = _user("head@greenwood.example.com")
        first = latest_code(user, VerificationPurpose.REGISTER)

        api_client.post(reverse("v1:school_portal:otp-resend"), {"email": user.email})
        second = latest_code(user, VerificationPurpose.REGISTER)

        assert first != second
        stale = api_client.post(
            reverse("v1:school_portal:register-verify"), {"email": user.email, "code": first}
        )
        assert stale.status_code == 400

    def test_resend_says_nothing_about_addresses_it_does_not_know(self, api_client):
        known = api_client.post(
            reverse("v1:school_portal:otp-resend"), {"email": "nobody@example.com"}
        )
        assert known.status_code == 200
        assert mail.outbox == []


class TestLogin:
    def test_a_correct_password_returns_a_challenge_rather_than_a_token(self, api_client, school):
        response = api_client.post(
            reverse("v1:school_portal:login"),
            {"email": school.email, "password": DEFAULT_PASSWORD},
        )

        assert response.status_code == 200
        assert response.data["otp_required"] is True
        assert "access" not in response.data
        assert len(mail.outbox) == 1

    def test_the_second_step_returns_the_token_pair(self, api_client, school):
        started = api_client.post(
            reverse("v1:school_portal:login"),
            {"email": school.email, "password": DEFAULT_PASSWORD},
        )
        code = latest_code(school.user, VerificationPurpose.LOGIN)

        response = api_client.post(
            reverse("v1:school_portal:login-verify"),
            {"challenge": started.data["challenge"], "code": code},
        )

        assert response.status_code == 200
        assert "access" in response.data
        assert response.data["school"]["id"] == str(school.pk)

    def test_a_wrong_password_never_reaches_the_second_step(self, api_client, school):
        response = api_client.post(
            reverse("v1:school_portal:login"),
            {"email": school.email, "password": "not-the-password"},
        )

        assert response.status_code == 401
        assert mail.outbox == []

    def test_a_teacher_is_refused_the_school_door_with_the_same_message(self, api_client, school):
        from apps.schools.factories import TeacherFactory

        teacher = TeacherFactory(school=school)
        as_teacher = api_client.post(
            reverse("v1:school_portal:login"),
            {"email": teacher.user.email, "password": DEFAULT_PASSWORD},
        )
        wrong_password = api_client.post(
            reverse("v1:school_portal:login"),
            {"email": school.email, "password": "not-the-password"},
        )

        assert as_teacher.status_code == wrong_password.status_code == 401
        assert as_teacher.data["error"]["message"] == wrong_password.data["error"]["message"]

    def test_a_wrong_code_is_rejected_and_the_challenge_survives_one_slip(self, api_client, school):
        started = api_client.post(
            reverse("v1:school_portal:login"),
            {"email": school.email, "password": DEFAULT_PASSWORD},
        )
        challenge = started.data["challenge"]

        missed = api_client.post(
            reverse("v1:school_portal:login-verify"), {"challenge": challenge, "code": "000000"}
        )
        assert missed.status_code == 400

        code = latest_code(school.user, VerificationPurpose.LOGIN)
        recovered = api_client.post(
            reverse("v1:school_portal:login-verify"), {"challenge": challenge, "code": code}
        )
        assert recovered.status_code == 200

    def test_a_code_burns_out_after_enough_wrong_guesses(self, api_client, school):
        started = api_client.post(
            reverse("v1:school_portal:login"),
            {"email": school.email, "password": DEFAULT_PASSWORD},
        )
        challenge = started.data["challenge"]
        code = latest_code(school.user, VerificationPurpose.LOGIN)

        for _ in range(MAX_ATTEMPTS):
            api_client.post(
                reverse("v1:school_portal:login-verify"),
                {"challenge": challenge, "code": "000000"},
            )

        # The right code no longer works: the attempts, not the throttle, are
        # what an attacker with many addresses runs out of.
        response = api_client.post(
            reverse("v1:school_portal:login-verify"), {"challenge": challenge, "code": code}
        )
        assert response.status_code == 400

    def test_an_expired_code_is_refused(self, api_client, school):
        started = api_client.post(
            reverse("v1:school_portal:login"),
            {"email": school.email, "password": DEFAULT_PASSWORD},
        )
        code = latest_code(school.user, VerificationPurpose.LOGIN)
        VerificationCode.objects.filter(user=school.user).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )

        response = api_client.post(
            reverse("v1:school_portal:login-verify"),
            {"challenge": started.data["challenge"], "code": code},
        )
        assert response.status_code == 400

    def test_signing_in_verifies_an_address_registration_never_confirmed(self, api_client, school):
        school.user.email_verified = False
        school.user.save(update_fields=["email_verified"])

        started = api_client.post(
            reverse("v1:school_portal:login"),
            {"email": school.email, "password": DEFAULT_PASSWORD},
        )
        code = latest_code(school.user, VerificationPurpose.LOGIN)
        api_client.post(
            reverse("v1:school_portal:login-verify"),
            {"challenge": started.data["challenge"], "code": code},
        )

        school.user.refresh_from_db()
        assert school.user.email_verified is True


class TestPasswordReset:
    def test_the_three_steps_set_a_new_password(self, api_client, school):
        api_client.post(reverse("v1:school_portal:password-reset-request"), {"email": school.email})
        code = latest_code(school.user, VerificationPurpose.PASSWORD_RESET)

        verified = api_client.post(
            reverse("v1:school_portal:password-reset-verify"),
            {"email": school.email, "code": code},
        )
        assert verified.status_code == 200

        confirmed = api_client.post(
            reverse("v1:school_portal:password-reset-confirm"),
            {
                "reset_token": verified.data["reset_token"],
                "password": "a-new-password-456!",
                "password_confirm": "a-new-password-456!",
            },
        )
        assert confirmed.status_code == 204

        school.user.refresh_from_db()
        assert school.user.check_password("a-new-password-456!")

    def test_the_request_step_looks_identical_for_an_unknown_address(self, api_client, school):
        known = api_client.post(
            reverse("v1:school_portal:password-reset-request"), {"email": school.email}
        )
        unknown = api_client.post(
            reverse("v1:school_portal:password-reset-request"),
            {"email": "nobody@example.com"},
        )

        assert known.status_code == unknown.status_code == 200
        assert known.data == unknown.data
        # Only one of them actually sent anything.
        assert len(mail.outbox) == 1

    def test_the_emailed_code_alone_cannot_set_a_password(self, api_client, school):
        api_client.post(reverse("v1:school_portal:password-reset-request"), {"email": school.email})
        code = latest_code(school.user, VerificationPurpose.PASSWORD_RESET)

        # Someone who read the code over a shoulder still needs the handle the
        # verify step returns, which never leaves the browser that asked.
        response = api_client.post(
            reverse("v1:school_portal:password-reset-confirm"),
            {
                "reset_token": code,
                "password": "a-new-password-456!",
                "password_confirm": "a-new-password-456!",
            },
        )
        assert response.status_code == 400

    def test_a_reset_token_is_single_use(self, api_client, school):
        api_client.post(reverse("v1:school_portal:password-reset-request"), {"email": school.email})
        code = latest_code(school.user, VerificationPurpose.PASSWORD_RESET)
        verified = api_client.post(
            reverse("v1:school_portal:password-reset-verify"),
            {"email": school.email, "code": code},
        )
        body = {
            "reset_token": verified.data["reset_token"],
            "password": "a-new-password-456!",
            "password_confirm": "a-new-password-456!",
        }

        first = api_client.post(reverse("v1:school_portal:password-reset-confirm"), body)
        second = api_client.post(reverse("v1:school_portal:password-reset-confirm"), body)

        assert first.status_code == 204
        assert second.status_code == 400


class TestPasswordChange:
    def test_a_signed_in_school_changes_its_own_password(self, school_client, school):
        response = school_client.post(
            reverse("v1:school_portal:password-change"),
            {"current_password": DEFAULT_PASSWORD, "new_password": "a-new-password-456!"},
        )

        assert response.status_code == 204
        school.user.refresh_from_db()
        assert school.user.check_password("a-new-password-456!")

    def test_the_current_password_has_to_be_right(self, school_client):
        response = school_client.post(
            reverse("v1:school_portal:password-change"),
            {"current_password": "wrong", "new_password": "a-new-password-456!"},
        )
        assert response.status_code == 400


class TestPurposeIsolation:
    def test_a_code_issued_for_one_purpose_does_not_work_for_another(self, api_client, school):
        VerificationService().issue(user=school.user, purpose=VerificationPurpose.PASSWORD_RESET)
        code = latest_code(school.user, VerificationPurpose.PASSWORD_RESET)

        # Same inbox, same six digits, different job.
        response = api_client.post(
            reverse("v1:school_portal:register-verify"),
            {"email": school.email, "code": code},
        )
        assert response.status_code == 400


def _user(email: str):
    from apps.users.models import User

    return User.objects.get(email=email)
