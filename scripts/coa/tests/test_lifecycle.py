"""scripts/coa/lifecycle.sh against a fake `aws` and a fake COA checkout: e-mails never reach the build log, the
destroy's exit codes map to results (exit preserved through the redaction pipe), the tear-down waits for stacks
still in progress, leftovers are reported, and a checkout at any other commit is refused."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "coa" / "lifecycle.sh"
CFG = json.loads((ROOT / "contracts" / "generated" / "coa_control.json").read_text())

FAKE_AWS = r"""#!/usr/bin/env bash
# list-stacks: the in-progress query answers from $FAKE_BUSY (a counter file: "busy" until it reaches 0); the
# leftovers query answers $FAKE_LEFT
echo "aws $*" >>"$FAKE_LOG"
case "$*" in
  *"list-stacks"*"CREATE_IN_PROGRESS"*)
    n="$(cat "$FAKE_BUSY" 2>/dev/null || echo 0)"
    if [ "$n" -gt 0 ]; then echo $((n - 1)) >"$FAKE_BUSY"; echo "coa-dev-serve"; else echo ""; fi ;;
  *"list-stacks"*) printf '%s' "${FAKE_LEFT:-}" ;;
  *"sts get-caller-identity"*) echo 000000000000 ;;
  *) echo "" ;;
esac
"""


def _exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def box(tmp_path: Path) -> dict[str, object]:
    bin_dir = tmp_path / "bin"
    _exe(bin_dir / "aws", FAKE_AWS)
    work = tmp_path / "work"
    coa = work / "context-ontology-accelerator"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RP_BUNDLE_DIR": str(ROOT),
           "RP_COA_WORKDIR": str(work), "RP_RESULT_FILE": str(tmp_path / "result"),
           "FAKE_LOG": str(tmp_path / "aws.log"), "FAKE_BUSY": str(tmp_path / "busy"), "RP_SETTLE_POLL_SECONDS": "0",
           "HOME": str(tmp_path / "home")}
    env.pop("CODEBUILD_BUILD_ID", None)
    return {"env": env, "tmp": tmp_path, "coa": coa}


def _run(box: dict[str, object], step: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), step], env=box["env"], input=stdin, capture_output=True, text=True,
                          timeout=60, check=False)


def _destroy(box: dict[str, object], rc: int) -> None:
    _exe(box["coa"] / "scripts" / "destroy.sh",  # type: ignore[operator]
         f"#!/usr/bin/env bash\necho 'deleting UserPoolUser admin.person@example.com (CREATE_FAILED)'\n"
         f"echo \"guardduty=$SCL_DESTROY_DELETE_GUARDDUTY_ENDPOINT yes=$SCL_DESTROY_YES\"\nexit {rc}\n")


def test_redact_masks_email_addresses(box: dict[str, object]) -> None:
    out = _run(box, "redact", "PhysicalResourceId: a.b-c+d@mail.example.org failed; arn:aws:iam::x:role/y\n")
    assert out.stdout == "PhysicalResourceId: <email> failed; arn:aws:iam::x:role/y\n"


@pytest.mark.parametrize(("rc", "result", "exit_code"), [(0, "ok", 0), (3, "eni_pending", 0), (1, None, 1)])
def test_down_maps_destroy_exit_codes_and_redacts(box: dict[str, object], rc: int, result: str | None,
                                                  exit_code: int) -> None:
    _destroy(box, rc)
    out = _run(box, "down")
    assert out.returncode == exit_code, out.stderr
    assert "admin.person@example.com" not in out.stdout and "<email>" in out.stdout
    assert "guardduty=1 yes=1" in out.stdout  # GuardDuty's leftovers in COA's VPC are deleted, not left
    res = box["tmp"] / "result"  # type: ignore[operator]
    assert (res.read_text() if res.exists() else None) == result


def test_down_waits_for_stacks_still_in_progress(box: dict[str, object]) -> None:
    (box["tmp"] / "busy").write_text("2")  # type: ignore[operator]
    _destroy(box, 0)
    out = _run(box, "down")
    assert out.returncode == 0, out.stderr
    assert out.stdout.count("waiting for stacks still in progress: coa-dev-serve") == 2
    log = (box["tmp"] / "aws.log").read_text()  # type: ignore[operator]
    assert log.count("CREATE_IN_PROGRESS") == 3


def test_check_turns_ok_into_leftovers(box: dict[str, object]) -> None:
    res = box["tmp"] / "result"  # type: ignore[operator]
    res.write_text("ok")
    box["env"]["FAKE_LEFT"] = "coa-dev-network\tDELETE_FAILED\n"  # type: ignore[index]
    out = _run(box, "check")
    assert out.returncode == 0, out.stderr
    assert res.read_text() == "leftovers"
    assert (box["tmp"] / "result.leftovers").read_text() == "coa-dev-network DELETE_FAILED"  # type: ignore[operator]


def test_check_keeps_eni_pending_and_ok_when_nothing_is_left(box: dict[str, object]) -> None:
    res = box["tmp"] / "result"  # type: ignore[operator]
    res.write_text("eni_pending")
    box["env"]["FAKE_LEFT"] = "coa-dev-network\tDELETE_FAILED\n"  # type: ignore[index]
    _run(box, "check")
    assert res.read_text() == "eni_pending"
    res.write_text("ok")
    box["env"]["FAKE_LEFT"] = ""  # type: ignore[index]
    _run(box, "check")
    assert res.read_text() == "ok"


def test_prepare_refuses_a_checkout_at_another_commit(box: dict[str, object]) -> None:
    coa = box["coa"]
    coa.mkdir(parents=True)  # type: ignore[union-attr]
    git = {"cwd": coa, "check": True, "capture_output": True, "env": {**box["env"],  # type: ignore[dict-item]
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t.invalid"}}
    subprocess.run(["git", "init", "-q"], **git)  # type: ignore[call-overload]
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "not COA"], **git)  # type: ignore[call-overload]
    out = _run(box, "prepare")
    assert out.returncode == 2 and f"not {CFG['coa']['commit']}" in out.stdout


def test_pins_are_exact() -> None:
    assert len(CFG["coa"]["commit"]) == 40
    assert "@sha256:" in CFG["executor"]["binfmtImage"]
    sums = CFG["executor"]["checksums"]
    assert sums["node"].startswith("sha256:") and sums["uv"].startswith("sha256:")
    assert sums["maven"].startswith("sha512:") and sums["pnpm"].startswith("sha512-")
    src = SCRIPT.read_text()
    assert "uv sync --all-packages --locked" in src and "pip install" not in src
