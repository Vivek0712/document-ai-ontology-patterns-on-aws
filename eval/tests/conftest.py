from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (REPO_ROOT, REPO_ROOT / "scripts" / "lib"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("RP_ENV", "test")
    monkeypatch.setenv("RP_CONFIG_SOURCE", "local")
    monkeypatch.setenv("RP_REPO_ROOT", str(REPO_ROOT))
    monkeypatch.setenv("RP_NO_PROFILE", "1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
