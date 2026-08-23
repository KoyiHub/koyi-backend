import pytest
from django.urls import reverse


def test_healthz_is_public(client):
    response = client.get(reverse("health-check"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_readyz_reports_database(client):
    response = client.get(reverse("readiness-check"))

    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "ok"


def test_request_id_header_is_echoed(client):
    response = client.get(reverse("health-check"), headers={"x-request-id": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


def test_request_id_is_generated_when_absent(client):
    response = client.get(reverse("health-check"))

    assert response.headers["X-Request-ID"]
