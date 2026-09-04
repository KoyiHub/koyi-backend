"""The list envelope every paginated page is built on."""

import pytest
from django.urls import reverse

from apps.schools.factories import StudentFactory, TeacherFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def client(api_client):
    from rest_framework_simplejwt.tokens import RefreshToken

    teacher = TeacherFactory()
    for _ in range(3):
        StudentFactory(school=teacher.school)
    token = RefreshToken.for_user(teacher.user).access_token
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api_client


def test_the_envelope_carries_enough_for_numbered_controls(client):
    """`page` and `num_pages` are what let a client show "3 of 11".

    Without them the only honest control is previous/next, which is a poor fit
    for a roster of a few hundred children.
    """
    response = client.get(reverse("v1:teacher_portal:bank-question-list"))

    assert response.status_code == 200
    assert set(response.data) == {
        "count",
        "page",
        "num_pages",
        "page_size",
        "next",
        "previous",
        "results",
    }
    assert response.data["page"] == 1
    assert response.data["num_pages"] >= 1


def test_page_size_is_capped(client):
    """A client asking for everything at once does not get it."""
    response = client.get(reverse("v1:teacher_portal:bank-question-list"), {"page_size": 5000})
    assert response.data["page_size"] == 100
