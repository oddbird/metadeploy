from uuid import uuid4

import pytest
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.sites.models import Site
from django.utils import timezone

from ..api.models import Job, PreflightResult, ScratchOrg
from ..api.push import (
    job_started,
    notify_org_changed,
    notify_org_result_changed,
    notify_post_job,
    preflight_completed,
    user_token_expired,
)
from ..api.serializers import JobSerializer, OrgSerializer, PreflightResultSerializer
from ..consumers import get_language_from_scope, user_context
from ..routing import application


def test_get_language_from_scope():
    scope = {"headers": ((b"accept-language", b"de"),)}
    lang = get_language_from_scope(scope)
    assert lang == "de"


def test_get_language_from_scope__default():
    assert (
        get_language_from_scope({"headers": ((b"accept-language", b"*"),)}) == "en-us"
    )
    assert (
        get_language_from_scope({"headers": ((b"accept-language", b"xx"),)}) == "en-us"
    )


class Session(dict):
    def __init__(self, **kwargs):
        self.update(kwargs)

    def save(self):
        pass


@pytest.fixture
async def get_communicator(db):
    """
    Get a communicator to interact with the project WS application (including middleware)
    """
    communicator = None

    async def _inner(path, *, session=None, user=None, host=None):
        if host is None:
            site = await database_sync_to_async(Site.objects.first)()
            assert site is not None
            host = site.domain

        # Tuples are required by spec
        # See https://github.com/django/asgiref/blob/main/specs/www.rst#http-connection-scope
        headers = [(b"host", bytes(host, encoding="utf-8"))]

        nonlocal communicator
        communicator = WebsocketCommunicator(application, path, headers)

        communicator.scope["session"] = session or Session()
        if user is not None:
            communicator.scope["user"] = user

        connected, _ = await communicator.connect()
        assert connected

        return communicator

    yield _inner

    await communicator.disconnect()


@sync_to_async
def get_org_id_async(user):
    return user.org_id


@sync_to_async
def get_serialized_org_async(data):
    return OrgSerializer(data)


@sync_to_async
def get_org_data_async(org):
    return org.data


@database_sync_to_async
def generate_model(model_factory, **kwargs):
    return model_factory(**kwargs)


@pytest.mark.django_db
async def test_push_notification_consumer__multi_tenancy(
    scratch_org_factory, site_factory, get_communicator
):
    uuid = str(uuid4())
    scratch_org = await generate_model(
        scratch_org_factory,
        uuid=uuid,
        enqueued_at=timezone.now(),
    )
    extra_site = await generate_model(site_factory, domain="extra.com")
    scratch_org.plan.plan_template.product.category.site_id = extra_site.id
    await database_sync_to_async(scratch_org.plan.plan_template.product.category.save)()

    communicator = await get_communicator(
        "/ws/notifications/", session=Session(scratch_org_id=uuid)
    )
    await communicator.send_json_to({"model": "scratchorg", "id": scratch_org.id})
    response = await communicator.receive_json_from()
    assert (
        "Invalid subscription" in response["error"]
    ), "Subscription should be invalid when trying to subscribe to an org in `extra_site` from the default site"

    communicator = await get_communicator(
        "/ws/notifications/", session=Session(scratch_org_id=uuid), host="unknown.com"
    )
    await communicator.send_json_to({"model": "scratchorg", "id": scratch_org.id})
    response = await communicator.receive_json_from()
    assert (
        "Invalid subscription" in response["error"]
    ), "Subscription should be invalid when trying to subscribe to an org in `extra_site` from an unknown site"

    communicator = await get_communicator(
        "/ws/notifications/",
        session=Session(scratch_org_id=uuid),
        host=extra_site.domain,
    )
    await communicator.send_json_to({"model": "scratchorg", "id": scratch_org.id})
    response = await communicator.receive_json_from()
    assert (
        "ok" in response
    ), "Subscription should be valid when trying to subscribe to an org in `extra_site` from the extra site"


@pytest.mark.django_db
async def test_push_notification_consumer__subscribe_scratch_org(
    scratch_org_factory, get_communicator
):
    uuid = str(uuid4())
    scratch_org = await generate_model(
        scratch_org_factory, uuid=uuid, enqueued_at=timezone.now()
    )
    communicator = await get_communicator(
        "/ws/notifications/", session=Session(scratch_org_id=uuid)
    )

    scratch_org_id = str(scratch_org.id)
    await communicator.send_json_to({"model": "scratchorg", "id": scratch_org_id})
    response = await communicator.receive_json_from()
    assert "ok" in response

    await notify_org_changed(scratch_org)
    response = await communicator.receive_json_from()
    assert response["type"] == "SCRATCH_ORG_UPDATED"


