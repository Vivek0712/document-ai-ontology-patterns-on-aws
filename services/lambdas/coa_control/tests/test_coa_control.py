"""rp-coa-control: the state machine, admin enforcement and audit, idempotency, optimistic locking, the executor's
events (incl. COA's destroy exit 3, ENIs pending), and the idle watcher with an injected clock."""

from __future__ import annotations

from datetime import timedelta

import pytest

from lambdas.coa_control.tests.conftest import ADMIN, T0, VIEWER, Env, event_for


def up_and_run(env: Env) -> str:
    status, body = env.api("up")
    assert status == 202, body
    bid = body["build"]["id"]
    env.codebuild.finish(bid)
    env.svc.on_build_event(event_for(bid))
    assert env.item().state == "running"
    return bid


# --- first read -------------------------------------------------------------------------------------------------

def test_first_read_detects_state_from_cloudformation(env: Env) -> None:
    status, body = env.api("get")
    assert status == 200
    assert (body["state"], body["detected"], body["deployed"]) == ("off", True, True)
    assert body["settings"] == {"autoTeardown": True, "idleMinutes": 60, "minMinutes": 15, "maxMinutes": 480,
                                "keepAliveMinutes": 60, "maxSessionHours": 10}
    assert body["lifecycle"]["coaTag"] == "v0.3.9" and body["lifecycle"]["upMinutes"] == [40, 60]
    assert body["cli"] == ["make coa-status", "make coa-up", "make coa-down"]


def test_first_read_finds_a_running_deployment_and_its_endpoints(env: Env) -> None:
    env.cfn.network_status = "UPDATE_COMPLETE"
    _, body = env.api("get")
    assert body["state"] == "running" and body["detected"] is True
    assert body["endpoints"]["consoleUrl"] == env.cfn.console_url
    assert body["endpoints"]["restEndpoint"] == env.cfn.rest
    assert body["idle"]["sources"][-1] == {"source": "became_running", "at": "2026-11-11T09:00:00.000Z"}
    assert "sessionUsd" not in body["cost"]  # the session's start is unknown: no session estimate


def test_first_read_of_a_half_deleted_network_is_partially_down_with_a_retry(env: Env) -> None:
    env.cfn.network_status = "DELETE_FAILED"
    _, body = env.api("get")
    assert body["state"] == "partially_down" and body["eni"]["nextRetryAt"] == "2026-11-11T09:30:00.000Z"


def test_first_read_adopts_a_running_deployment_with_auto_teardown_off(env: Env) -> None:
    env.cfn.network_status = "CREATE_COMPLETE"
    _, body = env.api("get")
    assert body["state"] == "running" and body["settings"]["autoTeardown"] is False
    assert "auto tear-down is off" in body["notice"].lower()
    env.clock.advance(hours=5)
    assert env.svc.tick()["action"] == "none"  # a hand-seeded COA is not destroyed without an admin's say
    _, body = env.api("settings", {"autoTeardown": True, "idleMinutes": 60})
    assert "notice" not in body
    assert env.svc.tick()["action"] == "teardown idle 300 min"


# --- admin, audit ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("op", ["up", "down", "settings", "keep_alive"])
def test_viewers_cannot_change_anything_and_are_audited(env: Env, op: str) -> None:
    body = {"autoTeardown": False, "idleMinutes": 30} if op == "settings" else {}
    status, resp = env.api(op, body, who=VIEWER)
    assert status == 403 and resp["error"]["code"] == "forbidden"
    assert env.codebuild.started == []
    rows = env.audit_rows()
    assert len(rows) == 1
    assert (rows[0]["decision"], rows[0]["tool"], rows[0]["callerSub6"]) == ("deny", f"coa_environment.{op}", "9b11c3")
    assert rows[0]["injectionPoint"] == "coa-control" and rows[0]["runId"] == "coa-environment"


def test_viewer_reads_state_without_cost(env: Env) -> None:
    up_and_run(env)
    _, mine = env.api("get")
    _, theirs = env.api("get", who=VIEWER)
    assert mine["viewer"]["canOperate"] is True and mine["cost"]["usdPerHour"] == pytest.approx(1.306)
    assert theirs["viewer"]["canOperate"] is False and "cost" not in theirs
    assert theirs["state"] == "running" and "read only" in theirs["note"].lower()


def test_allowed_actions_are_audited(env: Env) -> None:
    env.api("up")
    rows = env.audit_rows()
    assert [(r["tool"], r["decision"]) for r in rows] == [("coa_environment.up", "allow")]
    assert rows[0]["callerSub6"] == ADMIN[0][:6] and "ttl" in rows[0]


