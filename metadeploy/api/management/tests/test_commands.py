import logging
import re
from contextlib import ExitStack
from unittest import mock
from unittest.mock import patch

import pytest
from cumulusci.core.config import OrgConfig
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from requests.exceptions import HTTPError

from metadeploy.api.management.commands.schedule_release_test import (
    execute_release_test,
    get_plans_to_test,
)
from metadeploy.api.models import Job, Plan, PreflightResult, ScratchOrg


@pytest.mark.django_db()
def test_run_plan(plan_factory):
    plan = plan_factory(preflight_checks=[{"when": "False", "action": "error"}])
    org_config = OrgConfig(
        {
            "instance_url": "https://sample.salesforce.org/",
            "access_token": "abc123",
            "refresh_token": "abc123",
            "org_id": "00Dxxxxxxxxxxxxxxx",
        },
        "Release",
    )

    with ExitStack() as stack:
        stack.enter_context(patch("metadeploy.api.jobs.local_github_checkout"))
        stack.enter_context(
            patch("metadeploy.api.salesforce.OrgConfig.refresh_oauth_token")
        )
        stack.enter_context(
            patch("cumulusci.core.flowrunner.PreflightFlowCoordinator.run")
        )
        stack.enter_context(patch("cumulusci.core.flowrunner.FlowCoordinator.run"))
        stack.enter_context(
            patch(
                "metadeploy.api.jobs.create_scratch_org_on_sf",
                return_value=(org_config, None, None),
            )
        )
        stack.enter_context(patch("metadeploy.api.jobs.delete_scratch_org_on_sf"))

        call_command("run_plan", str(plan.id))

    # We can't query the scratch org since it's been deleted
    job = Job.objects.get()
    preflight_result = PreflightResult.objects.get()

    assert job.status == "complete"
    assert job.is_release_test
    assert preflight_result.is_release_test

    with pytest.raises(ScratchOrg.DoesNotExist):
        ScratchOrg.objects.get(plan=plan)


@pytest.mark.django_db
def test_run_plan__no_plan_exists():
    with pytest.raises(CommandError):
        call_command("run_plan", "abc123")


@pytest.mark.django_db()
@mock.patch("metadeploy.api.management.commands.run_plan.setup_scratch_org")
def test_run_plan__scratch_org_creation_fails(setup_scratch_org, plan_factory, caplog):
    caplog.set_level(logging.INFO)
    setup_scratch_org.side_effect = Exception("Scratch org creation failed")
    plan = plan_factory(preflight_checks=[{"when": "False", "action": "error"}])

    with pytest.raises(Exception, match="Scratch org creation failed"):
        call_command("run_plan", str(plan.id))

    assert "Scratch org creation failed" in caplog.text


@pytest.mark.django_db
def test_schedule_release_test__no_plans_to_test(caplog):
    caplog.set_level(logging.INFO)
    caplog.clear()
    execute_release_test()
    assert caplog.records[0].getMessage() == "No plans found for regression testing."


@pytest.mark.django_db
def test_get_plans_to_test(plan_template_factory, plan_factory):
    template_1 = plan_template_factory()
    # two plans of tier 'primary'
    plan_factory(tier=Plan.Tier.primary, plan_template=template_1)
    plan_factory(tier=Plan.Tier.primary, plan_template=template_1)
    # one plan with tier of 'additional' (should not be tested).
    plan_factory(tier=Plan.Tier.additional, plan_template=template_1)

    template_2 = plan_template_factory()
    # two plans of tier 'secondary'
    plan_factory(tier=Plan.Tier.secondary, plan_template=template_2)
    plan_factory(tier=Plan.Tier.secondary, plan_template=template_2)
    # one plan with tier of 'additional' (should not be tested).
    plan_factory(tier=Plan.Tier.additional, plan_template=template_2)

    # one template that has opted out of testing completely
    template_opted_out = plan_template_factory(regression_test_opt_out=True)
    plan_factory(tier=Plan.Tier.primary, plan_template=template_opted_out)

    plans_to_test = get_plans_to_test()
    assert len(plans_to_test) == 1


@pytest.mark.django_db
@mock.patch("metadeploy.api.management.commands.schedule_release_test.requests.post")
def test_schedule_release_test__happy_path(post, plan_template_factory, plan_factory):
    template = plan_template_factory()
    plan_factory(plan_template=template)

    post.return_value = mock.Mock(status_code=200, text="Fatal Error")
    execute_release_test()


@pytest.mark.django_db
@mock.patch("metadeploy.api.management.commands.schedule_release_test.requests.post")
def test_schedule_release_test__bad_response(post, plan_factory, plan_template_factory):
    template = plan_template_factory()
    plan_factory(plan_template=template)

    post.return_value = mock.Mock(status_code=500, text="Fatal Error")
    with pytest.raises(HTTPError, match="An internal server error occurred."):
        execute_release_test()


@pytest.mark.django_db
def test_schedule_release_test__no_heroku_worker_app_name(
    plan_template_factory, plan_factory
):
    template = plan_template_factory()
    plan_factory(plan_template=template)

    with mock.patch.object(settings, "HEROKU_APP_NAME", None):
        with pytest.raises(
            ImproperlyConfigured,
            match="The HEROKU_APP_NAME environment variable is required for regression testing.",
        ):
            execute_release_test()


@pytest.mark.django_db
def test_schedule_release_test__no_heroku_token(plan_template_factory, plan_factory):
    template = plan_template_factory()
    plan_factory(plan_template=template)

    with mock.patch.object(settings, "HEROKU_TOKEN", None):
        with pytest.raises(
            ImproperlyConfigured,
            match=re.escape(
                "The HEROKU_TOKEN environment variable is required for regression testing."
            ),
        ):
            execute_release_test()
