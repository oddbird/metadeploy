import logging
from unittest.mock import MagicMock, sentinel

import pytest
from django.core.cache import cache

from ..constants import REDIS_JOB_CANCEL_KEY
from ..flows import (
    BasicFlowCallback,
    JobFlowCallback,
    PreflightFlowCallback,
    StopFlowException,
)
from ..models import Step


def test_get_step_id(mocker):
    callbacks = BasicFlowCallback(sentinel.result)
    callbacks._steps = Step.objects.none()
    result = callbacks._get_step_id(step_num="anything")

    assert result is None


@pytest.mark.django_db
class TestJobFlow:
    def test_init(self, mocker):
        callbacks = JobFlowCallback(sentinel.job)
        assert callbacks.context == sentinel.job

    def test_cancel_job(self, mocker, job_factory):
        job = job_factory(org_id="00Dxxxxxxxxxxxxxxx")
        callbacks = JobFlowCallback(job)
        cache.set(REDIS_JOB_CANCEL_KEY.format(id=job.id), True)
        with pytest.raises(StopFlowException):
            callbacks.pre_task(None)
        cache.delete(REDIS_JOB_CANCEL_KEY.format(id=job.id))

    def test_pre_task(
        self, mocker, user_factory, plan_factory, step_factory, job_factory
    ):
        plan = plan_factory()
        steps = [step_factory(plan=plan, step_num=str(i)) for i in range(3)]

        job = job_factory(plan=plan, steps=steps, org_id="00Dxxxxxxxxxxxxxxx")

        callbacks = JobFlowCallback(job)

        stepspecs = [MagicMock(step_num=str(i)) for i in range(3)]
        MagicMock(exception=None)

        callbacks.pre_flow(sentinel.flow_coordinator)
        assert callbacks.result_handler.current_key is None
        for i, stepspec in enumerate(stepspecs):
            callbacks.pre_task(stepspec)
            assert callbacks.result_handler.current_key == str(steps[i].pk)
        callbacks.pre_task(None)
        assert callbacks.result_handler.current_key is None

    def test_post_task__permanent_org(self, plan_factory, step_factory, job_factory):
        plan = plan_factory()
        steps = [step_factory(plan=plan, step_num=str(i)) for i in range(3)]
        job = job_factory(plan=plan, steps=steps, org_id="00Dxxxxxxxxxxxxxxx")
        callbacks = JobFlowCallback(job)

        stepspecs = [MagicMock(step_num=step.step_num) for step in steps]
        result = MagicMock(exception=None)
        permanent_org_coordinator = MagicMock()

        callbacks.pre_flow(permanent_org_coordinator)
        for stepspec in stepspecs:
            callbacks.post_task(stepspec, result)
        callbacks.post_flow(permanent_org_coordinator)

        assert job.results == {str(step.id): [{"status": "ok"}] for step in steps}
        # Permanent orgs SHOULD NOT call the Salesforce API to reset the user password
        permanent_org_coordinator.org_config.salesforce_client.restful.assert_not_called()

    def test_post_task__scratch_org(
        self, plan_factory, step_factory, job_factory, scratch_org_factory
    ):
        plan = plan_factory()
        steps = [step_factory(plan=plan, step_num=str(i)) for i in range(3)]
        job = job_factory(plan=plan, steps=steps, org_id="00Dxxxxxxxxxxxxxxx")
        callbacks = JobFlowCallback(job)
        scratch_org = scratch_org_factory(plan=plan)

        stepspecs = [MagicMock(step_num=step.step_num) for step in steps]
        result = MagicMock(exception=None)
        scratch_org_coordinator = MagicMock(**{"org_config.org_id": scratch_org.org_id})

        callbacks.pre_flow(scratch_org_coordinator)
        for stepspec in stepspecs:
            callbacks.post_task(stepspec, result)
        callbacks.post_flow(scratch_org_coordinator)

        assert job.results == {str(step.id): [{"status": "ok"}] for step in steps}
        # Scratch orgs SHOULD call the Salesforce API to reset the user password
        scratch_org_coordinator.org_config.salesforce_client.restful.assert_called()

    def test_post_task__exception(
        self, mocker, user_factory, plan_factory, step_factory, job_factory
    ):
        user = user_factory()
        plan = plan_factory()
        steps = [step_factory(plan=plan, step_num=str(i)) for i in range(3)]

        job = job_factory(user=user, plan=plan, steps=steps, org_id=user.org_id)
        coordinator = MagicMock()

        callbacks = JobFlowCallback(job)

        step = MagicMock(step_num="0")
        step.result = MagicMock(exception=ValueError("Some error"))

        callbacks.pre_flow(coordinator)
        callbacks.post_task(step, step.result)
        callbacks.post_flow(coordinator)

        assert job.results == {
            str(steps[0].id): [{"status": "error", "message": "Some error"}]
        }