# --- spin up ---------------------------------------------------------------------------------------------------

def test_up_claims_then_starts_one_build(env: Env) -> None:
    status, body = env.api("up")
    assert status == 202 and body["outcome"] == "started" and body["state"] == "starting"
    assert len(env.codebuild.started) == 1
    started = env.codebuild.started[0]
    assert started["RP_OPERATION"] == "up" and started["RP_REQUESTED_BY"] == ADMIN[0][:6]
    item = env.item()
    assert item.build.id == started["id"] and item.session_started_at == "2026-11-11T09:00:00.000Z"
    assert [p.id for p in item.build.phases] == ["queued", "provisioning", "install", "pre_build", "build",
                                                 "post_build", "done"]


def test_up_is_idempotent(env: Env) -> None:
    env.api("up", {"idempotencyKey": "k-1"})
    status, body = env.api("up", {"idempotencyKey": "k-1"})
    assert (status, body["outcome"]) == (200, "noop")
    status, body = env.api("up")  # starting: a second spin-up is a no-op, not a second build
    assert (status, body["outcome"]) == (200, "noop")
    assert len(env.codebuild.started) == 1


def test_up_while_running_is_a_noop(env: Env) -> None:
    up_and_run(env)
    status, body = env.api("up")
    assert (status, body["outcome"], body["state"]) == (200, "noop", "running")


def test_build_events_fold_phases_and_become_running_with_endpoints(env: Env) -> None:
    _, body = env.api("up")
    bid = body["build"]["id"]
    env.codebuild.running(bid, "BUILD")
    env.svc.on_build_event(event_for(bid, kind="CodeBuild Build Phase Change"))
    phases = {p.id: p.status for p in env.item().build.phases}
    assert phases == {"queued": "succeeded", "provisioning": "succeeded", "install": "succeeded",
                      "pre_build": "succeeded", "build": "running", "post_build": "pending", "done": "pending"}
    env.cfn.network_status = "CREATE_COMPLETE"
    env.clock.advance(minutes=45)
    env.codebuild.finish(bid)
    item = env.svc.on_build_event(event_for(bid))
    assert item.state == "running" and item.running_since == "2026-11-11T09:45:00.000Z"
    assert item.endpoints.console_url == env.cfn.console_url
    assert all(p.status == "succeeded" for p in item.build.phases)
    _, view = env.api("get")
    assert view["cost"]["sessionHours"] == 0.75 and view["cost"]["sessionUsd"] == pytest.approx(0.98)
    assert view["cost"]["provenance"] == "estimate"


def test_events_of_other_projects_and_old_builds_are_ignored(env: Env) -> None:
    _, body = env.api("up")
    assert env.svc.on_build_event(event_for(body["build"]["id"], project="someone-else")) is None
    env.codebuild.builds["old:1"] = {"id": "old:1", "buildStatus": "FAILED", "phases": []}
    assert env.svc.on_build_event(event_for("old:1")) is None
    assert env.item().state == "starting"


def test_duplicate_terminal_events_change_nothing(env: Env) -> None:
    bid = up_and_run(env)
    version = env.item().version
    assert env.svc.on_build_event(event_for(bid)) is None
    assert env.item().version == version


def test_failed_spin_up_is_surfaced_with_the_phase_and_log(env: Env) -> None:
    _, body = env.api("up")
    bid = body["build"]["id"]
    env.codebuild.finish(bid, status="FAILED", failed_phase="BUILD")
    item = env.svc.on_build_event(event_for(bid))
    assert item.state == "failed"
    assert item.error.phase == "build" and "make deploy-dev" in item.error.message
    assert item.error.log_url.endswith(bid)
    assert {p.id: p.status for p in item.build.phases}["build"] == "failed"
    status, again = env.api("up")  # a failed spin-up may be retried
    assert status == 202 and again["state"] == "starting"


def test_seed_failure_leaves_coa_running_with_an_error(env: Env) -> None:
    _, body = env.api("up")
    env.codebuild.finish(body["build"]["id"], result="seed_failed")
    item = env.svc.on_build_event(event_for(body["build"]["id"]))
    assert item.state == "running" and item.error.phase == "post_build"


# --- tear down -------------------------------------------------------------------------------------------------

def test_down_from_running_then_off(env: Env) -> None:
    up_and_run(env)
    status, body = env.api("down")
    assert status == 202 and body["state"] == "stopping"
    bid = body["build"]["id"]
    assert env.codebuild.started[-1]["RP_OPERATION"] == "down"
    env.clock.advance(minutes=25)
    env.codebuild.finish(bid)
    item = env.svc.on_build_event(event_for(bid))
    assert item.state == "off" and item.stopped_at == "2026-11-11T09:25:00.000Z" and item.endpoints is None
    _, view = env.api("get")
    assert view["cost"]["sessionHours"] == pytest.approx(25 / 60, abs=0.001)


