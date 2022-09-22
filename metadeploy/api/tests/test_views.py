from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import django_rq
import pytest
from django.core.management import call_command
from django.urls import reverse

from metadeploy.conftest import format_timestamp
from metadeploy.multitenancy import override_current_site_id

from ..models import SUPPORTED_ORG_TYPES, Job, Plan, PreflightResult, ScratchOrg, Token


def test_openapi_schema(tmp_path):
    schema_file = Path("docs/api/schema.yml")
    temp_file = tmp_path / "schema.yml"

    cmd = "spectacular --file {} --validate --fail-on-warn"
    call_command(*cmd.format(temp_file).split())

    assert schema_file.read_text() == temp_file.read_text(), (
        "The OpenAPI schema is outdated. Run `python manage.py "
        f"{cmd.format(schema_file)}` and commit the results."
    )


@pytest.mark.django_db
def test_token_auth_multi_tenancy(anon_client, user_factory, extra_site):
    user = user_factory()
    url = reverse("user")
    token1 = Token.objects.create(user=user)
    with override_current_site_id(extra_site.id):
        token2 = Token.objects.create(user=user)

    response = anon_client.get(url, HTTP_AUTHORIZATION=f"Token {token1}")
    assert response.status_code == 200, "Token 1 should work on the default site"
    response = anon_client.get(url, HTTP_AUTHORIZATION=f"Token {token2}")
    assert response.status_code == 401, "Token 2 should not work on the default site"

    response = anon_client.get(
        url, SERVER_NAME=extra_site.domain, HTTP_AUTHORIZATION=f"Token {token2}"
    )
    assert response.status_code == 200, "Token 2 should work on the extra site"
    response = anon_client.get(
        url, SERVER_NAME=extra_site.domain, HTTP_AUTHORIZATION=f"Token {token1}"
    )
    assert response.status_code == 401, "Token 1 should not work on the extra site"


@pytest.mark.django_db
class TestUserView:
    def test_ok(self, client):
        response = client.get(reverse("user"))

        assert response.status_code == 200
        assert response.json()["username"].endswith("@example.com")

    def test_multi_tenancy(self, client, extra_site):
        response = client.get(reverse("user"), SERVER_NAME=extra_site.domain)

        assert (
            response.status_code == 200
        ), "Users should be able to authenticate on all Sites using sessions"


@pytest.mark.django_db
class TestObtainTokenView:
    def test_multi_tenancy(self, anon_client, user_factory, extra_site):
        url = reverse("token")
        user = user_factory()
        data = {"username": user.username, "password": "foobar"}
        token1 = Token.objects.create(user=user)
        with override_current_site_id(extra_site.id):
            token2 = Token.objects.create(user=user)

        response = anon_client.post(url, data=data)
        assert response.data.get("token") == token1.key

        response = anon_client.post(url, data=data, SERVER_NAME=extra_site.domain)
        assert response.data.get("token") == token2.key


