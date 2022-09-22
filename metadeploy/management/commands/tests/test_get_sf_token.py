from io import StringIO

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_promote_superuser__no_such_user():
    out = StringIO()
    call_command("get_sf_token", "test@example.com", stdout=out)

    assert "No such user." in out.getvalue()


@pytest.mark.django_db
def test_promote_superuser__success(user_factory):
    user_factory(email="test@example.com")
    out = StringIO()
    call_command("get_sf_token", "test@example.com", stdout=out)

    assert "TEST_TOKEN=" in out.getvalue()
    assert "TEST_TOKEN_SECRET=" in out.getvalue()