def test_down_when_off_or_stopping_is_a_noop(env: Env) -> None:
    status, body = env.api("down")
    assert (status, body["outcome"]) == (200, "noop")
    up_and_run(env)
    env.api("down")
    status, body = env.api("down")
    assert (status, body["outcome"]) == (200, "noop")
    assert [s["RP_OPERATION"] for s in env.codebuild.started] == ["up", "down"]


def test_conflicting_operations_are_refused(env: Env) -> None:
    env.api("up")
    env.clock.advance(seconds=1)
    status, body = env.api("down")
    assert status == 409 and "spinning up" in body["error"]["message"]
    up_build = env.item().build.id
    env.codebuild.finish(up_build)
    env.svc.on_build_event(event_for(up_build))
    env.clock.advance(seconds=1)
    env.api("down")
    env.clock.advance(seconds=1)
    status, body = env.api("up")
    assert status == 409 and "tearing down" in body["error"]["message"]
    assert [r["decision"] for r in env.audit_rows()] == ["allow", "deny", "allow", "deny"]


def test_forced_down_stops_the_spin_up_and_tears_down(env: Env) -> None:
    _, body = env.api("up")
    up_build = body["build"]["id"]
    status, body = env.api("down", {"force": True})
    assert status == 202 and body["outcome"] == "stopped_and_started" and body["state"] == "stopping"
    assert env.codebuild.stopped == [up_build]
    # the stopped spin-up's late event is not the current build any more
    assert env.svc.on_build_event(event_for(up_build)) is None
    assert env.item().build.operation == "down" and "forced" in env.item().reason


def test_forced_down_waits_for_the_concurrency_slot_then_the_watcher_starts_it(env: Env) -> None:
    env.api("up")
    env.codebuild.fail_starts = 99
    status, body = env.api("down", {"force": True})
    assert status == 202 and body["build"]["id"].startswith("pending:")
    env.codebuild.fail_starts = 0
    env.clock.advance(minutes=1)
    env.svc.tick()
    assert env.item().build.id.startswith("pending:")  # too early: the API call may still be starting it
    env.clock.advance(minutes=2)
    env.svc.tick()
    item = env.item()
    assert not item.build.id.startswith("pending:") and item.build.operation == "down"


def test_executor_that_never_starts_goes_to_failed(env: Env) -> None:
    env.codebuild.fail_starts, env.codebuild.fail_with = 1, "InvalidInputException: no such project"
    status, body = env.api("up")
    assert status == 202 and body["state"] == "failed" and "did not start" in body["error"]["message"]
    assert body["failedOperation"] == "up"


def test_a_busy_executor_defers_the_start_instead_of_failing(env: Env) -> None:
    env.codebuild.fail_starts = 1  # AccountLimitExceeded: retryable
    status, body = env.api("up")
    assert status == 202 and body["state"] == "starting" and body["build"]["id"].startswith("pending:")
    env.clock.advance(minutes=2)
    assert env.svc.tick()["action"] == "started_pending"
    assert not env.item().build.id.startswith("pending:") and len(env.codebuild.started) == 1


def test_a_lost_start_response_is_adopted_not_failed_or_started_twice(env: Env) -> None:
    env.codebuild.lose_response = True
    status, body = env.api("up")
    assert status == 202 and body["state"] == "starting"
    assert body["build"]["id"] == "rp-coa-lifecycle:0001" and len(env.codebuild.started) == 1


def test_destroy_exit_3_is_partially_down_and_the_watcher_retries(env: Env) -> None:
    up_and_run(env)
    _, body = env.api("down")
    env.codebuild.finish(body["build"]["id"], result="eni_pending")
    item = env.svc.on_build_event(event_for(body["build"]["id"]))
    assert item.state == "partially_down" and item.eni_retries == 1
    assert item.next_retry_at == "2026-11-11T09:30:00.000Z"
    _, view = env.api("get")
    assert view["eni"] == {"retries": 1, "maxRetries": 16, "nextRetryAt": "2026-11-11T09:30:00.000Z",
                           "windowEndsAt": "2026-11-11T17:00:00.000Z"}
    status, refused = env.api("up")
    assert status == 409 and "network interfaces" in refused["error"]["message"]
    env.clock.advance(minutes=29)
    assert env.svc.tick()["action"] == "none"
    env.clock.advance(minutes=1)
    assert env.svc.tick()["action"] == "eni_retry"
    item = env.item()
    assert item.state == "stopping" and item.build.attempt == 2 and "waiting for AgentCore ENIs" in item.reason
    env.codebuild.finish(item.build.id)
    assert env.svc.on_build_event(event_for(item.build.id)).state == "off"