@pytest.mark.django_db
async def test_push_notification_consumer__subscribe_scratch_org_staff(
    user_factory, scratch_org_factory, get_communicator
):
    user = await generate_model(user_factory, is_staff=True)
    scratch_org = await generate_model(scratch_org_factory, enqueued_at=timezone.now())
    communicator = await get_communicator("/ws/notifications/", user=user)

    await communicator.send_json_to({"model": "scratchorg", "id": str(scratch_org.id)})
    response = await communicator.receive_json_from()
    assert "ok" in response


@pytest.mark.django_db
async def test_push_notification_consumer__scratch_org_job_started(
    scratch_org_factory, job_factory, get_communicator
):
    org_id = "00Dxxxxxxxxxxxxxxx"
    uuid = str(uuid4())
    scratch_org = await generate_model(
        scratch_org_factory,
        uuid=uuid,
        status=ScratchOrg.Status.complete,
        enqueued_at=timezone.now(),
        org_id=org_id,
    )
    job = await generate_model(
        job_factory, user=None, status=Job.Status.complete, org_id=org_id
    )
    communicator = await get_communicator(
        "/ws/notifications/", session=Session(scratch_org_id=uuid)
    )

    await communicator.send_json_to({"model": "scratchorg", "id": str(scratch_org.id)})
    response = await communicator.receive_json_from()
    assert "ok" in response

    await job_started(scratch_org, job)
    response = await communicator.receive_json_from()
    assert response["type"] == "JOB_STARTED"


@pytest.mark.django_db
async def test_push_notification_consumer__user_token_invalid(
    user_factory, get_communicator
):
    user = await generate_model(user_factory)
    communicator = await get_communicator("/ws/notifications/", user=user)

    await communicator.send_json_to({"model": "user", "id": str(user.id)})
    response = await communicator.receive_json_from()
    assert "ok" in response

    await user_token_expired(user)
    response = await communicator.receive_json_from()
    assert response == {"type": "USER_TOKEN_INVALID"}


@sync_to_async
def run_serializer(serializer_class, instance, context):
    return serializer_class(instance=instance, context=context).data


@pytest.mark.django_db
async def test_push_notification_consumer__subscribe_preflight(
    user_factory, preflight_result_factory, plan_factory, get_communicator
):
    user = await generate_model(user_factory)
    plan = await generate_model(plan_factory)
    org_id = await get_org_id_async(user)
    preflight = await generate_model(
        preflight_result_factory,
        user=user,
        status=PreflightResult.Status.complete,
        plan=plan,
        org_id=org_id,
    )
    communicator = await get_communicator("/ws/notifications/", user=user)

    await communicator.send_json_to(
        {"model": "preflightresult", "id": str(preflight.id)}
    )
    response = await communicator.receive_json_from()
    assert "ok" in response

    await preflight_completed(preflight)
    response = await communicator.receive_json_from()

    payload = await run_serializer(
        PreflightResultSerializer,
        preflight,
        user_context(user, communicator.scope["session"]),
    )
    assert response == {"type": "PREFLIGHT_COMPLETED", "payload": payload}


@pytest.mark.django_db
async def test_push_notification_consumer__subscribe_preflight_scratch_org(
    scratch_org_factory, preflight_result_factory, plan_factory, get_communicator
):
    org_id = "00Dxxxxxxxxxxxxxxx"
    uuid = str(uuid4())
    plan = await generate_model(plan_factory)
    await generate_model(
        scratch_org_factory,
        uuid=uuid,
        status=ScratchOrg.Status.complete,
        enqueued_at=timezone.now(),
        org_id=org_id,
    )
    preflight = await generate_model(
        preflight_result_factory,
        user=None,
        status=PreflightResult.Status.complete,
        plan=plan,
        org_id=org_id,
    )
    communicator = await get_communicator(
        "/ws/notifications/", session=Session(scratch_org_id=uuid)
    )

    await communicator.send_json_to(
        {"model": "preflightresult", "id": str(preflight.id)}
    )
    response = await communicator.receive_json_from()
    assert "ok" in response


@pytest.mark.django_db
async def test_push_notification_consumer__subscribe_job(
    user_factory, job_factory, get_communicator
):
    user = await generate_model(user_factory)
    org_id = await get_org_id_async(user)
    job = await generate_model(
        job_factory, user=user, status=Job.Status.complete, org_id=org_id
    )
    communicator = await get_communicator("/ws/notifications/", user=user)

    await communicator.send_json_to({"model": "job", "id": str(job.id)})
    response = await communicator.receive_json_from()
    assert "ok" in response

    await notify_post_job(job)
    response = await communicator.receive_json_from()
    assert response == {
        "type": "JOB_COMPLETED",
        "payload": await run_serializer(
            JobSerializer, job, user_context(user, communicator.scope["session"])
        ),
    }


