"""ensure_index under parallel ingestion: losing the create race is not a failure."""
from __future__ import annotations

import pytest
from lambdas.ingest_lib.aoss import AossClient
from opensearchpy.exceptions import RequestError


class _Indices:
    def __init__(self, error: str | None) -> None:
        self.error = error

    def exists(self, index: str) -> bool:
        return False

    def create(self, index: str, body: dict) -> None:
        if self.error:
            raise RequestError(400, self.error, {})


class _Client:
    def __init__(self, error: str | None) -> None:
        self.indices = _Indices(error)


def _aoss(error: str | None) -> AossClient:
    return AossClient(_Client(error))


def test_creates_when_missing() -> None:
    assert _aoss(None).ensure_index("units-v1-0", 1024) is True


def test_lost_race_is_not_an_error() -> None:
    assert _aoss("resource_already_exists_exception").ensure_index("units-v1-0", 1024) is False


def test_other_errors_propagate() -> None:
    with pytest.raises(RequestError):
        _aoss("illegal_argument_exception").ensure_index("units-v1-0", 1024)