def test_eni_retries_give_up_after_the_limit(env: Env) -> None:
    up_and_run(env)
    env.api("down")
    for _ in range(17):
        bid = env.item().build.id
        env.codebuild.finish(bid, result="eni_pending")
        env.svc.on_build_event(event_for(bid))
        if env.item().state == "failed":
            break
        env.clock.advance(minutes=30)
        env.svc.tick()
    item = env.item()
    assert item.state == "failed" and item.eni_retries == 17 and "make coa-down" in item.error.message


def test_failed_tear_down_is_retried_once_then_stays_failed_and_needs_attention(env: Env) -> None:
    import json

    up_and_run(env)
    _, body = env.api("down")
    env.cfn.network_status = "DELETE_FAILED"
    env.codebuild.finish(body["build"]["id"], status="FAILED", failed_phase="BUILD")
    env.svc.on_build_event(event_for(body["build"]["id"]))
    assert env.item().state == "failed" and env.item().failed_operation == "down"
    env.clock.advance(minutes=29)
    assert env.svc.tick()["action"] == "none"
    assert json.loads(env.emitted[-1])["NeedsAttention"] == 1
    env.clock.advance(minutes=1)
    assert env.svc.tick()["action"] == "failed_down_retry"
    item = env.item()
    assert item.state == "stopping" and item.build.requested_by == "idle-watcher" and item.down_auto_retries == 1
    assert "automatic retry 1 of 1" in env.audit_rows()[-1]["reason"]
    env.codebuild.finish(item.build.id, status="FAILED", failed_phase="BUILD")
    env.svc.on_build_event(event_for(item.build.id))
    env.clock.advance(hours=6)  # the network stack is still there: the probe leaves it failed
    assert env.svc.tick()["action"] == "none"
    assert env.item().state == "failed"
    status, _ = env.api("down")  # an admin may retry by hand
    assert status == 202


def test_spin_up_is_refused_after_a_failed_tear_down(env: Env) -> None:
    up_and_run(env)
    env.cfn.network_status = "DELETE_FAILED"
    _, body = env.api("down")
    env.codebuild.finish(body["build"]["id"], status="FAILED", failed_phase="BUILD")
    env.svc.on_build_event(event_for(body["build"]["id"]))
    status, refused = env.api("up")
    assert status == 409 and "tear down again" in refused["error"]["message"].lower()
    _, view = env.api("get")
    assert "endpoints" not in view and view["failedOperation"] == "down"
    assert "residual" in view["cost"] and "may still be running" in view["cost"]["residual"]


# --- settings, keep alive, locking -----------------------------------------------------------------------------

def test_settings_bounds_and_locking(env: Env) -> None:
    _, body = env.api("get")
    v = body["version"]
    status, resp = env.api("settings", {"autoTeardown": True, "idleMinutes": 14, "expectedVersion": v})
    assert status == 400 and "between 15 and 480" in resp["error"]["message"]
    status, resp = env.api("settings", {"autoTeardown": True, "idleMinutes": 481})
    assert status == 400
    status, resp = env.api("settings", {"autoTeardown": False, "idleMinutes": 15, "expectedVersion": v})
    assert status == 200 and resp["settings"]["autoTeardown"] is False and resp["settings"]["idleMinutes"] == 15
    status, resp = env.api("settings", {"autoTeardown": True, "idleMinutes": 480, "expectedVersion": v})
    assert status == 409 and "reload" in resp["error"]["message"]
    status, resp = env.api("settings", {"autoTeardown": True, "idleMinutes": 480, "expectedVersion": v + 1})
    assert status == 200 and resp["settings"]["idleMinutes"] == 480


def test_bad_bodies_are_400(env: Env) -> None:
    assert env.api("settings", {"autoTeardown": True})[0] == 400
    assert env.api("up", {"idempotencyKey": "spaces are not allowed"})[0] == 400


def test_lost_race_is_a_409_not_a_second_build(env: Env) -> None:
    from lambdas.coa_control.service import Conflict

    env.api("get")
    store = env.svc.store
    real_put = store.put

    def racing_put(item, expected):  # another writer bumps the version between read and write
        if item.state == "starting":
            store.put = real_put
            real_put(store.get("coa-dev"), store.get("coa-dev").version)
        return real_put(item, expected)

    store.put = racing_put
    status, body = env.api("up")
    assert status == 409 and env.codebuild.started == []
    with pytest.raises(Conflict):
        real_put(env.item(), env.item().version - 1)


