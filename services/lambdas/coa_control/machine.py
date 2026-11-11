"""The COA environment state machine, with no AWS in it: every function takes the state item, the config and
``now`` and returns the next item (or raises ``Refusal``). ``service.py`` does the I/O around it.

States: off → starting → running → stopping → off, plus failed and partially_down (COA's destroy exits 3 while
AgentCore still holds runtime ENIs; the idle watcher retries the destroy every ``eniRetryMinutes``, up to
``eniMaxRetries`` times, then gives up to failed). Rules (docs/decisions/0006):

- up from off or from a failed spin-up; a no-op while starting or running; refused while stopping, partially_down
  or after a failed tear-down (parts of COA may still exist: tear down again first).
- down from running, failed or partially_down; a no-op while stopping or off; refused while starting unless forced,
  and refused even when forced while the spin-up's build has not been bound to a CodeBuild id yet.
- Automatic tear-down only from running (or failed after a spin-up, where stacks may be up and billing), only with
  auto tear-down on, never within ``cooldownMinutes`` of a state change, and once idle ≥ the threshold or the
  session is older than ``maxSessionHours`` (unless kept alive).
- A failed tear-down is retried automatically ``failedDownAutoRetries`` times (``failedDownRetryMinutes`` later),
  then stays failed and raises the needs-attention alarm.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from evidence_core.models.coa_env import (
    CoaActivity,
    CoaBuild,
    CoaControlConfig,
    CoaCostEstimate,
    CoaEnvItem,
    CoaEnvState,
    CoaError,
    CoaHistoryEntry,
    CoaIdleView,
    CoaOperation,
    CoaPhase,
    CoaPhaseId,
    CoaSettings,
)
from evidence_core.models.status import iso

PENDING_PREFIX = "pending:"  # a build the item claims before StartBuild has answered
HISTORY_KEEP = 20
PENDING_START_GIVE_UP = timedelta(minutes=20)  # a stopped build can hold the one concurrency slot a while
TERMINAL = {"SUCCEEDED", "FAILED", "FAULT", "TIMED_OUT", "STOPPED"}
IDLE_WATCHER = "idle-watcher"

PHASE_ORDER: tuple[CoaPhaseId, ...] = ("queued", "provisioning", "install", "pre_build", "build", "post_build", "done")
CODEBUILD_PHASE: dict[str, CoaPhaseId] = {
    "SUBMITTED": "queued", "QUEUED": "queued", "PROVISIONING": "provisioning", "DOWNLOAD_SOURCE": "provisioning",
    "INSTALL": "install", "PRE_BUILD": "pre_build", "BUILD": "build", "POST_BUILD": "post_build",
    "UPLOAD_ARTIFACTS": "done", "FINALIZING": "done", "COMPLETED": "done",
}


def phase_labels(op: CoaOperation, tag: str) -> dict[CoaPhaseId, str]:
    return {
        "queued": "Queued",
        "provisioning": "Provisioning",
        "install": "Toolchain (JDK, Node, pnpm, uv, Maven)",
        "pre_build": f"Clone COA {tag} · setup",
        "build": "make deploy-dev" if op == "up" else "make destroy-dev",
        "post_build": "Seed governed metric" if op == "up" else "Check what is left",
        "done": "Done",
    }


class Refusal(Exception):  # noqa: N818 - rendered as an API error
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def conflict(message: str) -> Refusal:
    return Refusal(409, "conflict", message)


# --- time helpers ---------------------------------------------------------------------------------------------

def parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)


def latest(values: Iterable[str | None]) -> str | None:
    ds = [d for d in (parse(v) for v in values) if d is not None]
    return iso(max(ds)) if ds else None


# --- item lifecycle ---------------------------------------------------------------------------------------------

ADOPTED_NOTICE = ("COA was already running when environment control was first deployed. Auto tear-down is off "
                  "for it until you turn it on, so nothing you set up by hand is deleted without an admin's say.")


def new_item(cfg: CoaControlConfig, now: datetime, *, state: CoaEnvState = "off", detected: bool = False,
             reason: str | None = None) -> CoaEnvItem:
    """The first row. A deployment found running at this point predates the control (hand-seeded, perhaps with
    an account-specific setup), so it is adopted with auto tear-down off and a notice; an admin turns it on."""
    item = CoaEnvItem(env=cfg.coa.stack_prefix, state=state, state_since=iso(now),
                      auto_teardown=cfg.idle.default_auto_teardown, idle_minutes=cfg.idle.default_minutes,
                      detected=detected, reason=reason)
    if state == "running":
        item.running_since = iso(now)
        item.activity = {"became_running": iso(now)}
        item.auto_teardown = False
        item.notice = ADOPTED_NOTICE
    if state == "partially_down":  # a network stack left DELETE_FAILED / deleting: the destroy retry applies
        item.eni_first_at = iso(now)
        item.next_retry_at = iso(now + timedelta(minutes=cfg.lifecycle.eni_retry_minutes))
    return item


def found_deployed(item: CoaEnvItem, now: datetime, status: str) -> CoaEnvItem:
    """Off, but CloudFormation says COA is deployed (``make deploy-dev`` by hand, the CLI's local mode): watch it
    like any running session, with the row's own auto tear-down setting."""
    nxt = transition(item, "running", now, f"found deployed ({status})", "idle-watcher")
    nxt.detected = True
    nxt.running_since = iso(now)
    nxt.session_started_at = None
    nxt.stopped_at = None
    nxt.activity = {"became_running": iso(now)}
    nxt.keep_alive_until = None
    nxt.build = None
    nxt.error = None
    return nxt


def found_gone(item: CoaEnvItem, now: datetime) -> CoaEnvItem:
    """Failed after a tear-down, but no COA stack is left (cleaned up by hand, or the delete finished late)."""
    nxt = transition(item, "off", now, "no COA stacks found", "idle-watcher")
    nxt.error = None
    nxt.endpoints = None
    nxt.running_since = None
    nxt.keep_alive_until = None
    nxt.stopped_at = item.stopped_at or iso(now)
    nxt.eni_retries = 0
    nxt.eni_first_at = None
    nxt.next_retry_at = None
    return nxt


def found_delete_failed(item: CoaEnvItem, cfg: CoaControlConfig, now: datetime, status: str) -> CoaEnvItem:
    """Off, but COA's network stack is DELETE_FAILED (AgentCore ENIs, typically): the destroy retry applies."""
    nxt = transition(item, "partially_down", now, f"COA's network stack is {status}", "idle-watcher")
    nxt.eni_first_at = iso(now)
    nxt.next_retry_at = iso(now + timedelta(minutes=cfg.lifecycle.eni_retry_minutes))
    return nxt


def transition(item: CoaEnvItem, to: CoaEnvState, now: datetime, reason: str, by: str,
               failed_op: CoaOperation | None = None) -> CoaEnvItem:
    nxt = item.model_copy(deep=True)
    if to != item.state:
        nxt.history = [*item.history, CoaHistoryEntry(at=iso(now), from_state=item.state, to_state=to, reason=reason,
                                                      by=by)][-HISTORY_KEEP:]
        nxt.state_since = iso(now)
    nxt.state = to
    nxt.reason = reason
    if to == "failed":
        nxt.failed_operation = failed_op or (item.build.operation if item.build else item.failed_operation)
    else:
        nxt.failed_operation = None
    return nxt


def claim_build(item: CoaEnvItem, op: CoaOperation, now: datetime, *, reason: str, by: str, op_id: str,
                cfg: CoaControlConfig, attempt: int = 1) -> CoaEnvItem:
    """The item after an operation is accepted: the build is claimed (``pending:<op_id>``) before StartBuild runs,
    so the conditional write is the lock; ``service`` swaps in the real build id once CodeBuild answers."""
    to: CoaEnvState = "starting" if op == "up" else "stopping"
    nxt = transition(item, to, now, reason, by)
    labels = phase_labels(op, cfg.coa.tag)
    nxt.build = CoaBuild(id=f"{PENDING_PREFIX}{op_id}", operation=op, status="SUBMITTED", started_at=iso(now),
                         reason=reason, requested_by=by, attempt=attempt,
                         phases=[CoaPhase(id=p, label=labels[p]) for p in PHASE_ORDER])
    nxt.error = None
    nxt.notice = None
    if op == "up":
        nxt.session_started_at = iso(now)
        nxt.stopped_at = None
        nxt.running_since = None
        nxt.activity = {}
        nxt.keep_alive_until = None
        nxt.eni_retries = 0
        nxt.eni_first_at = None
        nxt.next_retry_at = None
        nxt.down_auto_retries = 0
        nxt.detected = False
    elif item.state == "failed":  # a fresh tear-down after a failed one: its ENI count starts again
        nxt.eni_retries = 0
        nxt.eni_first_at = None
        nxt.next_retry_at = None
    return nxt


@dataclass(frozen=True)
class Plan:
    item: CoaEnvItem
    outcome: str  # "started" | "noop" | "stopped_and_started"
    stop_build: str | None = None  # a running build to stop first (forced tear-down during a spin-up)

    @property
    def starts(self) -> bool:
        return self.outcome in ("started", "stopped_and_started")


def check_version(item: CoaEnvItem, expected: int | None) -> None:
    if expected is not None and expected != item.version:
        raise conflict("The environment changed since you loaded it (version "
                       f"{item.version}, you saw {expected}); reload and try again.")


def request_up(item: CoaEnvItem, cfg: CoaControlConfig, now: datetime, *, by: str, op_id: str,
               idempotency_key: str | None = None) -> Plan:
    if idempotency_key and idempotency_key == item.last_idempotency_key:
        return Plan(item, "noop")
    if item.state in ("starting", "running"):
        return Plan(item, "noop")
    if item.state == "stopping":
        raise conflict("COA is tearing down. Spin it up again once the tear-down has finished.")
    if item.state == "partially_down":
        raise conflict(f"COA's tear-down is waiting for AgentCore to release its network interfaces (up to "
                       f"{cfg.lifecycle.eni_window_hours} h). COA cannot deploy over a VPC that is still being "
                       "deleted; the retry runs by itself.")
    if item.state == "failed" and item.failed_operation == "down":
        raise conflict("The last tear-down failed, so parts of COA (possibly its VPC) may still exist and a deploy "
                       "over them would fail. Tear down again first.")
    nxt = claim_build(item, "up", now, reason="spin-up requested", by=by, op_id=op_id, cfg=cfg)
    nxt.last_idempotency_key = idempotency_key
    return Plan(nxt, "started")


def request_down(item: CoaEnvItem, cfg: CoaControlConfig, now: datetime, *, by: str, op_id: str,
                 force: bool = False, reason: str = "tear-down requested",
                 idempotency_key: str | None = None) -> Plan:
    if idempotency_key and idempotency_key == item.last_idempotency_key:
        return Plan(item, "noop")
    if item.state in ("stopping", "off"):
        return Plan(item, "noop")
    stop = None
    if item.state == "starting":
        if not force:
            raise conflict("COA is spinning up. Tear it down after the spin-up finishes, or force the tear-down "
                           "(stops the running build first).")
        if item.build and item.build.id.startswith(PENDING_PREFIX):
            # its StartBuild may still be in flight: a down claimed now would lose track of that build
            raise conflict("The spin-up's build is still being started. Try the forced tear-down again in a minute, "
                           "once it has a CodeBuild id to stop.")
        stop = item.build.id if item.build else None
        reason = f"{reason} (forced during spin-up)"
    attempt = item.eni_retries + 1 if item.state == "partially_down" else 1
    nxt = claim_build(item, "down", now, reason=reason, by=by, op_id=op_id, cfg=cfg, attempt=attempt)
    nxt.last_idempotency_key = idempotency_key
    return Plan(nxt, "stopped_and_started" if stop else "started", stop_build=stop)


def request_failed_down_retry(item: CoaEnvItem, cfg: CoaControlConfig, now: datetime, *, op_id: str) -> Plan:
    n = item.down_auto_retries + 1
    plan = request_down(item, cfg, now, by=IDLE_WATCHER, op_id=op_id,
                        reason=f"automatic retry {n} of {cfg.lifecycle.failed_down_auto_retries} of a failed tear-down")
    plan.item.down_auto_retries = n
    return plan


def update_settings(item: CoaEnvItem, cfg: CoaControlConfig, *, auto_teardown: bool, idle_minutes: int) -> CoaEnvItem:
    if not cfg.idle.min_minutes <= idle_minutes <= cfg.idle.max_minutes:
        raise Refusal(400, "bad_request", f"idleMinutes must be between {cfg.idle.min_minutes} and "
                                          f"{cfg.idle.max_minutes}")
    nxt = item.model_copy(deep=True)
    nxt.auto_teardown = auto_teardown
    nxt.idle_minutes = idle_minutes
    nxt.notice = None
    return nxt


def keep_alive(item: CoaEnvItem, cfg: CoaControlConfig, now: datetime) -> CoaEnvItem:
    if item.state not in ("running", "starting"):
        raise conflict("Keep alive applies while COA is starting or running.")
    nxt = item.model_copy(deep=True)
    until = now + timedelta(minutes=cfg.idle.keep_alive_minutes)
    nxt.keep_alive_until = latest([item.keep_alive_until, iso(until)])
    nxt.activity = {**item.activity, "keep_alive": iso(now)}
    return nxt


def record_seen(item: CoaEnvItem, source: str, at: str | None, count: float | None = None) -> CoaEnvItem:
    """A background activity source (``seen``): only ever moves forward."""
    if not at:
        return item
    if latest([item.seen.get(source), at]) == item.seen.get(source):
        return item
    nxt = item.model_copy(deep=True)
    nxt.seen = {**item.seen, source: latest([item.seen.get(source), at]) or at}
    if count is not None:
        nxt.seen_count = {**item.seen_count, source: count}
    return nxt


# --- the executor's answer ---------------------------------------------------------------------------------------

def phases_from_codebuild(op: CoaOperation, tag: str, phases: list[dict[str, Any]], build_status: str
                          ) -> list[CoaPhase]:
    """CodeBuild phases (``batch_get_builds`` ``phases``) folded into the panel's seven steps."""
    labels = phase_labels(op, tag)
    grouped: dict[CoaPhaseId, list[dict[str, Any]]] = {p: [] for p in PHASE_ORDER}
    for ph in phases:
        mapped = CODEBUILD_PHASE.get(str(ph.get("phaseType")))
        if mapped:
            grouped[mapped].append(ph)
    terminal = build_status in TERMINAL
    out: list[CoaPhase] = []
    for pid in PHASE_ORDER:
        cb = grouped[pid]
        starts = [_ts(p.get("startTime")) for p in cb if p.get("startTime")]
        ends = [_ts(p.get("endTime")) for p in cb if p.get("endTime")]
        statuses = {str(p.get("phaseStatus")) for p in cb if p.get("phaseStatus")}
        if not cb:
            status = "skipped" if terminal else "pending"
        elif statuses & {"FAILED", "FAULT", "TIMED_OUT", "STOPPED", "CLIENT_ERROR"}:
            status = "failed"
        elif len(ends) == len(cb) and (pid != "done" or terminal):
            status = "succeeded"
        else:
            status = "failed" if terminal and build_status != "SUCCEEDED" else "running"
        if pid == "done" and terminal:
            status = "succeeded" if build_status == "SUCCEEDED" else "failed"
        out.append(CoaPhase(id=pid, label=labels[pid], status=status,  # type: ignore[arg-type]
                            started_at=min(starts) if starts else None,
                            ended_at=max(ends) if ends and len(ends) == len(cb) else None))
    return out


def _ts(value: Any) -> str:
    if isinstance(value, datetime):
        return iso(value)
    return iso(parse(str(value)))


def failure_message(phases: list[dict[str, Any]], build_status: str) -> tuple[str, str | None]:
    for ph in phases:
        if str(ph.get("phaseStatus")) in {"FAILED", "FAULT", "TIMED_OUT", "STOPPED", "CLIENT_ERROR"}:
            ctx = "; ".join(str(c.get("message") or "") for c in ph.get("contexts") or [] if c.get("message"))
            phase = CODEBUILD_PHASE.get(str(ph.get("phaseType")), "build")
            return (ctx or f"{ph.get('phaseType')} {ph.get('phaseStatus')}"), phase
    return f"build {build_status.lower()}", None


def apply_build(item: CoaEnvItem, cfg: CoaControlConfig, now: datetime, build: dict[str, Any]) -> CoaEnvItem | None:
    """Fold one ``batch_get_builds`` build into the item. ``None`` when the build is not the item's current one
    (an old build's late event, or a build stopped by a forced tear-down), or nothing changed."""
    cur = item.build
    if cur is None or not build_matches(cur, build):
        return None
    status = str(build.get("buildStatus") or "IN_PROGRESS")
    raw_phases = list(build.get("phases") or [])
    phases = phases_from_codebuild(cur.operation, cfg.coa.tag, raw_phases, status)
    log_url = ((build.get("logs") or {}).get("deepLink")) or cur.log_url
    nxt = item.model_copy(deep=True)
    assert nxt.build is not None
    nxt.build.id = str(build.get("id") or cur.id)  # a pending claim adopts the build it started
    nxt.build.status = status
    nxt.build.phases = phases
    nxt.build.log_url = log_url
    if status not in TERMINAL:
        return nxt if nxt.build != cur else None
    if cur.ended_at:  # already folded (a duplicate event)
        return None
    ended = _ts(build["endTime"]) if build.get("endTime") else iso(now)
    nxt.build.ended_at = ended
    exported = {v.get("name"): v.get("value") for v in build.get("exportedEnvironmentVariables") or []}
    result = str(exported.get("RP_RESULT") or "")
    if cur.operation == "down":
        nxt.stopped_at = item.stopped_at or ended  # the destroy has run: the session's cost clock stops here
    if status == "SUCCEEDED" and cur.operation == "up":
        nxt.build.result = "ok"
        nxt = transition(nxt, "running", now, "spin-up finished", "executor")
        nxt.running_since = iso(now)
        nxt.activity = {**nxt.activity, "became_running": iso(now)}
        if result == "seed_failed":
            nxt.error = CoaError(message="COA is up, but the governed-metric seed failed; run `make coa-seed` or "
                                         "read the build log.", at=iso(now), phase="post_build", log_url=log_url)
        return nxt
    if status == "SUCCEEDED" and cur.operation == "down" and result == "eni_pending":
        nxt.build.result = "eni_pending"
        retries = item.eni_retries + 1
        nxt.eni_retries = retries
        nxt.eni_first_at = item.eni_first_at or iso(now)
        if retries > cfg.lifecycle.eni_max_retries:
            nxt.next_retry_at = None
            nxt.error = CoaError(message=f"AgentCore still holds network interfaces after {retries} tear-down "
                                         "attempts; run `make coa-down` later.", at=iso(now), phase="build",
                                 log_url=log_url)
            return transition(nxt, "failed", now, "ENIs still held; gave up retrying", "executor")
        nxt.next_retry_at = iso(now + timedelta(minutes=cfg.lifecycle.eni_retry_minutes))
        return transition(nxt, "partially_down", now, "waiting for AgentCore ENIs", "executor")
    if status == "SUCCEEDED" and cur.operation == "down" and result == "leftovers":
        nxt.build.result = "leftovers"
        left = str(exported.get("RP_LEFTOVERS") or "").strip()
        nxt.error = CoaError(message="COA's destroy finished, but these stacks are still there: "
                                     f"{left or 'see the build log'}.", at=iso(now), phase="post_build",
                             log_url=log_url)
        return transition(nxt, "failed", now, "stacks left after the destroy", "executor")
    if status == "SUCCEEDED" and cur.operation == "down":
        nxt.build.result = "ok"
        nxt = transition(nxt, "off", now, "tear-down finished", "executor")
        nxt.stopped_at = iso(now)
        nxt.running_since = None
        nxt.endpoints = None
        nxt.keep_alive_until = None
        nxt.eni_retries = 0
        nxt.eni_first_at = None
        nxt.next_retry_at = None
        nxt.down_auto_retries = 0
        return nxt
    nxt.build.result = "failed"
    message, phase = failure_message(raw_phases, status)
    verb = "spin-up" if cur.operation == "up" else "tear-down"
    nxt.error = CoaError(message=f"The {verb} {status.lower().replace('_', ' ')}: {message}", at=iso(now),
                         phase=phase, log_url=log_url)
    if cur.operation == "down":
        nxt.next_retry_at = None
    return transition(nxt, "failed", now, f"{verb} {status.lower().replace('_', ' ')}", "executor")


def build_op_id(build: dict[str, Any]) -> str | None:
    env = (build.get("environment") or {}).get("environmentVariables") or []
    for v in env:
        if v.get("name") == "RP_OP_ID":
            return str(v.get("value"))
    return None


def build_matches(cur: CoaBuild, build: dict[str, Any]) -> bool:
    """The build is the row's current one: same id, or the row still holds the claim (``pending:<op_id>``) and the
    build was started for that claim (its ``RP_OP_ID``)."""
    if build.get("id") == cur.id:
        return True
    return cur.id.startswith(PENDING_PREFIX) and build_op_id(build) == cur.id.removeprefix(PENDING_PREFIX)


def start_failed(item: CoaEnvItem, now: datetime, message: str) -> CoaEnvItem:
    nxt = item.model_copy(deep=True)
    if nxt.build:
        nxt.build.status = "FAULT"
        nxt.build.result = "failed"
        nxt.build.ended_at = iso(now)
    nxt.error = CoaError(message=f"The executor did not start: {message}", at=iso(now), phase="queued")
    if nxt.build and nxt.build.operation == "down":
        nxt.stopped_at = item.stopped_at
    return transition(nxt, "failed", now, "executor did not start", "executor")


# --- idle --------------------------------------------------------------------------------------------------------

def session_floor(item: CoaEnvItem) -> datetime | None:
    return parse(item.session_started_at) or parse(item.running_since)


def merged_activity(item: CoaEnvItem) -> dict[str, str]:
    """State-machine sources plus the background ones seen during this session (older ones belong to an earlier
    session: ``seen`` is never cleared, so a write of the state machine can never lose a background time)."""
    floor = session_floor(item)
    out = dict(item.activity)
    for source, at in item.seen.items():
        if at and (floor is None or parse(at) >= floor):  # type: ignore[operator]
            out[source] = latest([out.get(source), at]) or at
    return out


def last_activity(item: CoaEnvItem) -> tuple[str | None, str | None]:
    best: tuple[str | None, str | None] = (None, None)
    for source, at in merged_activity(item).items():
        if at and (best[0] is None or parse(at) > parse(best[0])):  # type: ignore[operator]
            best = (at, source)
    return best


def _idle_base(item: CoaEnvItem) -> datetime:
    at, _ = last_activity(item)
    return parse(at) or parse(item.running_since) or parse(item.state_since)  # type: ignore[return-value]


def teardown_at(item: CoaEnvItem) -> datetime:
    deadline = _idle_base(item) + timedelta(minutes=item.idle_minutes)
    keep = parse(item.keep_alive_until)
    return max(deadline, keep) if keep else deadline


def cooldown_until(item: CoaEnvItem, cfg: CoaControlConfig) -> datetime:
    return parse(item.state_since) + timedelta(minutes=cfg.idle.cooldown_minutes)  # type: ignore[operator]


def auto_teardown_applies(item: CoaEnvItem) -> bool:
    return item.state == "running" or (item.state == "failed" and item.failed_operation == "up")


def max_session_until(item: CoaEnvItem, cfg: CoaControlConfig) -> datetime | None:
    """The hard cap: ``maxSessionHours`` after the session became running (or began), pushed out by Keep alive."""
    base = parse(item.running_since) or parse(item.session_started_at)
    if base is None:
        return None
    cap = base + timedelta(hours=cfg.idle.max_session_hours)
    keep = parse(item.keep_alive_until)
    return max(cap, keep) if keep else cap


def idle_due(item: CoaEnvItem, cfg: CoaControlConfig, now: datetime) -> str | None:
    """The reason when an automatic tear-down is due now ("idle N min" or "max session N h"), else ``None``."""
    if not item.auto_teardown or not auto_teardown_applies(item):
        return None
    if now < cooldown_until(item, cfg):
        return None
    cap = max_session_until(item, cfg)
    if now < teardown_at(item):
        if cap is not None and now >= cap:
            return f"max session {cfg.idle.max_session_hours} h"
        return None
    return f"idle {math.floor((now - _idle_base(item)).total_seconds() / 60)} min"


def failed_down_retry_due(item: CoaEnvItem, cfg: CoaControlConfig, now: datetime) -> bool:
    since = parse(item.state_since)
    return (item.state == "failed" and item.failed_operation == "down"
            and item.down_auto_retries < cfg.lifecycle.failed_down_auto_retries and since is not None
            and now >= since + timedelta(minutes=cfg.lifecycle.failed_down_retry_minutes))


def probe_due(item: CoaEnvItem, cfg: CoaControlConfig, now: datetime) -> bool:
    """Off or failed after a tear-down: time to ask CloudFormation what is really deployed."""
    if not (item.state == "off" or (item.state == "failed" and item.failed_operation == "down")):
        return False
    last = parse(item.probed_at)
    return last is None or now - last >= timedelta(minutes=cfg.idle.probe_minutes)


def needs_attention(item: CoaEnvItem, now: datetime) -> bool:
    """For the alarm: a failed state, or an activity read failing for longer than the idle threshold."""
    err = parse(item.activity_error_since)
    return item.state == "failed" or (err is not None and now - err >= timedelta(minutes=item.idle_minutes))


def eni_retry_due(item: CoaEnvItem, now: datetime) -> bool:
    nxt = parse(item.next_retry_at)
    return item.state == "partially_down" and nxt is not None and now >= nxt


def pending_start_stale(item: CoaEnvItem, now: datetime) -> bool:
    b = item.build
    return bool(b and b.id.startswith(PENDING_PREFIX) and item.state in ("starting", "stopping")
                and now - parse(b.started_at) >= PENDING_START_GIVE_UP)  # type: ignore[operator]


# --- the view ---------------------------------------------------------------------------------------------------

def settings_of(item: CoaEnvItem, cfg: CoaControlConfig) -> CoaSettings:
    return CoaSettings(auto_teardown=item.auto_teardown, idle_minutes=item.idle_minutes,
                       min_minutes=cfg.idle.min_minutes, max_minutes=cfg.idle.max_minutes,
                       keep_alive_minutes=cfg.idle.keep_alive_minutes, max_session_hours=cfg.idle.max_session_hours)


def idle_view(item: CoaEnvItem, cfg: CoaControlConfig) -> CoaIdleView | None:
    if item.state not in ("running", "starting") and not auto_teardown_applies(item):
        return None
    at, source = last_activity(item)
    merged = merged_activity(item)
    sources = [CoaActivity(source=s, at=merged.get(s), detail=_detail(item, s, merged))  # type: ignore[arg-type]
               for s in ("api_gateway", "agentcore", "provenant", "keep_alive", "became_running")]
    show_deadline = item.auto_teardown and auto_teardown_applies(item)
    cool = cooldown_until(item, cfg)
    deadline = teardown_at(item)
    cap = max_session_until(item, cfg)
    if cap is not None and cap < deadline:
        deadline = cap
    return CoaIdleView(last_activity_at=at, last_activity_source=source, sources=sources,  # type: ignore[arg-type]
                       keep_alive_until=item.keep_alive_until,
                       teardown_at=iso(max(deadline, cool)) if show_deadline else None,
                       cooldown_until=iso(cool), checked_at=item.checked_at,
                       max_session_until=iso(cap) if cap and show_deadline else None,
                       activity_error=item.activity_error, activity_error_since=item.activity_error_since)


_UNIT = {"api_gateway": "successful requests", "agentcore": "invocations"}


def _detail(item: CoaEnvItem, source: str, merged: dict[str, str]) -> str | None:
    n = item.seen_count.get(source)
    if n is None or source not in _UNIT or not merged.get(source):
        return None
    return f"{int(n)} {_UNIT[source]} in its last active 5-minute period"


def cost_of(item: CoaEnvItem, cfg: CoaControlConfig, now: datetime) -> CoaCostEstimate | None:
    if cfg.prices is None:
        return None
    rate = cfg.prices.usd_per_hour
    start = parse(item.session_started_at)
    hours = usd = None
    stopped = parse(item.stopped_at) if item.state not in ("starting", "running") else None
    if start is not None:
        end = stopped or now  # the clock stops when the destroy has run, not when the VPC is finally gone
        hours = round(max(0.0, (end - start).total_seconds()) / 3600, 3)
        usd = round(hours * rate, 2)
    residual = None
    if item.state == "partially_down":
        residual = ("The rest of COA is deleted; only its VPC, subnets and security groups are left until AgentCore "
                    "releases its network interfaces (no hourly charge of note).")
    elif item.state == "failed" and item.failed_operation == "down":
        residual = ("The tear-down failed, so some COA stacks may still be running and billing until it is retried; "
                    "this session's figure stops at the failed destroy.")
    return CoaCostEstimate(usd_per_hour=rate, session_hours=hours, session_usd=usd,
                           components=cfg.prices.components, as_of=cfg.prices.as_of, residual=residual)