@pytest.mark.django_db
class TestJobViewSet:
    def test_job__cannot_see(self, client, job_factory):
        job = job_factory(org_id="00Dxxxxxxxxxxxxxxx")
        response = client.get(reverse("job-detail", kwargs={"pk": job.id}))

        assert response.status_code == 404
        assert response.json() == {"detail": "Not found."}

    def test_job__multi_tenancy(self, job_factory, assert_multi_tenancy):
        assert_multi_tenancy.client.user.is_staff = True
        assert_multi_tenancy.client.user.save()
        job = job_factory(org_id="00Dxxxxxxxxxxxxxxx")

        assert_multi_tenancy(reverse("job-detail", args=[job.id]))

    def test_job__is_staff(self, client, user_factory, job_factory):
        staff_user = user_factory(is_staff=True)
        client.force_login(staff_user)
        job = job_factory(org_id="00Dxxxxxxxxxxxxxxx")
        response = client.get(reverse("job-detail", kwargs={"pk": job.id}))

        assert response.status_code == 200
        assert response.json() == {
            "id": str(job.id),
            "creator": {"username": job.user.sf_username, "is_staff": False},
            "plan": str(job.plan.id),
            "steps": [],
            "instance_url": "https://example.com",
            "org_id": "00Dxxxxxxxxxxxxxxx",
            "results": {},
            "created_at": format_timestamp(job.created_at),
            "enqueued_at": None,
            "job_id": None,
            "status": "started",
            "org_name": "Sample Org",
            "org_type": "",
            "is_production_org": False,
            "error_count": 0,
            "warning_count": 0,
            "is_public": False,
            "user_can_edit": False,
            "message": "",
            "error_message": "",
            "edited_at": format_timestamp(job.edited_at),
            "product_slug": str(job.plan.version.product.slug),
            "version_label": str(job.plan.version.label),
            "version_is_most_recent": True,
            "plan_slug": str(job.plan.slug),
        }

    def test_job__your_own(self, client, user_factory, job_factory):
        user = user_factory(org_name="Secret Org")
        client.force_login(user)
        job = job_factory(user=user, org_id=client.user.org_id)
        response = client.get(reverse("job-detail", kwargs={"pk": job.id}))

        assert response.status_code == 200
        assert response.json() == {
            "id": str(job.id),
            "creator": {"username": job.user.sf_username, "is_staff": False},
            "plan": str(job.plan.id),
            "steps": [],
            "instance_url": "https://example.com",
            "org_id": "00Dxxxxxxxxxxxxxxx",
            "results": {},
            "created_at": format_timestamp(job.created_at),
            "enqueued_at": None,
            "job_id": None,
            "status": "started",
            "org_name": "Secret Org",
            "org_type": "",
            "is_production_org": False,
            "error_count": 0,
            "warning_count": 0,
            "is_public": False,
            "user_can_edit": True,
            "message": "",
            "error_message": "",
            "edited_at": format_timestamp(job.edited_at),
            "product_slug": str(job.plan.version.product.slug),
            "version_label": str(job.plan.version.label),
            "version_is_most_recent": True,
            "plan_slug": str(job.plan.slug),
        }

    def test_job__is_public(self, client, job_factory):
        job = job_factory(is_public=True, org_id="00Dxxxxxxxxxxxxxxx")
        response = client.get(reverse("job-detail", kwargs={"pk": job.id}))

        assert response.status_code == 200
        assert response.json() == {
            "id": str(job.id),
            "creator": None,
            "plan": str(job.plan.id),
            "instance_url": None,
            "org_id": None,
            "steps": [],
            "results": {},
            "created_at": format_timestamp(job.created_at),
            "enqueued_at": None,
            "job_id": None,
            "status": "started",
            "org_name": None,
            "org_type": "",
            "is_production_org": False,
            "error_count": 0,
            "warning_count": 0,
            "is_public": True,
            "user_can_edit": False,
            "message": "",
            "error_message": "",
            "edited_at": format_timestamp(job.edited_at),
            "product_slug": str(job.plan.version.product.slug),
            "version_label": str(job.plan.version.label),
            "version_is_most_recent": True,
            "plan_slug": str(job.plan.slug),
        }

    def test_job__is_public_anon(self, anon_client, job_factory):
        job = job_factory(is_public=True, org_id="00Dxxxxxxxxxxxxxxx")
        url = reverse("job-detail", kwargs={"pk": job.id})
        response = anon_client.get(url)

        assert response.status_code == 200
        assert response.json() == {
            "id": str(job.id),
            "creator": None,
            "plan": str(job.plan.id),
            "instance_url": None,
            "org_id": None,
            "steps": [],
            "results": {},
            "error_count": 0,
            "warning_count": 0,
            "created_at": format_timestamp(job.created_at),
            "enqueued_at": None,
            "job_id": None,
            "status": "started",
            "org_name": None,
            "org_type": "",
            "is_production_org": False,
            "is_public": True,
            "user_can_edit": False,
            "message": "",
            "error_message": "",
            "edited_at": format_timestamp(job.edited_at),
            "product_slug": str(job.plan.version.product.slug),
            "version_label": str(job.plan.version.label),
            "version_is_most_recent": True,
            "plan_slug": str(job.plan.slug),
        }

    def test_create_job(self, client, plan_factory, preflight_result_factory):
        plan = plan_factory()
        preflight_result_factory(
            plan=plan,
            user=client.user,
            status=PreflightResult.Status.complete,
            org_id=client.user.org_id,
        )
        data = {"plan": str(plan.id), "steps": []}
        response = client.post(reverse("job-list"), data=data)

        assert response.status_code == 201
        assert response.json()["org_type"] == "Developer Edition"
        assert response.json()["org_name"] == "Sample Org"

    def test_destroy_job(self, client, job_factory):
        job = job_factory(user=client.user, org_id=client.user.org_id)
        response = client.delete(reverse("job-detail", kwargs={"pk": job.id}))

        assert response.status_code == 204
        assert Job.objects.filter(id=job.id).exists()

    def test_destroy_job__bad_user(self, client, job_factory):
        job = job_factory(is_public=True, org_id="00Dxxxxxxxxxxxxxxx")
        response = client.delete(reverse("job-detail", kwargs={"pk": job.id}))

        assert response.status_code == 403
        assert Job.objects.filter(id=job.id).exists()

    def test_queryset_anonymous_scratch_org(
        self, anon_client, job_factory, scratch_org_factory
    ):
        org_id = "00Dyyyyyyyyyyyyyyy"
        uuid = str(uuid4())
        job = job_factory(
            is_public=False,
            org_id=org_id,
        )
        scratch_org_factory(
            status=ScratchOrg.Status.complete,
            org_id=org_id,
            uuid=uuid,
        )
        url = reverse("job-detail", kwargs={"pk": job.id})
        session = anon_client.session
        session["scratch_org_id"] = uuid
        session.save()
        response = anon_client.get(url)

        assert response.status_code == 200
        assert response.json() == {
            "id": str(job.id),
            "creator": None,
            "plan": str(job.plan.id),
            "instance_url": None,
            "org_id": None,
            "steps": [],
            "results": {},
            "error_count": 0,
            "warning_count": 0,
            "created_at": format_timestamp(job.created_at),
            "enqueued_at": None,
            "job_id": None,
            "status": "started",
            "org_name": None,
            "org_type": "",
            "is_production_org": False,
            "is_public": False,
            "user_can_edit": False,
            "message": "",
            "error_message": "",
            "edited_at": format_timestamp(job.edited_at),
            "product_slug": str(job.plan.version.product.slug),
            "version_label": str(job.plan.version.label),
            "version_is_most_recent": True,
            "plan_slug": str(job.plan.slug),
        }


