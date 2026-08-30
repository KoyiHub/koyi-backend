from django.core import mail
from django.core.signing import TimestampSigner

from apps.users.factories import UserFactory


def _verification_token_for(user):
    return TimestampSigner().sign(str(user.pk))


def test_school_registration_sends_verification_email(api_client):
    response = api_client.post(
        "/api/v1/schools/register/",
        {
            "email": "new-school@example.com",
            "password": "A-strong-password-123",
            "password_confirm": "A-strong-password-123",
            "name": "North Ridge Academy",
            "abbreviation": "nra",
            "class_system": "grade",
        },
    )

    assert response.status_code == 201
    assert response.data["email_verified"] is False
    assert len(mail.outbox) == 1
    assert "verify" in mail.outbox[0].subject.lower()


def test_school_email_verification_confirms_account(api_client, db):
    user = UserFactory(email_verified=False)
    token = _verification_token_for(user)

    response = api_client.get("/api/v1/schools/verify-email/", {"token": token})

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.email_verified is True


def test_verify_email_rejects_invalid_token(api_client, db):
    response = api_client.get("/api/v1/schools/verify-email/", {"token": "bad-token"})

    assert response.status_code == 400


def test_resend_verification_email_is_throttled(api_client, db):
    user = UserFactory(email_verified=False)

    first = api_client.post("/api/v1/schools/verify-email/resend/", {"email": user.email})
    second = api_client.post("/api/v1/schools/verify-email/resend/", {"email": user.email})

    assert first.status_code == 200
    assert second.status_code == 429
    assert len(mail.outbox) == 1
