"""
These are end-to-end integration tests that touch stateful resources,
like a Salesforce org. They should be run with caution, and needn't be
run on every test run.
"""

from os import environ

import pytest
from django.core.exceptions import ImproperlyConfigured

from metadeploy.api.jobs import run_flows
from metadeploy.api.models import Job


def env(name):
    try:
        return environ[name]
    except KeyError:
        raise ImproperlyConfigured(
            f"Cannot run integration tests. Missing environment variable: {name}."
        )


@pytest.mark.integration
@pytest.mark.django_db
def test_can_reach_salesforce(
    social_token_factory,
    social_account_factory,
    job_factory,
    user_factory,
    plan_factory,
    step_factory,
    version_factory,
    product_factory,
):
    # Ensure 12-factor-esque values are found:
    INSTANCE_URL = env("TEST_INSTANCE_URL")
    ORGANIZATION_ID = env("TEST_ORGANIZATION_ID")
    TOKEN = env("TEST_TOKEN")
    TOKEN_SECRET = env("TEST_TOKEN_SECRET")

    user = user_factory(socialaccount_set=[])
    social_account = social_account_factory(
        user=user,
        extra_data={
            "instance_url": INSTANCE_URL,
            "organization_id": ORGANIZATION_ID,
            "organization_details": {
                "Name": "OddBird",
                "OrganizationType": "Developer Edition",
            },
        },
        socialtoken_set=[],
    )
    social_token_factory(account=social_account, token=TOKEN, token_secret=TOKEN_SECRET)

    product = product_factory(repo_url="https://github.com/SFDO-Tooling/CumulusCI-Test")
    version = version_factory(commit_ish="feature/preflight", product=product)
    plan = plan_factory(version=version)
    step_factory(plan=plan)
    job = job_factory(user=user)

    run_flows(
        user=user,
        plan=plan,
        skip_steps=[],
        result_class=Job,
        result_id=job.id,
    )