@pytest.mark.django_db
class TestProductCategoryViewSet:
    def test_multi_tenancy(self, product_category, assert_multi_tenancy):
        assert_multi_tenancy(
            reverse("productcategory-detail", args=[product_category.pk])
        )


@pytest.mark.django_db
class TestProductViewSet:
    def test_ok(self, client, allowed_list_factory, product_factory, version_factory):
        allowed_list = allowed_list_factory(org_type=["Developer"])
        product = product_factory(visible_to=allowed_list)
        version_factory(product=product)
        response = client.get(reverse("product-list"))

        assert response.status_code == 200
        assert response.json() == {
            "count": 0,
            "results": [],
            "previous": None,
            "next": None,
        }

    def test_multi_tenancy(self, version, assert_multi_tenancy):
        assert_multi_tenancy.client.logout()
        assert_multi_tenancy(reverse("product-detail", args=[version.product_id]))

    def test_unlisted(self, client, product_factory, version_factory):
        product1 = product_factory()
        version_factory(product=product1)
        product2 = product_factory(is_listed=False)
        version_factory(product=product2)

        response = client.get(reverse("product-get-one"), {"slug": product2.slug})

        assert response.status_code == 200
        assert response.json()["id"] == product2.id

    def test_product_search(self, anon_client, version_factory):
        url = reverse("product-list")
        # Create Versions because Products need at least one Version to show up
        version_factory(product__title="My custom title")
        version_factory(product__tags=["custom tag", "another tag"])

        response = anon_client.get(url, {"search": "CUSTOM TITLE"})
        assert response.data["count"] == 1
        assert (
            response.data["results"][0]["title"] == "My custom title"
        ), "Expected Products to be searchable by `title`"

        response = anon_client.get(url, {"search": "CUSTOM TAG"})
        assert response.data["count"] == 1
        assert response.data["results"][0]["tags"] == [
            "custom tag",
            "another tag",
        ], "Expected Products to be searchable by `tags`"


