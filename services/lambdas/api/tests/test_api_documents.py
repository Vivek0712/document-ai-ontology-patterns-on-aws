from __future__ import annotations

from lambdas.api.routes import DOC_EVENTS, DOC_REPROCESS, DOC_STAGE, DOC_UNITS, DOCUMENT
from lambdas.api.tests.support import SUB_A, SUB_B


def test_document_detail_has_both_lanes(call, seeded) -> None:
    status, d = call("GET", DOCUMENT, path_params={"docId": seeded.doc_id})
    assert status == 200
    assert d["document"]["fileName"] == seeded.file_name and d["canWrite"] is True
    assert set(d["lanes"]) == {"1.0", "2.3.1"}
    assert d["lanes"]["2.3.1"]["lane"]["state"] == "SUCCEEDED"
    assert len(d["lanes"]["2.3.1"]["stages"]) == 10


def test_document_acl(call, seeded) -> None:
    status, err = call("GET", DOCUMENT, path_params={"docId": seeded.doc_id}, sub=SUB_B, groups=["markets-analyst"])
    assert status == 403
    status, _ = call("GET", DOCUMENT, path_params={"docId": "nope-000000"})
    assert status == 404


def test_stage_detail_shows_versions_events_and_artifacts(call, seeded) -> None:
    status, s = call("GET", DOC_STAGE, path_params={"docId": seeded.doc_id, "pv": "2.3.1", "stageId": "extract"})
    assert status == 200
    rec = s["record"]
    assert rec["state"] == "SUCCEEDED" and rec["attempt"] == 1 and rec["engine"] == "docling"
    assert rec["versions"]["engineVersion"] == "docling-serve-cpu 1.38.2"
    assert rec["artifacts"][0]["kind"] == "docling_json"
    assert s["definition"]["functions"] == ["rp-stage-status", "rp-docling-call", "rp-textract-page"]
    assert [e["event"] for e in s["events"]] == ["STARTED", "HEARTBEAT", "SUCCEEDED"]
    assert s["previousStage"] == "route" and s["nextStage"] == "classify"


def test_stage_detail_after_reprocess_keeps_previous_run(call, seeded, env) -> None:
    """A second execution archives the first run; the inspector shows only the new run's events and
    lists the earlier run under history."""
    from evidence_core.enums import LaneState, StageId
    from evidence_core.models.status import LaneRecord, StageResult, iso
    from evidence_core.stages import resolve_stages
    from evidence_core.status import StatusWriter

    tbl, pv = env.deps.table("doc_status"), "2.3.1"
    lane = StatusWriter(tbl, seeded.doc_id, "x", pv, StageId.REGISTER).get_lane()
    w = StatusWriter(tbl, seeded.doc_id, lane.package_id, pv, StageId.REGISTER)
    second = (lane.execution_arn or "arn:x") + "-reprocess"
    w.init_lane(resolve_stages(env.deps.pipeline(pv).config),
                LaneRecord(doc_id=seeded.doc_id, package_id=lane.package_id, pipeline_version=pv,
                           state=LaneState.RUNNING, execution_arn=second, started_at=iso()))
    ex = w.for_stage(StageId.EXTRACT)
    ex.start(execution_arn=second)
    ex.succeed(StageResult(stage_id=StageId.EXTRACT, state="SUCCEEDED", headline="second run"))
    status, s = call("GET", DOC_STAGE, path_params={"docId": seeded.doc_id, "pv": pv, "stageId": "extract"})
    assert status == 200
    assert s["record"]["headline"] == "second run" and s["record"]["executionArn"] == second
    assert [e["event"] for e in s["events"]] == ["STARTED", "SUCCEEDED"]  # not mixed with the first run
    assert len(s["history"]) == 1 and s["history"][0]["headline"] == "extract ok"
    assert s["history"][0]["artifacts"][0]["kind"] == "docling_json"


def test_stage_detail_skipped_has_reason(call, seeded) -> None:
    status, s = call("GET", DOC_STAGE, path_params={"docId": seeded.doc_id, "pv": "1.0", "stageId": "graph"})
    assert status == 200
    assert s["record"]["state"] == "SKIPPED" and "ontology" in s["record"]["skipReason"]
    assert s["definition"]["enabled"] is False
    assert [e["event"] for e in s["events"]] == ["SKIPPED"]


def test_stage_detail_rejects_unknown_stage(call, seeded) -> None:
    status, err = call("GET", DOC_STAGE, path_params={"docId": seeded.doc_id, "pv": "2.3.1", "stageId": "x"})
    assert status == 400


def test_events_paginate_oldest_first(call, seeded) -> None:
    status, page = call("GET", DOC_EVENTS, path_params={"docId": seeded.doc_id, "pv": "2.3.1"}, query={"limit": "5"})
    assert status == 200 and len(page["items"]) == 5 and page["nextCursor"]
    ats = [e["at"] for e in page["items"]]
    assert ats == sorted(ats)
    status, page2 = call("GET", DOC_EVENTS, path_params={"docId": seeded.doc_id, "pv": "2.3.1"},
                         query={"limit": "500", "cursor": page["nextCursor"]})
    assert status == 200 and page2["items"][0]["at"] >= ats[-1]
    status, _ = call("GET", DOC_EVENTS, path_params={"docId": seeded.doc_id, "pv": "2.3.1"},
                     query={"cursor": "!!!"})
    assert status == 400


def test_units_list_joins_status_and_silver(call, seeded) -> None:
    status, ul = call("GET", DOC_UNITS, path_params={"docId": seeded.doc_id, "pv": "2.3.1"})
    assert status == 200
    by_id = {u["unitId"]: u for u in ul["items"]}
    ok, held = by_id[seeded.unit_id], by_id[seeded.held_unit_id]
    assert ok["indexed"] is True and ok["fieldCount"] == 1 and ok["goldKey"].startswith("gold/")
    assert held["held"] is True and held["piiFlag"] == "held" and "goldKey" not in held


def test_reprocess_starts_execution(call, seeded, env, monkeypatch) -> None:
    import json

    sm = env.deps.sfn.create_state_machine(
        name="rp-ingest", definition=json.dumps({"StartAt": "P", "States": {"P": {"Type": "Pass", "End": True}}}),
        roleArn="arn:aws:iam::000000000000:role/sfn")
    monkeypatch.setenv("RP_STATE_MACHINE_ARN", sm["stateMachineArn"])
    status, err = call("POST", DOC_REPROCESS, path_params={"docId": seeded.doc_id},
                       body={"pipelineVersion": "1.0"})
    assert status == 409  # 1.0 lane is RUNNING
    status, r = call("POST", DOC_REPROCESS, path_params={"docId": seeded.doc_id},
                     body={"pipelineVersion": "2.3.1"})
    assert status == 202 and r["executionArn"].startswith("arn:aws:states")
    ex = env.deps.sfn.describe_execution(executionArn=r["executionArn"])
    payload = json.loads(ex["input"])
    assert payload == {"pipelineVersion": "2.3.1", "bucket": "rp-data-test", "key": seeded.bronze_key,
                       "versionId": "v1", "size": 12345, "trigger": "reprocess", "requestedBy": SUB_A}


def test_reprocess_owner_only(call, seeded) -> None:
    status, _ = call("POST", DOC_REPROCESS, path_params={"docId": seeded.doc_id}, body={"pipelineVersion": "2.3.1"},
                     sub=SUB_B)
    assert status == 403
