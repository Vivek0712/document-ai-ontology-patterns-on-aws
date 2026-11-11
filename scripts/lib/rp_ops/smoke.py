"""smoke.sh: end-to-end checks against the deployed stack, walking the user journey
and validating every response against the contract models."""

from __future__ import annotations

import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from evidence_core.config import ConfigDiff
from evidence_core.models.answer import AnswerEvent, AskRequest, ErrorEvent, RunEvent, RunStartEvent
from evidence_core.models.api import (
    DocumentDetail,
    Me,
    PackageDetail,
    PackageList,
    PackageStatus,
    PipelineList,
    StageDetail,
    StageEventPage,
    UnitList,
)

from .api import ApiClient, ApiHttpError
from .auth import bearer, sign_in
from .aws import Aws
from .seed import DEMO_PACKAGE_ID
from .verify import Check

HEADLINE_QUESTION = "What is the total income across this package?"


def _check(name: str, fn: Callable[[], str]) -> Check:
    try:
        return Check(name, "PASS", fn())
    except ApiHttpError as exc:
        return Check(name, "FAIL", str(exc))
    except (AssertionError, ValueError) as exc:
        return Check(name, "FAIL", f"{type(exc).__name__}: {exc}")


def run_smoke(aws: Aws, *, package_id: str = DEMO_PACKAGE_ID, ask: bool = False) -> list[Check]:
    api_url = aws.ssm("api_url")
    assert api_url
    a = sign_in(aws, "user-a")
    b = sign_in(aws, "user-b")
    api = ApiClient(api_url, bearer(a))
    api_b = ApiClient(api_url, bearer(b))
    checks: list[Check] = []
    state: dict[str, Any] = {}

    def me() -> str:
        m = Me.load(api.get("me"))
        assert "lending-analyst" in m.groups, f"groups {m.groups}"
        return f"user-a groups {m.groups}"

    def id_token_rejected() -> str:
        if not a.id_token:
            return "no ID token from this sign-in method (skipped)"
        status, _ = api.request("GET", "me", authorization=bearer(a, "id"))
        assert status == 401, f"ID token got {status}, expected 401 (AuthorizationScopes missing?)"
        return "ID token -> 401"

    def pipelines() -> str:
        items = PipelineList.load(api.get("pipelines")).items
        pvs = sorted(i.pipeline_version for i in items)
        assert {"1.0", "2.3.1"} <= set(pvs), f"pipelines {pvs} (run scripts/seed.sh)"
        return f"versions {pvs}"

    def diff() -> str:
        d = ConfigDiff.load(api.get("pipelines/diff", a="1.0", b="2.3.1"))
        assert d.changed_blocks == ["preprocessing"], f"changed {d.changed_blocks}"
        assert d.prompt_sha.equal and d.model_id.equal, "prompt or model differs"
        return d.summary

    def packages() -> str:
        ids = [p.package_id for p in PackageList.load(api.get("packages")).items]
        assert package_id in ids, f"{package_id} not visible to user-a ({ids})"
        detail = PackageDetail.load(api.get(f"packages/{package_id}"))
        state["docs"] = detail.documents
        return f"{len(ids)} package(s); {package_id} has {len(detail.documents)} document(s)"

    def status() -> str:
        s = PackageStatus.load(api.get(f"packages/{package_id}/status"))
        state["status"] = s
        lanes = ", ".join(f"{pv} {r.state} {r.indexed}/{r.expected}" for pv, r in sorted(s.lanes.items()))
        return f"askReady {s.ask_ready}; {lanes or 'no lanes yet'}"

    def denied() -> str:
        status_code, _ = api_b.request("GET", f"packages/{package_id}")
        assert status_code in (403, 404), f"user-b got {status_code}"
        return f"user-b -> {status_code}"

    def stage_inspection() -> str:
        docs = state.get("docs") or []
        if not docs:
            return "no documents uploaded yet (skipped)"
        doc = docs[0]
        q = urllib.parse.quote(doc.doc_id, safe="")
        detail = DocumentDetail.load(api.get(f"documents/{q}"))
        inspected = 0
        for pv, lane in detail.lanes.items():
            for rec in lane.stages:
                StageDetail.load(api.get(f"documents/{q}/lanes/{pv}/stages/{rec.stage_id.value}"))
                inspected += 1
            StageEventPage.load(api.get(f"documents/{q}/lanes/{pv}/events", limit=20))
            units = UnitList.load(api.get(f"documents/{q}/lanes/{pv}/units"))
            state.setdefault("units", {})[pv] = len(units.items)
        return f"{doc.file_name}: {inspected} stage records across {len(detail.lanes)} lane(s); units {state['units']}"

    checks += [
        _check("GET /me (access token)", me),
        _check("GET /me (ID token rejected)", id_token_rejected),
        _check("GET /pipelines", pipelines),
        _check("GET /pipelines/diff", diff),
        _check("GET /packages + detail", packages),
        _check("GET /packages/{id}/status", status),
        _check("ACL: user-b denied", denied),
        _check("stage inspector routes", stage_inspection),
    ]
    if ask:
        for pv in ("1.0", "2.3.1"):
            checks.append(_check(f"POST /ask {pv}", lambda pv=pv: _ask(api, package_id, pv)))
    web = aws.ssm("web_url", required=False)
    if web:
        checks.append(_check("web UI", lambda: _http_ok(web)))
    return checks


def _ask(api: ApiClient, package_id: str, pv: str) -> str:
    seen: list[str] = []
    answer: AnswerEvent | None = None
    run: RunEvent | None = None
    for ev in api.ask(AskRequest(question=HEADLINE_QUESTION, package_id=package_id, pipeline_version=pv)):
        seen.append(ev.type)
        if isinstance(ev, ErrorEvent):
            raise ValueError(f"{ev.code}: {ev.message}")
        if isinstance(ev, AnswerEvent):
            answer = ev
        if isinstance(ev, RunEvent):
            run = ev
    assert seen and seen[0] == RunStartEvent.model_fields["type"].default, f"first event {seen[:1]}"
    assert answer is not None and run is not None, f"events {seen}"
    resolvable = sum(1 for c in answer.claims if c.resolvable)
    return (f"total {answer.total} · {len(answer.claims)} claims ({resolvable} region-resolvable) · "
            f"run {run.run_id} · {run.latency_ms} ms")


def _http_ok(url: str) -> str:
    with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 (CloudFront URL from SSM)
        assert resp.status == 200, f"{resp.status}"
        return f"{url} -> {resp.status}"