@pytest.mark.django_db
class TestVersionViewSet:
    def test_ok(self, client, version_factory):
        version = version_factory()
        response = client.get(reverse("version-detail", kwargs={"pk": version.id}))

        assert response.status_code == 200
        assert response.json() == {
            "id": str(version.id),
            "product": str(version.product.id),
            "label": version.label,
            "description": "A sample version.",
            "created_at": format_timestamp(version.created_at),
            "primary_plan": None,
            "secondary_plan": None,
            "is_listed": True,
        }

    def test_multi_tenancy(self, version, assert_multi_tenancy):
        assert_multi_tenancy(reverse("version-detail", args=[version.pk]))

    def test_additional_plans(self, client, plan_factory, settings):
        plan = plan_factory(tier=Plan.Tier.additional)
        response = client.get(
            reverse("version-additional-plans", kwargs={"pk": plan.version.id})
        )

        assert response.status_code == 200
        assert response.json() == [
            {
                "id": str(plan.id),
                "title": str(plan.title),
                "version": str(plan.version.id),
                "preflight_message": "",
                "tier": "additional",
                "slug": str(plan.slug),
                "old_slugs": [],
                "order_key": 0,
                "steps": [],
                "is_allowed": True,
                "is_listed": True,
                "not_allowed_instructions": None,
                "requires_preflight": False,
                "average_duration": None,
                "supported_orgs": "Persistent",
                "scratch_org_duration": settings.SCRATCH_ORG_DURATION_DAYS,
            }
        ]

    def test_unlisted(self, client, product_factory, version_factory):
        product = product_factory()
        version_factory(product=product)
        version = version_factory(product=product, is_listed=False)

        response = client.get(
            reverse("version-get-one"),
            {"label": version.label, "product": product.slug},
        )

        assert response.status_code == 200
        assert response.json()["id"] == version.id