def test_keep_alive_extends_the_deadline(env: Env) -> None:
    status, _ = env.api("keep_alive")
    assert status == 409  # off
    up_and_run(env)
    env.api("settings", {"autoTeardown": True, "idleMinutes": 15})
    env.clock.advance(minutes=10)
    _, body = env.api("keep_alive")
    assert body["idle"]["keepAliveUntil"] == "2026-11-11T10:10:00.000Z"
    assert body["idle"]["teardownAt"] == "2026-11-11T10:10:00.000Z"
    env.clock.advance(minutes=59)
    assert env.svc.tick()["action"] == "none"
    env.clock.advance(minutes=1)
    assert env.svc.tick()["action"].startswith("teardown idle")


# --- the idle watcher --------------------------------------------------------------------------------------------

def test_viewing_is_not_activity(env: Env) -> None:
    up_and_run(env)
    for minute in range(0, 61, 5):
        env.clock.now = T0 + timedelta(minutes=minute)
        env.api("get")
        env.api("get", who=VIEWER)
    assert env.svc.tick()["action"] == "teardown idle 60 min"
    assert env.item().reason == "idle 60 min"


def test_idle_boundary_59_vs_60_minutes(env: Env) -> None:
    up_and_run(env)
    env.clock.advance(minutes=59, seconds=59)
    assert env.svc.tick()["action"] == "none"
    env.clock.advance(seconds=1)
    result = env.svc.tick()
    assert result["action"] == "teardown idle 60 min"
    item = env.item()
    assert item.state == "stopping" and item.build.requested_by == "idle-watcher"
    row = env.audit_rows()[-1]
    assert row["callerSub6"] == "idle-w" and "automatic tear-down: idle 60 min" in row["reason"]


def test_coa_activity_postpones_the_tear_down(env: Env) -> None:
    up_and_run(env)
    env.clock.advance(minutes=40)
    env.cloudwatch.points["api_gateway"] = [(T0 + timedelta(minutes=30), 3.0), (T0 + timedelta(minutes=35), 0.0)]
    env.cloudwatch.points["api_4xx"] = [(T0 + timedelta(minutes=30), 1.0)]
    env.cloudwatch.points["agentcore"] = [(T0 + timedelta(minutes=20), 1.0)]
    env.clock.advance(minutes=50)  # 90 min after start; API activity at 30-35 min -> idle 55 min
    assert env.svc.tick()["action"] == "none"
    item = env.item()
    assert item.seen["api_gateway"] == "2026-11-11T09:35:00.000Z" and item.seen_count["api_gateway"] == 2
    assert item.seen["agentcore"] == "2026-11-11T09:25:00.000Z"
    _, view = env.api("get")
    assert view["idle"]["lastActivitySource"] == "api_gateway"
    assert view["idle"]["sources"][0]["detail"] == "2 successful requests in its last active 5-minute period"
    assert view["idle"]["teardownAt"] == "2026-11-11T10:35:00.000Z"
    env.clock.advance(minutes=5)
    assert env.svc.tick()["action"] == "teardown idle 60 min"
    api_q = env.cloudwatch.queries[0]["MetricDataQueries"]
    assert [q["Id"] for q in api_q] == ["apic", "apie", "api"]
    assert api_q[0]["MetricStat"]["Metric"]["Dimensions"] == [{"Name": "ApiName", "Value": "coa-dev-api"}]
    assert api_q[1]["MetricStat"]["Metric"]["MetricName"] == "4XXError"
    assert api_q[2]["Expression"] == "FILL(apic, 0) - FILL(apie, 0)"
    runtime_q = env.cloudwatch.queries[1]["MetricDataQueries"]
    assert len(runtime_q) == 1  # the one coa_dev_ runtime; other agents are not COA's activity
    assert env.cloudwatch.list_calls[0]["RecentlyActive"] == "PT3H"


def test_provenant_touch_is_activity(env: Env) -> None:
    up_and_run(env)
    env.clock.advance(minutes=50)
    status, _ = env.api("touch", who=VIEWER)
    assert status == 200
    env.clock.advance(minutes=20)
    assert env.svc.tick()["action"] == "none"
    assert env.item().seen["provenant"] == "2026-11-11T09:50:00.000Z"