class TestPreflightFlow:
    def test_init(self, mocker):
        callbacks = PreflightFlowCallback(sentinel.preflight)
        assert callbacks.context == sentinel.preflight

    @pytest.mark.django_db
    def test_post_flow(
        self, mocker, user_factory, plan_factory, step_factory, preflight_result_factory
    ):
        user = user_factory()
        plan = plan_factory()
        step1 = step_factory(plan=plan, path="name_1", step_num="1.1")
        step_factory(plan=plan, path="name_2", step_num="1.2")
        step3 = step_factory(plan=plan, path="name_3", step_num="2")
        step4 = step_factory(plan=plan, path="name_4", step_num="3")
        step5 = step_factory(plan=plan, path="name_5", step_num="3.4")
        pfr = preflight_result_factory(user=user, plan=plan, org_id=user.org_id)
        results = {
            "1.1": [{"status": "error", "message": "error 1"}],
            "2": [{"status": "warn", "message": "warn 1"}],
            "3": [{"status": "optional", "message": ""}],
            "3.4": [{"status": "skip", "message": "skip 1"}],
        }
        flow_coordinator = MagicMock(preflight_results=results)

        callbacks = PreflightFlowCallback(pfr)
        callbacks.pre_flow(flow_coordinator)
        logging.getLogger("cumulusci.flows").info("test")
        callbacks.post_flow(flow_coordinator)

        assert pfr.log == "test\n"
        assert pfr.results == {
            step1.id: [{"status": "error", "message": "error 1"}],
            step3.id: [{"status": "warn", "message": "warn 1"}],
            step4.id: [{"status": "optional", "message": ""}],
            step5.id: [{"status": "skip", "message": "skip 1"}],
        }

    @pytest.mark.django_db
    def test_post_flow__multiple_results_for_single_step_saved(
        self, mocker, user_factory, plan_factory, step_factory, preflight_result_factory
    ):
        """This test ensures that if multiple results exist for a given step_num
        that they are all saved to the database."""
        user = user_factory()
        plan = plan_factory()
        step_factory(plan=plan, path="name_1", step_num="1")
        step_factory(plan=plan, path="name_2", step_num="2")
        preflight_result = preflight_result_factory(
            user=user, plan=plan, org_id=user.org_id
        )

        results = {
            None: [
                {"status": "warn", "message": "a warning message"},
                {"status": "error", "message": "an error message"},
            ]
        }
        flow_coordinator = MagicMock(preflight_results=results)

        callbacks = PreflightFlowCallback(preflight_result)
        callbacks.pre_flow(flow_coordinator)
        callbacks.post_flow(flow_coordinator)

        actual_results = preflight_result.results
        assert actual_results == {
            "plan": [
                {"status": "warn", "message": "a warning message"},
                {"status": "error", "message": "an error message"},
            ]
        }

    @pytest.mark.django_db
    def test_post_task__exception(
        self, mocker, user_factory, plan_factory, preflight_result_factory
    ):
        user = user_factory()
        plan = plan_factory()
        pfr = preflight_result_factory(user=user, plan=plan, org_id=user.org_id)
        callbacks = PreflightFlowCallback(pfr)

        step = MagicMock()
        step.result = MagicMock(exception=ValueError("A value error."))
        with pytest.raises(ValueError, match="A value error."):
            callbacks.post_task(step, step.result)
