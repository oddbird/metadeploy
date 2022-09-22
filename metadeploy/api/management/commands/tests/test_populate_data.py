import pytest
from django.contrib.sites.models import Site
from django.core.management import call_command

from ....models import Product

PATCH_ROOT = "metadeploy.api.management.commands.populate_data"


@pytest.fixture
def patch_models(mocker):
    """
    Use this fixture so models don't actually create instances. Saves over 10 seconds!
    """
    mocker.patch(f"{PATCH_ROOT}.AllowedList")
    mocker.patch(f"{PATCH_ROOT}.Plan")
    mocker.patch(f"{PATCH_ROOT}.PlanSlug")
    mocker.patch(f"{PATCH_ROOT}.PlanTemplate")
    mocker.patch(f"{PATCH_ROOT}.Product")
    mocker.patch(f"{PATCH_ROOT}.ProductCategory")
    mocker.patch(f"{PATCH_ROOT}.ProductSlug")
    mocker.patch(f"{PATCH_ROOT}.Step")
    mocker.patch(f"{PATCH_ROOT}.Version")


@pytest.mark.django_db
def test_populate_data():
    assert Product.objects.count() == 0

    call_command("populate_data")

    assert Product.objects.count() == 35


@pytest.mark.django_db
def test_adjust_site(settings, patch_models):
    settings.DEBUG = True
    assert not Site.objects.filter(domain="localhost:8080").exists()

    call_command("populate_data")

    assert Site.objects.filter(
        domain="localhost:8080"
    ).exists(), (
        "Expected `populate_data` to adjust the default Site domain when DEBUG=True"
    )


@pytest.mark.django_db
def test_adjust_site__production(settings, patch_models):
    settings.DEBUG = False
    assert not Site.objects.filter(domain="localhost:8080").exists()

    call_command("populate_data")

    assert not Site.objects.filter(
        domain="localhost:8080"
    ).exists(), "Expected `populate_data` to NOT adjust the default Site domain when DEBUG=False"
