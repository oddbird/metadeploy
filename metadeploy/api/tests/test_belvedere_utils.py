import pytest

from ..belvedere_utils import (
    convert_to_18,
    obscure_mpinstaller_deployment_test_failure,
    obscure_salesforce_log,
)


def test_convert_to_18_too_short():
    text = "00D1F0000009"
    with pytest.raises(ValueError):
        convert_to_18(text)


def test_convert_to_18():
    text = "00D1F0000009Gpn"
    expected = "00D1F0000009GpnUAE"
    assert convert_to_18(text) == expected


def test_convert_to_18_caps():
    text = "00DABCDEFGHIJKL"
    expected = "00DABCDEFGHIJKL255"
    assert convert_to_18(text) == expected


def test_convert_to_18_already_18():
    text = "00D1F0000009GpnUAE"
    assert convert_to_18(text) == text


def test_obscure_salesforce_log():
    text = """
(Required: 1, Available: 1)
Please include this ErrorId if you contact support: 000000-000 (000000)
Organization Name: Some organization
Organization ID:
000000000000000
    """
    expected = """
(Required: <X>, Available: <Y>)
Please include this ErrorId if you contact support: <ERROR_ID>
Organization Name: <ORG_NAME>
Organization ID:
000...
    """
    assert obscure_salesforce_log(text) == expected


def test_obscure_mpinstaller_deployment_test_failure():
    text = "Apex Test Failure: "
    expected = "Apex Test Failure"
    assert obscure_mpinstaller_deployment_test_failure(text) == expected
