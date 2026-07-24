import pytest

from delta_chat.observability.context import InvalidRequestIdError, validate_request_id


def test_request_id_rejects_traversal():
    with pytest.raises(InvalidRequestIdError):
        validate_request_id("../etc/passwd")
    with pytest.raises(InvalidRequestIdError):
        validate_request_id("a/b")
    with pytest.raises(InvalidRequestIdError):
        validate_request_id("")
    assert validate_request_id("eval-native_revision-abc123") == "eval-native_revision-abc123"