@pytest.mark.django_db
async def test_push_notification_consumer__subscribe_job__bad(
    user_factory, job_factory, get_communicator
):
    user = await generate_model(user_factory)
    job = await generate_model(
        job_factory, status=Job.Status.complete, org_id="00Dxxxxxxxxxxxxxxx"
    )
    communicator = await get_communicator("/ws/notifications/", user=user)

    await communicator.send_json_to({"model": "job", "id": str(job.id)})
    response = await communicator.receive_json_from()
    assert "error" in response

    await notify_post_job(job)
    assert await communicator.receive_nothing()


@pytest.mark.django_db
async def test_push_notification_consumer__subscribe_job__missing(
    user_factory, get_communicator
):
    user = await generate_model(user_factory)
    communicator = await get_communicator("/ws/notifications/", user=user)

    await communicator.send_json_to({"model": "job", "id": "missingjob"})
    response = await communicator.receive_json_from()
    assert "error" in response


@pytest.mark.django_db
async def test_push_notification_consumer__subscribe_org(
    social_account_factory, user_factory, job_factory, plan_factory, get_communicator
):
    org_id = "00Dxxxxxxxxxxxxxxx"
    user = await generate_model(user_factory, socialaccount_set=[])
    await generate_model(
        social_account_factory,
        user=user,
        extra_data={
            # instance_url is the important part here:
            "instance_url": "https://example.com/",
            "organization_id": org_id,
            "organization_details": {
                "Name": "Sample Org",
                "OrganizationType": "Developer Edition",
            },
        },
    )
    plan = await generate_model(plan_factory)
    org_id = await get_org_id_async(user)
    job = await generate_model(
        job_factory,
        status=Job.Status.started,
        user=user,
        plan=plan,
        org_id=org_id,
    )
    communicator = await get_communicator("/ws/notifications/", user=user)

    await communicator.send_json_to({"model": "org", "id": org_id})
    response = await communicator.receive_json_from()
    assert "ok" in response

    await notify_org_result_changed(job)
    response = await communicator.receive_json_from()
    org = await get_serialized_org_async(
        {
            "org_id": org_id,
            "current_job": job,
            "current_preflight": None,
        }
    )
    org_data = await get_org_data_async(org)
    assert response == {"type": "ORG_CHANGED", "payload": org_data}


@pytest.mark.django_db
async def test_push_notification_consumer__anon_subscribe_org(
    social_account_factory,
    user_factory,
    job_factory,
    plan_factory,
    scratch_org_factory,
    get_communicator,
):
    uuid = str(uuid4())
    org_id = "00Dyyyyyyyyyyyyyyy"
    await generate_model(
        scratch_org_factory, uuid=uuid, org_id=org_id, status=ScratchOrg.Status.complete
    )
    user = await generate_model(user_factory, socialaccount_set=[])
    await generate_model(
        social_account_factory,
        user=user,
        extra_data={
            # instance_url is the important part here:
            "instance_url": "https://example.com/",
            "organization_id": org_id,
            "organization_details": {
                "Name": "Sample Org",
                "OrganizationType": "Developer Edition",
            },
        },
    )
    plan = await generate_model(plan_factory)
    org_id = await get_org_id_async(user)
    job = await generate_model(
        job_factory,
        status=Job.Status.started,
        user=user,
        plan=plan,
        org_id=org_id,
    )

    communicator = await get_communicator("/ws/notifications/")

    await communicator.send_json_to({"model": "org", "id": org_id, "uuid": uuid})
    response = await communicator.receive_json_from()
    assert "ok" in response

    await notify_org_result_changed(job)
    response = await communicator.receive_json_from()
    org = await get_serialized_org_async(
        {
            "org_id": org_id,
            "current_job": job,
            "current_preflight": None,
        }
    )
    data = await get_org_data_async(org)
    assert response == {
        "type": "ORG_CHANGED",
        "payload": data,
    }, response


@pytest.mark.django_db
async def test_push_notification_consumer__subscribe_org_bad(
    user_factory, get_communicator
):
    user = await generate_model(user_factory)
    communicator = await get_communicator("/ws/notifications/", user=user)

    await communicator.send_json_to({"model": "org", "id": "missingorg"})
    response = await communicator.receive_json_from()
    assert "error" in response
