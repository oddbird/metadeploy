import json
from io import StringIO
from unittest import mock

import pytest
from django.core.exceptions import ObjectDoesNotExist
from django.core.management import CommandError, call_command

from metadeploy.api.models import Product, ProductCategory


@pytest.fixture
def product():
    category = ProductCategory.objects.create(title="Test Category", order_key=10)

    title = "Test Product Title"
    short_description = "Keep it short."
    description = "This is slightly more descriptive."
    click_through_agreement = "# Please check the box below"
    error_message = "# This is an error message"
    return Product.objects.create(
        title=title,
        short_description=short_description,
        description=description,
        click_through_agreement=click_through_agreement,
        error_message=error_message,
        category=category,
    )


@pytest.mark.django_db
def test_extract_labels(product):
    """Test extract labels command."""
    out = StringIO()
    call_command("extract_labels", stdout=out)

    expected = {
        "Product": {
            str(product.id): {
                "title": {
                    "message": product.title,
                    "description": "The name of the product",
                },
                "short_description": {
                    "message": product.short_description,
                    "description": "Short description of the product",
                },
                "description": {
                    "message": product.description,
                    "description": "Description of the product",
                },
                "click_through_agreement": {
                    "message": product.click_through_agreement,
                    "description": "Users must check a box to agree to this legal text before running the installer",
                },
                "error_message": {
                    "message": product.error_message,
                    "description": "Message regarding what to do if an error occurs during installation",
                },
            }
        }
    }
    actual = out.getvalue()
    expected = json.dumps(expected) + "\n"
    assert actual == expected


@pytest.mark.django_db
@mock.patch("metadeploy.management.commands.extract_labels.getattr")
def test_error_handling(getattr, product):
    getattr.side_effect = ObjectDoesNotExist
    out = StringIO()

    call_command("extract_labels", stdout=out)

    out = out.getvalue()
    expected = '{"Product": {"' + str(product.id) + '": {}}}\n'
    assert out == expected


@pytest.mark.django_db
def test_multi_tenancy__ok(extra_site, monkeypatch):
    """Command should run if `DJANGO_SITE_ID` is set while `extra_site` is present"""
    monkeypatch.setenv("DJANGO_SITE_ID", str(extra_site.id))
    call_command("extract_labels")


@pytest.mark.django_db
def test_multi_tenancy__bad(extra_site):
    with pytest.raises(CommandError, match="Multiple Sites detected"):
        call_command("extract_labels")
        pytest.fail(
            "Expected command to fail if `DJANGO_SITE_ID` is not set while "
            "`extra_site` is present"
        )
