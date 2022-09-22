"""
A note on how jobs are scheduled and run:

Any job that has to touch the database should not be scheduled directly.
This is because of the issues laid out in
<https://brandur.org/job-drain>, but to summarize: if a job is triggered
in the same transaction as the data it relies on is written, it may try
to run before that data is actually visible in the database.

To get around this, we have a single periodic enqueuer job that picks up
instances of the Job model and triggers the run_flows_job.
"""

import contextlib
import enum
import logging
import os
import sys
import traceback
import uuid
from datetime import timedelta
from typing import Union

from asgiref.sync import async_to_sync
from cumulusci.core.config import OrgConfig, ServiceConfig
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django_rq import job as django_rq_job
from rq.exceptions import ShutDownImminentException
from rq.worker import StopRequested

from metadeploy.multitenancy import disable_site_filtering, override_current_site_id

from .cci_configs import MetaDeployCCI, extract_user_and_repo
from .cleanup import cleanup_user_data
from .flows import StopFlowException
from .github import local_github_checkout
from .models import ORG_TYPES, Job, Plan, PreflightResult, ScratchOrg
from .push import job_started, preflight_started, report_error
from .salesforce import create_scratch_org as create_scratch_org_on_sf
from .salesforce import delete_scratch_org as delete_scratch_org_on_sf

logger = logging.getLogger(__name__)
User = get_user_model()
sync_report_error = async_to_sync(report_error)


def job(*args, **kw):
    # keep failed jobs for 7 days
    kw["failure_ttl"] = 7 * 3600 * 24
    return django_rq_job(*args, **kw)


class JobType(str, enum.Enum):
    JOB = "job"
    PREFLIGHT = "preflight"
    TEST_JOB = "test_job"


class JobLogStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"  # preflight returned negative result
    ERROR = "error"  # job threw an exception and failed
    CANCELED = "canceled"  # by admins
    TERMINATED = "terminated"  # by Heroku


@contextlib.contextmanager
def finalize_result(result: Union[Job, PreflightResult]):
    start_time = timezone.now()
    end_time = None
    log_status = None
    log_msg = None

    try:
        yield

        result.status = result.Status.complete
        result.success_at = timezone.now()
        end_time = result.success_at

        if isinstance(result, PreflightResult) and result.has_any_errors():
            # Determine if the preflight returned a negative status (FAILURE)
            # as opposed to throwing an exception (ERROR)
            log_status = JobLogStatus.FAILURE
            log_msg = f"{result.__class__.__name__} {result.id} failed"
        else:
            log_status = JobLogStatus.SUCCESS
            log_msg = f"{result.__class__.__name__} {result.id} succeeded"
    except (StopRequested, ShutDownImminentException):
        # When an RQ worker gets a SIGTERM, it will initiate a warm shutdown,
        # trying to wrap up existing tasks and then raising a
        # ShutDownImminentException or StopRequested exception.
        # So we want to mark any job that's not done by then as canceled
        # by catching that exception as it propagates back up.
        result.status = result.Status.canceled
        result.canceled_at = timezone.now()
        end_time = result.canceled_at
        log_status = JobLogStatus.TERMINATED
        log_msg = f"{result.__class__.__name__} {result.id} interrupted by dyno restart"
        result.exception = (
            "The installation job was interrupted. Please retry the installation."
        )
        raise
    except StopFlowException as e:
        # User requested cancellation of job
        result.status = result.Status.canceled
        result.canceled_at = timezone.now()
        end_time = result.canceled_at
        log_status = JobLogStatus.CANCELED
        log_msg = f"{result.__class__.__name__} {result.id} canceled"
        result.exception = str(e)
    except Exception as e:
        # Other failures
        result.status = result.Status.failed
        end_time = timezone.now()
        log_status = JobLogStatus.ERROR
        log_msg = f"{result.__class__.__name__} {result.id} errored"
        result.exception = "".join(traceback.format_tb(e.__traceback__))
        result.exception += "\n" + f"{e.__class__.__name__}: {e}"
        if getattr(e, "response", None) is not None:
            result.exception += "\n" + e.response.text
        raise
    finally:
        duration = (end_time - start_time).seconds

        if result.is_release_test:
            job_type = JobType.TEST_JOB
        else:
            if isinstance(result, PreflightResult):
                job_type = JobType.PREFLIGHT
            elif isinstance(result, Job):
                job_type = JobType.JOB

        context = f"{result.plan.version.product.slug}/{result.plan.version.label}/{result.plan.slug}"
        logger.info(
            log_msg,
            extra={
                "context": {
                    "event": f"{job_type}",
                    "context": context,
                    "status": f"{log_status}",
                    "duration": duration,
                }
            },
        )
        result.save()


@contextlib.contextmanager
def report_errors_to(user):
    try:
        yield
    except Exception:
        sync_report_error(user)
        tb = traceback.format_exc()
        logger.error(tb)
        raise


@contextlib.contextmanager
def delete_org_on_error(scratch_org):
    try:
        yield
    except Exception:
        scratch_org.fail_job()
        raise


