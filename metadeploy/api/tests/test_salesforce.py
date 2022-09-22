from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from requests.exceptions import HTTPError

from ..salesforce import (
    ScratchOrgError,
    _get_devhub_api,
    _get_org_result,
    _poll_for_scratch_org_completion,
    delete_scratch_org,
    refresh_access_token,
)


class TestGetDevhubApi:
    @override_settings(DEVHUB_USERNAME=None)
    def test_no_devhub_username(self):
        with pytest.raises(ImproperlyConfigured):
            _get_devhub_api()

    @override_settings(DEVHUB_USERNAME="test@example.com")
    def test_bad(self):
        with ExitStack() as stack:
            jwt_session = stack.enter_context(
                patch("metadeploy.api.salesforce.jwt_session")
            )
            jwt_session.side_effect = HTTPError(
                "Error message.", response=MagicMock(status_code=400)
            )

            scratch_org = MagicMock()
            with pytest.raises(ScratchOrgError, match=".*org still exists*"):
                _get_devhub_api(scratch_org=scratch_org)

            assert scratch_org.delete.called


class TestRefreshAccessToken:
    def test_good(self):
        with ExitStack() as stack:
            OrgConfig = stack.enter_context(
                patch("metadeploy.api.salesforce.OrgConfig")
            )

            refresh_access_token(
                scratch_org=MagicMock(),
                config=MagicMock(),
                org_name=MagicMock(),
            )

            assert OrgConfig.called

    def test_bad(self):
        with ExitStack() as stack:
            get_current_job = stack.enter_context(
                patch("metadeploy.api.salesforce.get_current_job")
            )
            get_current_job.return_value = MagicMock(id=123)
            OrgConfig = stack.enter_context(
                patch("metadeploy.api.salesforce.OrgConfig")
            )
            OrgConfig.side_effect = HTTPError(
                "Error message.", response=MagicMock(status_code=400)
            )

            scratch_org = MagicMock()
            with pytest.raises(ScratchOrgError, match=".*job ID.*"):
                refresh_access_token(
                    scratch_org=scratch_org,
                    config=MagicMock(),
                    org_name=MagicMock(),
                )

            assert scratch_org.delete.called


@pytest.mark.django_db
def test_delete_org(scratch_org_factory):
    scratch_org = scratch_org_factory(config={"org_id": "some-id"})
    with ExitStack() as stack:
        devhub_api = MagicMock()
        _get_devhub_api = stack.enter_context(
            patch("metadeploy.api.salesforce._get_devhub_api")
        )
        _get_devhub_api.return_value = devhub_api
        devhub_api.query.return_value = {"records": [{"Id": "some-id"}]}

        delete_scratch_org(scratch_org)

        assert devhub_api.ActiveScratchOrg.delete.called


@pytest.mark.django_db
def test_get_org_result():
    scratch_org_info_id = "2SR4p000000DTAaGAO"
    cci = MagicMock()
    cci.project_config.project__package__namespace = "protogen"
    devhub_api = MagicMock()
    scratch_org_config = MagicMock()
    scratch_org_config.namespaced = True
    devhub_api.ScratchOrgInfo.create.return_value = {"id": scratch_org_info_id}
    devhub_api.ScratchOrgInfo.get.return_value = {
        "Id": scratch_org_info_id,
        "Name": "MetaDeploy Scratch Org",
    }
    devhub_api.ScratchOrgInfo.describe.return_value = {
        "fields": [{"name": "FooField", "createable": True}]
    }

    scratch_org_definition = {
        "features": ["Communities", "MarketingUser"],
        "description": "foo",
        "template": "0TTxxxxxxxxxxxx",
        "fooField": "barValue",
        "settings": {"FooSettings": {"UseFoo": True}},
    }

    result = _get_org_result(
        email="jpmao@mao-kwikowski.luna",
        repo_owner="Protogen",
        repo_name="Caliban",
        repo_branch="feature/experiment",
        duration=3,
        scratch_org_config=scratch_org_config,
        scratch_org_definition=scratch_org_definition,
        cci=cci,
        devhub_api=devhub_api,
    )
    assert result == devhub_api.ScratchOrgInfo.get.return_value
    devhub_api.ScratchOrgInfo.create.assert_called_once_with(
        {
            "adminemail": "jpmao@mao-kwikowski.luna",
            "connectedappconsumerkey": settings.SFDX_CLIENT_ID,
            "connectedappcallbackurl": settings.SFDX_CLIENT_CALLBACK_URL,
            # Defaulted - should ignore the value in the definition
            # lack of `description` as a key proves case-insensitivity
            "description": "Protogen/Caliban feature/experiment",
            "durationdays": 3,
            # From schema of ScratchOrgInfo
            "foofield": "barValue",
            "features": "Communities;MarketingUser",
            "namespace": "protogen",
            "orgname": "MetaDeploy Scratch Org",
            "hassampledata": False,
        }
    )


@patch("metadeploy.api.salesforce.time.sleep")
def test_poll_for_scratch_org_completion__success(sleep):
    scratch_org_info_id = "2SR4p000000DTAaGAO"
    devhub_api = MagicMock()
    initial_result = {
        "Id": scratch_org_info_id,
        "Status": "Creating",
        "ErrorCode": None,
    }
    end_result = {"Id": scratch_org_info_id, "Status": "Active", "ErrorCode": None}
    devhub_api.ScratchOrgInfo.get.side_effect = [initial_result, end_result]

    org_result = _poll_for_scratch_org_completion(devhub_api, initial_result)
    assert org_result == end_result


@patch("metadeploy.api.salesforce.time.sleep")
def test_poll_for_scratch_org_completion__failure(sleep):
    scratch_org_info_id = "2SR4p000000DTAaGAO"
    devhub_api = MagicMock()
    initial_result = {
        "Id": scratch_org_info_id,
        "Status": "Creating",
        "ErrorCode": None,
    }
    end_result = {"Id": scratch_org_info_id, "Status": "Failed", "ErrorCode": "Foo"}
    devhub_api.ScratchOrgInfo.get.side_effect = [initial_result, end_result]

    with pytest.raises(ScratchOrgError, match="Scratch org creation failed"):
        _poll_for_scratch_org_completion(devhub_api, initial_result)