@pytest.mark.django_db
class TestPlanViewSet:
    def test_ok(self, client, plan_factory, settings):
        plan = plan_factory()
        response = client.get(reverse("plan-detail", kwargs={"pk": plan.id}))

        assert response.status_code == 200
        assert response.json() == {
            "id": str(plan.id),
            "title": str(plan.title),
            "version": str(plan.version.id),
            "preflight_message": "",
            "requires_preflight": False,
            "tier": "primary",
            "slug": str(plan.slug),
            "old_slugs": [],
            "order_key": 0,
            "steps": [],
            "is_allowed": True,
            "is_listed": True,
            "not_allowed_instructions": None,
            "average_duration": None,
            "supported_orgs": "Persistent",
            "scratch_org_duration": settings.SCRATCH_ORG_DURATION_DAYS,
        }

    def test_multi_tenancy(self, plan, assert_multi_tenancy):
        assert_multi_tenancy(reverse("plan-detail", args=[plan.pk]))

    def test_not_visible(self, client, allowed_list_factory, plan_factory, settings):
        allowed_list = allowed_list_factory(description="Sample instructions.")
        plan = plan_factory(visible_to=allowed_list)
        response = client.get(reverse("plan-detail", kwargs={"pk": plan.id}))

        assert response.status_code == 200
        assert response.json() == {
            "id": str(plan.id),
            "title": str(plan.title),
            "version": str(plan.version.id),
            "preflight_message": None,
            "requires_preflight": False,
            "tier": "primary",
            "slug": str(plan.slug),
            "old_slugs": [],
            "order_key": 0,
            "steps": None,
            "is_allowed": False,
            "is_listed": True,
            "not_allowed_instructions": "<p>Sample instructions.</p>",
            "average_duration": None,
            "supported_orgs": "Persistent",
            "scratch_org_duration": settings.SCRATCH_ORG_DURATION_DAYS,
        }

    def test_visible(
        self,
        client,
        allowed_list_factory,
        allowed_list_org_factory,
        plan_factory,
        user_factory,
        settings,
    ):
        allowed_list = allowed_list_factory(description="Sample instructions.")
        allowed_list_org = allowed_list_org_factory(allowed_list=allowed_list)
        plan = plan_factory(visible_to=allowed_list)
        user = user_factory()
        social_account = user.socialaccount_set.all()[0]
        social_account.extra_data["organization_id"] = allowed_list_org.org_id
        social_account.save()
        client.force_login(user)
        response = client.get(reverse("plan-detail", kwargs={"pk": plan.id}))

        assert response.status_code == 200
        assert response.json() == {
            "id": str(plan.id),
            "title": str(plan.title),
            "version": str(plan.version.id),
            "preflight_message": "",
            "requires_preflight": False,
            "tier": "primary",
            "slug": str(plan.slug),
            "old_slugs": [],
            "order_key": 0,
            "steps": [],
            "is_allowed": True,
            "is_listed": True,
            "not_allowed_instructions": "<p>Sample instructions.</p>",
            "average_duration": None,
            "supported_orgs": "Persistent",
            "scratch_org_duration": settings.SCRATCH_ORG_DURATION_DAYS,
        }

    def test_visible_superuser(
        self, client, allowed_list_factory, plan_factory, user_factory, settings
    ):
        allowed_list = allowed_list_factory(description="Sample instructions.")
        plan = plan_factory(visible_to=allowed_list)
        user = user_factory(is_superuser=True)
        client.force_login(user)
        response = client.get(reverse("plan-detail", kwargs={"pk": plan.id}))

        assert response.status_code == 200
        assert response.json() == {
            "id": str(plan.id),
            "title": str(plan.title),
            "version": str(plan.version.id),
            "preflight_message": "",
            "requires_preflight": False,
            "tier": "primary",
            "slug": str(plan.slug),
            "old_slugs": [],
            "order_key": 0,
            "steps": [],
            "is_allowed": True,
            "is_listed": True,
            "not_allowed_instructions": "<p>Sample instructions.</p>",
            "average_duration": None,
            "supported_orgs": "Persistent",
            "scratch_org_duration": settings.SCRATCH_ORG_DURATION_DAYS,
        }

    def test_preflight_post__anon(self, anon_client, plan_factory, scratch_org_factory):
        uuid = str(uuid4())
        plan = plan_factory()
        scratch_org_factory(
            uuid=uuid,
            plan=plan,
            status=ScratchOrg.Status.complete,
            org_id="00D" + 15 * "x",  # 18 char org_id
            config={
                "instance_url": "instance_url",
                "org_id": "00D" + 12 * "x",  # 15 char org_id from platform
                "username": "username",
                "access_token": "token",
                "refresh_token": "refresh token",
            },
        )
        session = anon_client.session
        session["scratch_org_id"] = uuid
        session.save()
        response = anon_client.post(reverse("plan-preflight", kwargs={"pk": plan.id}))

        assert response.status_code == 201

    def test_preflight_post__anon__bad(self, anon_client, plan_factory):
        uuid = str(uuid4())
        plan = plan_factory()
        session = anon_client.session
        session["scratch_org_id"] = uuid
        session.save()
        response = anon_client.post(reverse("plan-preflight", kwargs={"pk": plan.id}))

        assert response.status_code == 403

    def test_preflight_post(self, client, plan_factory):
        plan = plan_factory()
        response = client.post(reverse("plan-preflight", kwargs={"pk": plan.id}))

        assert response.status_code == 201

    def test_preflight_get__good(self, client, plan_factory, preflight_result_factory):
        plan = plan_factory()
        preflight = preflight_result_factory(
            plan=plan,
            user=client.user,
            org_id=client.user.org_id,
        )
        response = client.get(reverse("plan-preflight", kwargs={"pk": plan.id}))

        assert response.status_code == 200
        assert response.json() == {
            "id": str(preflight.id),
            "instance_url": client.user.instance_url,
            "org_id": "00Dxxxxxxxxxxxxxxx",
            "plan": str(plan.id),
            "created_at": format_timestamp(preflight.created_at),
            "is_valid": True,
            "status": "started",
            "results": {},
            "error_count": 0,
            "warning_count": 0,
            "is_ready": False,
            "user": str(client.user.id),
            "edited_at": format_timestamp(preflight.edited_at),
        }

    def test_preflight_get__anon(
        self, anon_client, plan_factory, scratch_org_factory, preflight_result_factory
    ):
        uuid = str(uuid4())
        plan = plan_factory()
        org = scratch_org_factory(
            uuid=uuid,
            plan=plan,
            status=ScratchOrg.Status.complete,
            org_id="org_id",
            config={
                "instance_url": "instance_url",
                "org_id": "org_id",
                "username": "username",
                "access_token": "token",
                "refresh_token": "refresh token",
            },
        )
        preflight = preflight_result_factory(
            plan=plan,
            user=None,
            org_id=org.org_id,
        )
        session = anon_client.session
        session["scratch_org_id"] = uuid
        session.save()
        response = anon_client.get(reverse("plan-preflight", kwargs={"pk": plan.id}))

        assert response.status_code == 200
        assert response.json()["id"] == str(preflight.id)

    def test_preflight_get__bad(self, anon_client, plan_factory):
        plan = plan_factory()
        response = anon_client.get(reverse("plan-preflight", kwargs={"pk": plan.id}))

        assert response.status_code == 404

    def test_preflight_get__bad__anon(self, client, plan_factory):
        plan = plan_factory()
        response = client.get(reverse("plan-preflight", kwargs={"pk": plan.id}))

        assert response.status_code == 404

    def test_preflight_post__unallowed(
        self, client, plan_factory, allowed_list_factory
    ):
        allowed_list = allowed_list_factory()
        plan = plan_factory(visible_to=allowed_list)
        response = client.post(reverse("plan-preflight", kwargs={"pk": plan.id}))

        assert response.status_code == 403

    def test_preflight__where_plan_not_listed(
        self, client, plan_factory, preflight_result_factory
    ):
        plan = plan_factory()
        plan.is_listed = False
        plan.save()

        preflight_result_factory(
            plan=plan,
            user=client.user,
            org_id=client.user.org_id,
        )

        get_response = client.get(reverse("plan-preflight", kwargs={"pk": plan.id}))
        assert get_response.status_code == 200

        post_response = client.post(reverse("plan-preflight", kwargs={"pk": plan.id}))
        assert post_response.status_code == 201

    def test_scratch_org_get(self, client, plan_factory, scratch_org_factory):
        plan = plan_factory()
        uuid = str(uuid4())
        scratch_org_factory(
            uuid=uuid,
            plan=plan,
            config={
                "instance_url": "instance_url",
                "org_id": "org_id",
                "username": "username",
                "access_token": "token",
                "refresh_token": "refresh token",
            },
        )
        session = client.session
        session["scratch_org_id"] = uuid
        session.save()

        response = client.get(reverse("plan-scratch-org", kwargs={"pk": str(plan.id)}))
        assert response.status_code == 200

    def test_scratch_org_get__missing(self, client, plan_factory, scratch_org_factory):
        plan = plan_factory()
        uuid = str(uuid4())
        scratch_org_factory(
            uuid=uuid,
            config={
                "instance_url": "instance_url",
                "org_id": "org_id",
                "username": "username",
                "access_token": "token",
                "refresh_token": "refresh token",
            },
        )
        session = client.session
        session["scratch_org_id"] = uuid
        session.save()

        response = client.get(reverse("plan-scratch-org", kwargs={"pk": str(plan.id)}))
        assert response.status_code == 404

    def test_scratch_org_get__invalid_uuid(self, client, plan_factory):
        plan = plan_factory()
        session = client.session
        session["scratch_org_id"] = "invalid"
        session.save()

        response = client.get(reverse("plan-scratch-org", kwargs={"pk": str(plan.id)}))
        assert response.status_code == 404

    def test_scratch_org_post__no_devhub_username(self, client, plan_factory, settings):
        settings.DEVHUB_USERNAME = None
        plan = plan_factory()
        response = client.post(reverse("plan-scratch-org", kwargs={"pk": str(plan.id)}))
        assert response.status_code == 501

    def test_scratch_org_post__invalid_plan(self, client, plan_factory, settings):
        settings.DEVHUB_USERNAME = "devhub@example.com"
        plan = plan_factory()
        response = client.post(reverse("plan-scratch-org", kwargs={"pk": str(plan.id)}))
        assert response.status_code == 409

    def test_scratch_org_post__bad_data(self, client, plan_factory, settings):
        settings.DEVHUB_USERNAME = "devhub@example.com"
        plan = plan_factory(supported_orgs=SUPPORTED_ORG_TYPES.Scratch)
        response = client.post(reverse("plan-scratch-org", kwargs={"pk": str(plan.id)}))
        assert response.status_code == 400

    def test_scratch_org_post__queue_full(self, client, plan_factory, settings):
        queue = django_rq.get_queue("default")
        queue.empty()
        settings.DEVHUB_USERNAME = "devhub@example.com"
        plan = plan_factory(supported_orgs=SUPPORTED_ORG_TYPES.Scratch)
        with patch("metadeploy.api.views.django_rq") as djq:
            # Return something longer than the max queue length:
            djq.get_queue.return_value = [None] * 20
            response = client.post(
                reverse("plan-scratch-org", kwargs={"pk": str(plan.id)}),
                {"email": "test@example.com"},
            )
            assert response.status_code == 503

    def test_scratch_org_post__good(self, client, plan_factory, settings):
        queue = django_rq.get_queue("default")
        queue.empty()
        settings.DEVHUB_USERNAME = "devhub@example.com"
        plan = plan_factory(supported_orgs=SUPPORTED_ORG_TYPES.Scratch)
        with patch(
            "metadeploy.api.jobs.create_scratch_org_job"
        ) as create_scratch_org_job:
            uuid = "00000000-0000-0000-0000-000000000000"
            create_scratch_org_job.delay.return_value = MagicMock(
                id=uuid, enqueued_at=datetime(2020, 9, 3, 14, 0)
            )
            response = client.post(
                reverse("plan-scratch-org", kwargs={"pk": str(plan.id)}),
                {"email": "test@example.com"},
            )
            assert response.status_code == 202
            assert create_scratch_org_job.delay.called

    def test_unlisted(
        self,
        client,
        product_factory,
        version_factory,
        plan_template_factory,
        plan_factory,
    ):
        product = product_factory()
        version = version_factory(product=product)
        plan_template1 = plan_template_factory(product=product)
        plan_template2 = plan_template_factory(product=product)
        plan_factory(version=version, plan_template=plan_template1)
        plan = plan_factory(
            version=version,
            plan_template=plan_template2,
            is_listed=False,
            tier="additional",
        )

        response = client.get(
            reverse("plan-get-one"),
            {"slug": plan.slug, "version": str(version.id), "product": str(product.id)},
        )

        assert response.status_code == 200
        assert response.json()["id"] == plan.id

    def test_unlisted__missing_param(
        self,
        client,
        product_factory,
        version_factory,
        plan_template_factory,
        plan_factory,
    ):
        product = product_factory()
        version = version_factory(product=product)
        plan_template1 = plan_template_factory(product=product)
        plan_template2 = plan_template_factory(product=product)
        plan_factory(version=version, plan_template=plan_template1)
        plan = plan_factory(
            version=version,
            plan_template=plan_template2,
            is_listed=False,
            tier="additional",
        )

        response = client.get(
            reverse("plan-get-one"), {"slug": plan.slug, "product": str(product.id)}
        )

        assert response.status_code == 404

    def test_unlisted__multiple(
        self,
        client,
        product_factory,
        version_factory,
        plan_template_factory,
        plan_factory,
    ):
        # If there are multiple plans for the same version/plan template,
        # we expect to get the most recently created one.
        product = product_factory()
        version = version_factory(product=product)
        plan_template = plan_template_factory(product=product)
        plan_factory(
            version=version,
            plan_template=plan_template,
            is_listed=False,
            tier="additional",
        )
        plan = plan_factory(
            version=version,
            plan_template=plan_template,
            is_listed=False,
            tier="additional",
        )

        response = client.get(
            reverse("plan-get-one"),
            {"slug": plan.slug, "version": str(version.id), "product": str(product.id)},
        )

        assert response.status_code == 200
        assert response.json()["id"] == plan.id


