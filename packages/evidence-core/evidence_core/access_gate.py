"""Ask-gate refusals in ``rp-access-log`` (SC-7, W-13).

The ask gate is the package ACL and pipeline-version check made before a run starts: ``rp-api-ask`` (``POST /ask``)
and the AgentCore Runtime entrypoint (``services/agent/main.py``, the streaming path that skips ``rp-api-ask``).
When it refuses, no run record exists, no model is called and no tool runs, so the interceptor writes nothing. This
module writes the refusal itself: one ``AccessDecision`` row per refused ask, in the same table and shape as the
interceptor's rows, keyed by package (pk ``gate#{packageId}``) so a showcase can read the refusals of one package.

The row holds who asked (the first 6 characters of the Cognito sub and the token's groups), the package and
pipeline version asked for, the question as the sha256 of its normalised text (never the text), the qid when the
caller knows the golden set, the path (``api`` | ``runtime``), the code and message returned, and the request id.
Writing is best effort: a failed write is logged and never changes the refusal.
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from typing import Any

from .ddb import to_ddb
from .ids import short
from .logs import get_logger
from .models.golden import GoldenEntry, normalise_question
from .models.governance import AccessDecision, AccessInjected, AskGateRefusal, GatePath

log = get_logger("rp.access_gate")
TTL_DAYS = 30
GATE_PK_PREFIX = "gate#"
GATE_TOOL = "ask"
TARGETS: dict[str, str] = {"api": "rp-api-ask", "runtime": "rp-agent-runtime"}


def gate_pk(package_id: str) -> str:
    return f"{GATE_PK_PREFIX}{package_id}"


def question_sha(question: str) -> str:
    return hashlib.sha256(normalise_question(question).encode()).hexdigest()


def question_id_for(question: str, golden: list[GoldenEntry] | None) -> str | None:
    """The golden qid whose question or alias is this question (normalised), else None."""
    want = normalise_question(question)
    for g in golden or []:
        if want in {normalise_question(q) for q in (g.question, *g.aliases)}:
            return g.qid
    return None


def refusal_row(*, path: GatePath, request_id: str, sub: str, groups: list[str], package_id: str,
                pipeline_version: str, question: str, code: str, reason: str,
                golden: list[GoldenEntry] | None = None, now: datetime | None = None) -> AccessDecision:
    at = (now or datetime.now(UTC)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return AccessDecision(
        at=at, run_id=gate_pk(package_id), call_id=request_id or "-", tool=GATE_TOOL, target=TARGETS[path],
        caller_sub6=short(sub or ""), groups=list(groups),
        injected=AccessInjected(pipeline_version=pipeline_version, package_id=package_id, run_id=None),
        injection_point="ask-gate", decision="deny", reason=reason,
        gate=AskGateRefusal(path=path, request_id=request_id or "-", code=code, question_sha=question_sha(question),
                            question_id=question_id_for(question, golden)),
        source="access-log", day=at[:10], ttl=int(time.time()) + TTL_DAYS * 86400)


def write(table: Any | None, row: AccessDecision) -> bool:
    """Put the row (best effort). False when there is no table or the write failed."""
    if table is None:
        return False
    try:
        table.put_item(Item=to_ddb(row.dump() | {"sk": f"{row.at}#{row.call_id}"}))
        return True
    except Exception:  # noqa: BLE001 - best effort; the refusal stands
        log.exception("ask-gate access log write failed",
                      extra={"rp": {"packageId": row.injected.package_id, "requestId": row.call_id}})
        return False