@contextlib.contextmanager
def prepend_python_path(path):
    prev_path = sys.path.copy()
    sys.path.insert(0, path)
    try:
        yield
    finally:
        sys.path = prev_path


def run_flows(
    *,
    plan: Plan,
    skip_steps: list[str],
    result_class: Union[type[Job], type[PreflightResult]],
    result_id: int,
):
    """
    This operates with side effects; it changes things in a Salesforce
    org, and then records the results of those operations on to a
    `result`.

    Args:
        plan (Plan): The Plan instance for the flow you're running.
        skip_steps (List[str]): The strings in the list should be valid
            step_num values for steps in this flow.
        result_class (Union[Type[Job], Type[PreflightResult]]): The type
            of the instance onto which to record the results of running
            steps in the flow. Either a PreflightResult or a Job, as
            appropriate.
        result_id (int): the PK of the result instance to get.
    """
    with contextlib.ExitStack() as stack:
        # Scope all DB operations to the Plan's site
        stack.enter_context(override_current_site_id(plan.site_id))

        result = result_class.objects.get(pk=result_id)
        scratch_org = None
        if not result.user:
            # This means we're in a ScratchOrg.
            scratch_org = ScratchOrg.objects.get(org_id=result.org_id)

        repo_url = plan.version.product.repo_url
        commit_ish = plan.commit_ish or plan.version.commit_ish

        stack.enter_context(finalize_result(result))
        if result.user:
            stack.enter_context(report_errors_to(result.user))
        if scratch_org:
            stack.enter_context(delete_org_on_error(scratch_org))

        # Let's clone the repo locally:
        repo_user, repo_name = extract_user_and_repo(repo_url)
        repo_root = stack.enter_context(
            local_github_checkout(repo_user, repo_name, commit_ish)
        )

        # Get cwd into Python path, so that the tasks below can import
        # from the checked-out repo:
        stack.enter_context(prepend_python_path(os.path.abspath(repo_root)))

        # There's a lot of setup to make configs and keychains, link
        # them properly, and then eventually pass them into a flow,
        # which we then run:
        ctx = MetaDeployCCI(repo_root=repo_root, plan=plan)

        current_org = "current_org"
        if settings.METADEPLOY_FAST_FORWARD:  # pragma: no cover
            org_config = OrgConfig({}, name=current_org, keychain=ctx.keychain)
        elif scratch_org:
            org_config = scratch_org.get_refreshed_org_config(
                org_name=current_org, keychain=ctx.keychain
            )
        else:
            token, token_secret = result.user.token
            org_config = OrgConfig(
                {
                    "access_token": token,
                    "instance_url": result.user.instance_url,
                    "refresh_token": token_secret,
                    "username": result.user.sf_username,
                    # 'id' is used by CumulusCI to pick the right 'aud' for JWT auth
                    "id": result.user.oauth_id,
                },
                current_org,
                keychain=ctx.keychain,
            )
        org_config.save()

        # Set up the connected_app:
        connected_app = ServiceConfig(
            {
                "client_secret": settings.SFDX_CLIENT_SECRET,
                "callback_url": settings.SFDX_CLIENT_CALLBACK_URL,
                "client_id": settings.SFDX_CLIENT_ID,
                # Note: login_url is not used when refreshing the existing token,
                # so it doesn't matter whether it's login vs test
                "login_url": "https://login.salesforce.com",
            }
        )
        ctx.keychain.set_service("connected_app", "metadeploy", connected_app)
        ctx.keychain._default_services["connected_app"] = "metadeploy"

        steps = [
            step.to_spec(
                project_config=ctx.project_config, skip=step.step_num in skip_steps
            )
            for step in plan.steps.all()
        ]
        org = ctx.keychain.get_org(current_org)
        if not settings.METADEPLOY_FAST_FORWARD:
            result.run(ctx, plan, steps, org)


run_flows_job = job(run_flows)


@disable_site_filtering()
def enqueuer():
    logger.debug("Enqueuer live")
    for j in Job.objects.filter(enqueued_at=None):
        j.invalidate_related_preflight()
        rq_job = run_flows_job.delay(
            plan=j.plan,
            skip_steps=j.skip_steps(),
            result_class=Job,
            result_id=j.id,
        )
        j.job_id = rq_job.id
        j.enqueued_at = rq_job.enqueued_at
        j.save()


enqueuer_job = job(enqueuer)


# Aliased to expire_user_tokens_job for backwards compatibility
expire_user_tokens_job = cleanup_user_data_job = job(cleanup_user_data)


def preflight(preflight_result_id):
    # Because the FieldTracker interferes with transparently serializing models across
    # the Redis boundary, we have to pass a primitive value to this function,

    # Disable site filtering because the ID could come from any Site
    with disable_site_filtering():
        preflight_result = PreflightResult.objects.get(pk=preflight_result_id)
    run_flows(
        plan=preflight_result.plan,
        skip_steps=[],
        result_class=PreflightResult,
        result_id=preflight_result.id,
    )


preflight_job = job(preflight)