def test_a_failed_activity_read_is_shown_then_falls_back_to_the_last_known_activity(env: Env) -> None:
    import json

    up_and_run(env)
    env.cloudwatch.fail = True
    env.clock.advance(minutes=20)
    assert env.svc.tick()["action"] == "none"  # failing for 0 min
    _, view = env.api("get")
    assert "CloudWatch could not be read" in view["idle"]["activityError"]
    assert view["idle"]["activityErrorSince"] == "2026-11-11T09:20:00.000Z"
    env.clock.advance(minutes=59)
    assert env.svc.tick()["action"] == "none"  # failing 59 min < the 60-minute threshold
    assert json.loads(env.emitted[-1])["NeedsAttention"] == 0
    env.clock.advance(minutes=1)
    assert env.svc.tick()["action"] == "teardown idle 80 min"  # decided on what it last saw


def test_a_recovered_activity_read_clears_the_error(env: Env) -> None:
    up_and_run(env)
    env.cloudwatch.fail = True
    env.svc.tick()
    env.cloudwatch.fail = False
    env.clock.advance(minutes=5)
    env.svc.tick()
    assert env.item().activity_error is None and env.item().activity_error_since is None


def test_auto_teardown_off_and_cooldown(env: Env) -> None:
    up_and_run(env)
    env.api("settings", {"autoTeardown": False, "idleMinutes": 15})
    env.clock.advance(hours=5)
    assert env.svc.tick()["action"] == "none"
    env.api("settings", {"autoTeardown": True, "idleMinutes": 15})
    assert env.svc.tick()["action"] == "teardown idle 300 min"


def test_cooldown_after_a_state_change(env: Env) -> None:
    bid = up_and_run(env)
    env.api("settings", {"autoTeardown": True, "idleMinutes": 15})
    # activity long before the environment became running cannot shorten the cool-down
    env.svc.store.set_seen("coa-dev", "api_gateway", "2026-11-11T07:00:00.000Z")
    assert env.svc.tick()["action"] == "none"
    env.clock.advance(minutes=14)
    assert env.svc.tick()["action"] == "none"
    env.clock.advance(minutes=1)
    assert env.svc.tick()["action"] == "teardown idle 15 min"
    assert bid != env.item().build.id


def test_never_acts_while_a_build_runs_but_reconciles_a_missed_event(env: Env) -> None:
    _, body = env.api("up")
    bid = body["build"]["id"]
    env.clock.advance(hours=3)
    assert env.svc.tick()["action"] == "none"
    assert env.item().state == "starting"
    env.codebuild.finish(bid)
    assert env.svc.tick()["action"] == "reconciled"
    assert env.item().state == "running"


def test_failed_spin_up_is_torn_down_when_idle(env: Env) -> None:
    _, body = env.api("up")
    env.codebuild.finish(body["build"]["id"], status="FAILED", failed_phase="BUILD")
    env.svc.on_build_event(event_for(body["build"]["id"]))
    env.clock.advance(minutes=61)
    assert env.svc.tick()["action"] == "teardown idle 61 min"


def test_tick_stamps_checked_at_without_bumping_the_version(env: Env) -> None:
    up_and_run(env)
    v = env.item().version
    env.clock.advance(minutes=5)
    env.svc.tick()
    assert env.item().version == v and env.item().checked_at == "2026-11-11T09:05:00.000Z"


# --- the Lambda entry point ------------------------------------------------------------------------------------

def test_handler_dispatch(env: Env) -> None:
    from lambdas.coa_control import handler as h

    h.set_service(env.svc)
    try:
        out = h.handler({"source": "rp-api", "op": "get", "caller": {"sub": VIEWER[0], "groups": list(VIEWER[1])}})
        assert out["status"] == 200 and out["body"]["viewer"]["canOperate"] is False
        assert h.handler({"source": "rp-api", "op": "nope", "caller": {"sub": ADMIN[0], "groups": ["rp-admin"]}}
                         )["status"] == 404
        assert h.handler({"op": "tick"})["action"] == "none"
        assert h.handler({"source": "aws.codebuild", "detail": {"project-name": "x", "build-id": "y"}}) == {
            "state": None}
        assert h.handler({"something": "else"}) == {"ignored": True}
    finally:
        h.set_service(None)


# --- review round: drift, races, caps, background writes -----------------------------------------------------

def test_off_but_deployed_by_hand_is_found_and_then_watched(env: Env) -> None:
    env.api("get")  # the row exists: off
    env.cfn.network_status = "CREATE_COMPLETE"  # `make deploy-dev` from a laptop
    env.clock.advance(minutes=14)
    assert env.svc.tick()["action"] == "none"  # probed at creation; next probe after 15 min
    env.clock.advance(minutes=1)
    assert env.svc.tick()["action"] == "detected running"
    item = env.item()
    assert item.state == "running" and item.detected and item.auto_teardown is True
    assert item.reason.startswith("found deployed") and item.endpoints.rest_endpoint == env.cfn.rest
    assert env.audit_rows()[-1]["tool"] == "coa_environment.detect"
    env.clock.advance(minutes=60)
    assert env.svc.tick()["action"] == "teardown idle 60 min"