@pytest.mark.django_db
class TestOrgViewSet:
    def test_get_job__anonymous(self, anon_client, job_factory, plan_factory):
        plan = plan_factory()
        job_factory(plan=plan)
        response = anon_client.get(reverse("org-list"))

        assert response.status_code == 200
        assert response.json() == {}

    def test_get_job__uuid(
        self, anon_client, job_factory, plan_factory, scratch_org_factory
    ):
        uuid = str(uuid4())
        plan = plan_factory()
        org = scratch_org_factory(
            plan=plan,
            uuid=uuid,
            org_id="00Dxxxxxxxxxxxxxxx",
            status=ScratchOrg.Status.complete,
        )
        job = job_factory(plan=plan, org_id=org.org_id)
        session = anon_client.session
        session["scratch_org_id"] = uuid
        session.save()
        response = anon_client.get(reverse("org-list"))

        assert response.json()[org.org_id]["current_job"]["id"] == str(job.id)
        assert response.json()[org.org_id]["current_preflight"] is None

    def test_get_job(self, client, job_factory, plan_factory):
        plan = plan_factory()
        job = job_factory(
            plan=plan,
            org_id=client.user.org_id,
        )
        response = client.get(reverse("org-list"))

        assert response.json()[client.user.org_id]["current_job"]["id"] == str(job.id)
        assert response.json()[client.user.org_id]["current_preflight"] is None

    def test_get_preflight(self, client, preflight_result_factory, plan_factory):
        plan = plan_factory()
        preflight = preflight_result_factory(
            plan=plan,
            user=None,
            org_id=client.user.org_id,
        )
        response = client.get(reverse("org-list"))

        assert response.json()[client.user.org_id]["current_job"] is None
        assert response.json()[client.user.org_id]["current_preflight"] == str(
            preflight.id
        )

    def test_get_none(self, anon_client):
        response = anon_client.get(reverse("org-list"))

        assert response.json() == {}


@pytest.mark.django_db
class TestScratchOrgView:
    def test_redirect__good(self, anon_client, scratch_org_factory):
        with ExitStack() as stack:
            uuid = str(uuid4())
            scratch_org = scratch_org_factory(
                status=ScratchOrg.Status.complete,
                uuid=uuid,
            )
            session = anon_client.session
            session["scratch_org_id"] = uuid
            session.save()

            get_login_url = stack.enter_context(
                patch("metadeploy.api.models.ScratchOrg.get_login_url")
            )
            get_login_url.return_value = "https://example.com"
            url = reverse("scratch-org-redirect", kwargs={"pk": str(scratch_org.id)})
            response = anon_client.get(url)

            assert response.status_code == 302

    def test_redirect__bad(self, anon_client, scratch_org_factory):
        scratch_org = scratch_org_factory()

        url = reverse("scratch-org-redirect", kwargs={"pk": str(scratch_org.id)})
        response = anon_client.get(url)

        assert response.status_code == 404