@disable_site_filtering()
def expire_preflights():
    now = timezone.now()
    preflight_lifetime_ago = now - timedelta(
        minutes=settings.PREFLIGHT_LIFETIME_MINUTES
    )
    preflights_to_invalidate = PreflightResult.objects.filter(
        created_at__lte=preflight_lifetime_ago,
        is_valid=True,
    )
    for preflight in preflights_to_invalidate:
        preflight.is_valid = False
        preflight.save()


expire_preflights_job = job(expire_preflights)


def create_scratch_org(org_pk):
    """
    Takes our local ScratchOrg model instance and creates the actual org on Salesforce.

    If the plan associated with the ScratchOrg requires preflight checks, then
    they are automatically run against the org.
    If the plan associated with the ScratchOrg has *NO OPTIONAL STEPS* then
    the plan steps are also run against the org.
    """
    org, plan = setup_scratch_org(org_pk)

    with override_current_site_id(plan.site_id):
        if plan.requires_preflight:
            preflight_result = run_preflight_checks_sync(org)
            async_to_sync(preflight_started)(org, preflight_result)

        if plan.required_step_ids.count() == plan.steps.count():
            # Start installation job automatically if both:
            # - Plan has no preflight
            # - All plan steps are required
            job = run_plan_steps(org, release_test=False)
            # This is already called on `save()`, but the new Job isn't in the
            # database yet because it's in an atomic transaction.
            job.push_to_org_subscribers(is_new=True, changed={})
            async_to_sync(job_started)(org, job)


create_scratch_org_job = job(create_scratch_org)


def delete_scratch_org(scratch_org, should_delete_locally=True):
    with override_current_site_id(scratch_org.plan.site_id):
        try:
            scratch_org.refresh_from_db()
            delete_scratch_org_on_sf(scratch_org)
        finally:
            if should_delete_locally:
                scratch_org.delete(should_delete_on_sf=False, should_notify=False)


delete_scratch_org_job = job(delete_scratch_org)


@disable_site_filtering()
def calculate_average_plan_runtime():
    """Plan.average_duration is a slow query, so we
    get its value for each plan via this method and store its value
    on Plan.calculated_average_duration.
    """
    for plan in Plan.objects.all():
        duration_seconds = plan.average_duration
        if duration_seconds:
            plan.calculated_average_duration = int(duration_seconds)
            plan.save()


calculate_average_plan_runtime_job = job(calculate_average_plan_runtime)


def run_preflight_checks_sync(org: ScratchOrg, release_test=False):
    """Runs the preflight checks of the given plan against an org synchronously"""
    preflight_result = PreflightResult.objects.create(
        user=None,
        plan=org.plan,
        org_id=org.org_id,  # Set by org.complete()
        is_release_test=release_test,
    )
    preflight(preflight_result.pk)
    return preflight_result


def run_plan_steps(org: ScratchOrg, release_test=False):
    """Runs the plan steps against the org.
    If this is a release test then enqueued_at is populated to bypass queues,
    and the flow is run immediately."""
    with transaction.atomic():
        job = Job.objects.create(
            user=None,
            plan=org.plan,
            org_id=org.org_id,  # Set by org.complete()
            full_org_type=ORG_TYPES.Scratch,
            is_release_test=release_test,
            enqueued_at=timezone.now() if release_test else None,
        )
        job.steps.set(org.plan.steps.all())

    if release_test:
        run_flows(plan=job.plan, skip_steps=[], result_class=Job, result_id=str(job.id))

    return job


def setup_scratch_org(org_pk: str):
    """Given the id for a ScratchOrg record,
    checkout the source at the given commit from the associated plan
    and create a scratch org from Salesforce with code from the given
    commit."""
    # Disable site filtering because `org_pk` could come from any Site
    with disable_site_filtering():
        org = ScratchOrg.objects.get(pk=org_pk)
        plan = org.plan

    with override_current_site_id(plan.site_id):
        email = org.email
        org.email = None
        org.save()
        repo_url = plan.version.product.repo_url
        repo_owner, repo_name = extract_user_and_repo(repo_url)
        commit_ish = plan.commit_ish

        if settings.METADEPLOY_FAST_FORWARD:  # pragma: no cover
            fake_org_id = str(uuid.uuid4())[:18]
            scratch_org_config = OrgConfig({"org_id": fake_org_id}, "scratch")
        else:
            try:
                with local_github_checkout(
                    repo_owner, repo_name, commit_ish=commit_ish
                ) as repo_root:
                    scratch_org_config, _, org_config = create_scratch_org_on_sf(
                        repo_owner=repo_owner,
                        repo_name=repo_name,
                        repo_url=repo_url,
                        repo_branch=commit_ish,
                        email=email,
                        project_path=repo_root,
                        scratch_org=org,
                        org_name=plan.org_config_name,
                        duration=plan.scratch_org_duration,
                    )
            except Exception as e:
                org.fail(e)
                raise

        # this stores some values on the scratch
        # org model in the db
        org.complete(scratch_org_config)

        return org, plan