def test_off_with_a_delete_failed_network_is_partially_down(env: Env) -> None:
    env.api("get")
    env.cfn.network_status = "DELETE_FAILED"
    env.clock.advance(minutes=15)
    _, view = env.api("get")  # a GET probes too, at most every probeMinutes
    assert view["state"] == "partially_down" and view["eni"]["nextRetryAt"] == "2026-11-11T09:45:00.000Z"


def test_failed_tear_down_with_nothing_left_becomes_off(env: Env) -> None:
    up_and_run(env)
    env.cfn.network_status = "DELETE_FAILED"
    _, body = env.api("down")
    env.codebuild.finish(body["build"]["id"], status="FAILED", failed_phase="BUILD")
    env.svc.on_build_event(event_for(body["build"]["id"]))
    env.cfn.network_status = None  # cleaned up by hand
    env.clock.advance(minutes=15)
    assert env.svc.tick()["action"] == "detected off"
    item = env.item()
    assert item.state == "off" and item.reason == "no COA stacks found" and item.error is None
    assert env.api("up")[0] == 202  # spin-up is possible again


def test_destroy_that_leaves_stacks_is_failed_not_off(env: Env) -> None:
    up_and_run(env)
    _, body = env.api("down")
    env.codebuild.finish(body["build"]["id"], result="leftovers",
                         exported={"RP_LEFTOVERS": "coa-dev-network DELETE_FAILED"})
    item = env.svc.on_build_event(event_for(body["build"]["id"]))
    assert item.state == "failed" and item.failed_operation == "down" and item.build.result == "leftovers"
    assert "coa-dev-network DELETE_FAILED" in item.error.message


def test_forced_down_is_refused_while_the_spin_up_is_unbound(env: Env) -> None:
    env.codebuild.fail_starts = 1  # the spin-up's start is deferred: still pending
    env.api("up")
    status, body = env.api("down", {"force": True})
    assert status == 409 and "still being started" in body["error"]["message"]


def test_forced_down_waits_for_a_stopped_build_to_release_the_slot(env: Env) -> None:
    cb = env.codebuild
    cb.enforce_concurrency, cb.stop_ticks = True, 3
    _, body = env.api("up")
    up_build = body["build"]["id"]
    status, body = env.api("down", {"force": True})
    assert status == 202 and body["state"] == "stopping" and body["build"]["id"].startswith("pending:")
    assert cb.stopped == [up_build]
    for minute in (2, 4, 6):  # the stopped build still holds the one slot: keep trying, never fail
        env.clock.now = T0 + timedelta(minutes=minute)
        assert env.svc.tick()["action"] == "pending_deferred"
        assert env.item().state == "stopping"
        cb.settle()
    env.clock.now = T0 + timedelta(minutes=8)
    assert env.svc.tick()["action"] == "started_pending"
    item = env.item()
    assert item.state == "stopping" and item.build.operation == "down" and not item.build.id.startswith("pending:")
    assert [s["RP_OPERATION"] for s in cb.started] == ["up", "down"]


def test_a_pending_claim_gives_up_after_the_limit(env: Env) -> None:
    env.codebuild.fail_starts = 99
    env.api("up")
    env.clock.advance(minutes=19)
    assert env.svc.tick()["action"] == "pending_deferred"
    env.clock.advance(minutes=1)
    assert env.svc.tick()["action"] == "gave_up_pending_start"
    assert env.item().state == "failed" and "within 20 minutes" in env.item().error.message


def test_recovery_after_the_idempotency_window_adopts_the_build_it_started(env: Env) -> None:
    cb = env.codebuild
    cb.fail_starts = 1
    _, body = env.api("up")
    op_id = body["build"]["id"].removeprefix("pending:")
    # the start did happen (e.g. a retry on another path) with a different token, after CodeBuild forgot this one
    cb.start_build(projectName="rp-coa-lifecycle", idempotencyToken="other",
                   environmentVariablesOverride=[{"name": "RP_OPERATION", "value": "up", "type": "PLAINTEXT"},
                                                 {"name": "RP_OP_ID", "value": op_id, "type": "PLAINTEXT"}])
    env.clock.advance(minutes=6)
    assert env.svc.tick()["action"] == "adopted_pending"
    assert env.item().build.id == "rp-coa-lifecycle:0001" and len(cb.started) == 1


