from unittest import mock

import pytest
from allauth.socialaccount.providers.oauth2.client import OAuth2Error
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import reverse
from sfdo_template_helpers.oauth2.salesforce.views import SalesforcePermissionsError

from ..views import custom_500_view, custom_permission_denied_view


@pytest.mark.django_db
@mock.patch("metadeploy.views.render")
def test_custom_permission_denied_view__sf_permissions(render):
    request = RequestFactory().get("path")
    exc = SalesforcePermissionsError("I'm sorry Dave.")
    custom_permission_denied_view(request, exc)

    assert (
        render.call_args[1]["context"]["JS_CONTEXT"]["error_message"]
        == "I'm sorry Dave."
    )


@pytest.mark.django_db
@mock.patch("metadeploy.views.render")
def test_custom_permission_denied_view__unknown_error(render):
    request = RequestFactory().get("path")
    exc = Exception("I'm sorry Dave.")
    custom_permission_denied_view(request, exc)

    assert (
        render.call_args[1]["context"]["JS_CONTEXT"]["error_message"]
        == "An internal error occurred while processing your request."
    )


@pytest.mark.django_db
@mock.patch("metadeploy.views.render")
def test_custom_500_view__ip_restricted_error(render):
    try:
        # raise this to populate info for
        # call to sys.exec_info() in the view
        raise OAuth2Error(
            'Error retrieving access token: b\'{"error":"invalid_grant","error_description":"ip restricted"}\''
        )
    except OAuth2Error:
        allow_list = "0.0.0.1, 0.0.0.2, 0.0.0.3"
        with mock.patch("metadeploy.views.IP_RESTRICTED_MESSAGE", allow_list):
            factory = RequestFactory()
            request = factory.get("/accounts/salesforce/login/callback/")
            request.user = AnonymousUser()
            custom_500_view(request)

    assert allow_list == render.call_args[1]["context"]["JS_CONTEXT"]["error_message"]


@pytest.mark.django_db
def test_multi_tenancy_middleware_unknown_site(client, settings):
    url = reverse("admin:login")
    response = client.get(url)
    assert response.status_code == 200

    settings.ALLOWED_HOSTS += ["not-a-known-site.com"]
    response = client.get(url, SERVER_NAME="not-a-known-site.com")
    assert (
        response.status_code == 404
    ), "Expected a 404 when visiting a host that is allowed but doesn't have a matching Site record in the DB"


@pytest.mark.django_db
class TestSetSite:
    def test_superuser_required(self, client):
        client.user.is_staff = True
        client.user.save()
        url = reverse("set_site")

        response = client.get(url)

        assert response.status_code == 302
        assert response["location"] == f"{reverse('admin:login')}?next={url}"

    @pytest.mark.parametrize("site_id", ("", "abc", 123))
    def test_site_id_bad(self, client, site_id):
        client.user.is_staff = client.user.is_superuser = True
        client.user.save()

        response = client.get(reverse("set_site"), {"site_id": site_id})

        assert response.status_code == 404

    def test_rewrite_next(self, client, extra_site):
        client.user.is_staff = client.user.is_superuser = True
        client.user.save()

        response = client.get(
            reverse("set_site"),
            {"site_id": extra_site.id, "next": "/admin/APP/MODEL/2/change/"},
        )

        assert response.status_code == 302
        assert (
            response["location"] == "/admin/APP/MODEL/"
        ), "Expected a redirection to the list page of APP.MODEL instead of a detail page"

    def test_unsafe_next(self, client, extra_site):
        client.user.is_staff = client.user.is_superuser = True
        client.user.save()

        response = client.get(
            reverse("set_site"),
            {"site_id": extra_site.id, "next": "https://external.com"},
        )

        assert response.status_code == 302
        assert (
            response["location"] == "/admin/"
        ), "Expected a redirection to the admin root instead of an external site"

    def test_admin(self, client, product_category, extra_site):
        client.user.is_staff = client.user.is_superuser = True
        client.user.save()
        url = reverse("admin:api_productcategory_change", args=[product_category.pk])
        response = client.get(url)
        assert response.status_code == 200

        client.get(reverse("set_site"), {"site_id": extra_site.id})

        response = client.get(url)
        assert (
            response.status_code == 302
        ), "Original object should redirect to the admin root after manually selecting a site in the admin"
        assert response["location"] == reverse("admin:index")

    @pytest.mark.parametrize(
        "url_name", ("productcategory-detail", "admin_rest:productcategory-detail")
    )
    def test_non_admin(self, client, product_category, extra_site, url_name):
        client.user.is_staff = client.user.is_superuser = True
        client.user.save()
        url = reverse(url_name, args=[product_category.pk])
        ids = (product_category.id, str(product_category.id))
        response = client.get(url)
        assert response.data["id"] in ids

        client.get(reverse("set_site"), {"site_id": extra_site.id})

        response = client.get(url)
        assert (
            response.data["id"] in ids
        ), f"Visiting the set-site view shouldn't affect site-filtering for {url}"