def test_an_event_binds_a_build_whose_bind_lost_every_race(env: Env) -> None:
    from lambdas.coa_control.service import Conflict

    store = env.svc.store
    real_put = store.put

    def put(item, expected):  # the claim lands; every bind afterwards loses
        if item.build and not item.build.id.startswith("pending:"):
            raise Conflict("lost")
        return real_put(item, expected)

    store.put = put
    status, body = env.api("up")
    assert status == 202 and body["state"] == "starting"  # not a 409: the build is running
    store.put = real_put
    assert env.item().build.id.startswith("pending:")
    bid = env.codebuild.started[0]["id"]
    env.codebuild.running(bid, "INSTALL")
    item = env.svc.on_build_event(event_for(bid, kind="CodeBuild Build Phase Change"))
    assert item.build.id == bid and {p.id: p.status for p in item.build.phases}["install"] == "running"


def test_unauthenticated_traffic_is_not_activity(env: Env) -> None:
    up_and_run(env)
    env.cloudwatch.points["api_gateway"] = [(T0 + timedelta(minutes=30), 40.0)]
    env.cloudwatch.points["api_4xx"] = [(T0 + timedelta(minutes=30), 40.0)]  # all 401/403: a scanner
    env.clock.advance(minutes=60)
    assert env.svc.tick()["action"] == "teardown idle 60 min"
    assert "api_gateway" not in env.item().seen


def test_agentcore_metrics_are_not_truncated(env: Env) -> None:
    from lambdas.coa_control.tests.conftest import _runtime_metric

    env.cloudwatch.agentcore_metrics = [_runtime_metric(f"coa_dev_rt{i}") for i in range(30)]
    up_and_run(env)
    env.clock.advance(minutes=5)
    env.svc.tick()
    assert len(env.cloudwatch.queries[1]["MetricDataQueries"]) == 30


def test_max_session_cap_unless_kept_alive(env: Env) -> None:
    up_and_run(env)
    env.api("settings", {"autoTeardown": True, "idleMinutes": 480})
    for hour in range(1, 10):  # someone (or an open console tab) uses COA every hour
        env.clock.now = T0 + timedelta(hours=hour)
        env.cloudwatch.points["api_gateway"] = [(env.clock.now - timedelta(minutes=5), 3.0)]
        assert env.svc.tick()["action"] == "none"
    env.clock.now = T0 + timedelta(hours=9, minutes=30)
    _, view = env.api("keep_alive")
    assert view["idle"]["maxSessionUntil"] == "2026-11-11T19:30:00.000Z"  # 10 h cap, pushed out by Keep alive
    env.clock.now = T0 + timedelta(hours=10)
    env.cloudwatch.points["api_gateway"] = [(env.clock.now - timedelta(minutes=5), 3.0)]
    assert env.svc.tick()["action"] == "none"  # kept alive until 10:30 h
    env.clock.now = T0 + timedelta(hours=10, minutes=30)
    env.cloudwatch.points["api_gateway"] = [(env.clock.now - timedelta(minutes=5), 3.0)]
    assert env.svc.tick()["action"] == "teardown max session 10 h"
    assert "automatic tear-down: max session 10 h" in env.audit_rows()[-1]["reason"]


def test_a_versioned_write_never_loses_a_background_activity(env: Env) -> None:
    up_and_run(env)
    stale = env.item()
    env.clock.advance(minutes=10)
    env.api("touch", who=VIEWER)  # background write after `stale` was read
    env.svc.store.put(stale.model_copy(update={"idle_minutes": 30}), stale.version)
    item = env.item()
    assert item.idle_minutes == 30 and item.seen["provenant"] == "2026-11-11T09:10:00.000Z"


def test_cost_clock_stops_when_the_destroy_has_run(env: Env) -> None:
    up_and_run(env)
    env.clock.advance(minutes=30)
    _, body = env.api("down")
    env.clock.advance(minutes=24)
    env.codebuild.finish(body["build"]["id"], result="eni_pending", at=env.clock.now)
    env.svc.on_build_event(event_for(body["build"]["id"]))
    env.clock.advance(hours=5)
    _, view = env.api("get")
    assert view["state"] == "partially_down" and view["cost"]["sessionHours"] == pytest.approx(0.9)
    assert "no hourly charge of note" in view["cost"]["residual"]


def test_cli_calls_are_audited_with_the_reported_operator(env: Env) -> None:
    from lambdas.coa_control import handler as h

    h.set_service(env.svc)
    try:
        out = h.handler({"source": "cli", "op": "up", "body": {}, "operator": "assumed-role/Operator/session"})
        assert out["status"] == 202 and out["body"]["build"]["requestedBy"] == "cli"
        assert env.audit_rows()[-1]["reason"].endswith("(cli: assumed-role/Operator/session)")
    finally:
        h.set_service(None)
