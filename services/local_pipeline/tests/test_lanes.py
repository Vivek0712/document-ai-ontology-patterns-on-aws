"""End to end through the real handlers (moto S3/DynamoDB + fakes), both lanes, and the status
records the stage inspector reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from boto3.dynamodb.conditions import Key
from evidence_core import s3keys
from evidence_core.ddb import from_ddb
from evidence_core.enums import StageId
from evidence_core.ids import doc_id_for
from evidence_core.models.status import LaneRecord, StageEvent, StageRecord
from evidence_core.testing import SAMPLE_PACKAGE, SAMPLE_SUB
from lambdas.ingest_lib.pytest_fixtures import BUCKET, put_bronze
from lambdas.ingest_lib.testing import FixtureDocling, load_docling_fixtures
from lambdas.stage_status import handler as stage_status
from lambdas.status_stream import handler as status_stream
from lambdas.upload_url import handler as upload_url
from local_pipeline import synthetic
from local_pipeline.runner import LocalIngest

FIX = Path(__file__).resolve().parents[2] / "lambdas" / "ingest_lib" / "tests" / "fixtures"


@pytest.fixture
def svc(aws: Any) -> Any:
    aws.docling_override = FixtureDocling(load_docling_fixtures(FIX))
    return aws


def _records(svc: Any, doc_id: str) -> tuple[list[StageRecord], list[LaneRecord], list[StageEvent]]:
    items = [from_ddb(i) for i in svc.table("doc_status").query(KeyConditionExpression=Key("pk").eq(doc_id))["Items"]]
    stages = [StageRecord.load(i) for i in items if i["recordType"] == "STAGE"]
    lanes = [LaneRecord.load(i) for i in items if i["recordType"] == "LANE"]
    events = [StageEvent.load(i) for i in items if i["recordType"] == "EVENT"]
    return stages, lanes, events


def _run(svc: Any, file_name: str, data: bytes, pv: str, **kw: Any):  # noqa: ANN202
    trig = put_bronze(svc, file_name, data) | {"pipelineVersion": pv}
    return LocalIngest(**kw).run(trig)


def test_v1_lane_w2_every_stage_recorded(svc: Any) -> None:
    lane = _run(svc, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf(), "1.0")
    assert lane.status == "SUCCEEDED", lane.error
    doc_id = doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf")
    stages, lanes, events = _records(svc, doc_id)
    by_id = {s.stage_id: s for s in stages if s.pipeline_version == "1.0"}
    assert len(by_id) == 10
    assert {k for k, s in by_id.items() if s.state.value == "SKIPPED"} == {StageId.ROUTE, StageId.CLASSIFY,
                                                                          StageId.GRAPH}
    assert by_id[StageId.GRAPH].skip_reason.startswith("preprocessing.ontology: none")
    for sid in (StageId.REGISTER, StageId.EXTRACT, StageId.UNITS, StageId.PII_GATE, StageId.PROMOTE,
                StageId.INDEX, StageId.READY):
        rec = by_id[sid]
        assert rec.state.value == "SUCCEEDED", (sid, rec.error)
        assert rec.artifacts, sid
        assert rec.started_at and rec.ended_at and rec.duration_ms is not None
        assert rec.attempt == 1
    ex = by_id[StageId.EXTRACT]
    assert ex.inputs[0].kind.value == "source_pdf" and ex.inputs[0].sha256
    assert ex.versions.engine_version.startswith("pypdf 6.21.4") and ex.versions.code_sha == "test-sha"
    assert ex.config.paths == ["preprocessing.extraction"] and len(ex.config.sha) == 64
    # the model that actually ran (dev runs embed with the hash embedder), not the configured Titan id
    assert by_id[StageId.INDEX].versions.model_id == "local-hash-embedder"
    assert by_id[StageId.UNITS].output["units"] >= 1
    assert lanes[0].state.value == "SUCCEEDED" and lanes[0].indexed == by_id[StageId.INDEX].output["indexed"]
    assert {e.event.value for e in events} >= {"STARTED", "SUCCEEDED", "SKIPPED"}
    # every artifact key the inspector shows exists in the bucket
    for rec in by_id.values():
        for a in rec.artifacts:
            svc.s3.head_object(Bucket=BUCKET, Key=a.key)
    # the naive chunk text detaches W-2 values from their labels (overlay order)
    units_key = s3keys.silver_units_key(SAMPLE_PACKAGE, doc_id, "1.0")
    chunk = json.loads(svc.s3.get_object(Bucket=BUCKET, Key=units_key)["Body"].read().splitlines()[0])
    assert chunk["unitType"] == "chunk" and chunk["bboxes"] == [] and chunk["chunkLabel"] == "chunk c-01"
    assert "1 Wages, tips, other compensation\n2 Federal" in chunk["text"]


def test_v231_lane_w2_units_fields_index_graph(svc: Any) -> None:
    lane = _run(svc, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf(), "2.3.1")
    assert lane.status == "SUCCEEDED", lane.error
    assert "DoclingExtract" in lane.states_visited and "TextractPage" not in lane.states_visited
    doc_id = doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf")
    stages, lanes, events = _records(svc, doc_id)
    by_id = {s.stage_id: s for s in stages}
    assert all(s.state.value == "SUCCEEDED" for s in stages), [(s.stage_id, s.state, s.error) for s in stages]
    assert by_id[StageId.ROUTE].output["digitalPages"] == [1]
    assert by_id[StageId.EXTRACT].headline.startswith("Docling")
    assert by_id[StageId.EXTRACT].artifacts[0].note.startswith("docling")
    assert by_id[StageId.EXTRACT].versions.engine_version.startswith("docling")
    assert any(e.stage_id is StageId.EXTRACT and e.event.value == "QUEUED" for e in events)
    assert any(e.stage_id is StageId.EXTRACT and (e.message or "").startswith("Docling:") for e in events)
    assert by_id[StageId.CLASSIFY].output["docType"] == "doc:W2"
    assert by_id[StageId.GRAPH].output["facts"] >= 6 and by_id[StageId.GRAPH].output["triples"] > 50
    assert lanes[0].triples == by_id[StageId.GRAPH].output["triples"]
    gold = s3keys.gold_units_key(SAMPLE_PACKAGE, doc_id, "2.3.1")
    units = [json.loads(x) for x in svc.s3.get_object(Bucket=BUCKET, Key=gold)["Body"].read().splitlines()]
    box1 = next(f for u in units for f in u["fields"] if f["fieldId"] == "f-box1")
    assert box1["value"] == "98,400.00" and box1["bbox"]["page"] == 1
    # one Gold unit file per passed unit, page image rendered
    one = svc.s3.get_object(Bucket=BUCKET, Key=s3keys.gold_unit_key(SAMPLE_PACKAGE, units[0]["unitId"]))
    assert json.loads(one["Body"].read())["unitId"] == units[0]["unitId"]
    png = svc.s3.get_object(Bucket=BUCKET, Key=s3keys.page_image_key(SAMPLE_PACKAGE, doc_id, "2.3.1", 1))
    assert png["Body"].read()[:8] == b"\x89PNG\r\n\x1a\n"
    # UnitStatus rows carry aossId and gold keys; AOSS holds the docs with ACL metadata
    us = svc.table("unit_status").get_item(Key={"unitId": units[0]["unitId"]})["Item"]
    assert us["aossId"] and us["goldKey"] and us["piiFlag"] in ("none", "masked")
    docs = list(svc.aoss().docs["units-v2-3-1"].values())
    assert all(d["allowedPrincipals"] == [f"user:{SAMPLE_SUB}", "group:lending-analyst"] for d in docs)
    assert len(svc.sparql().updates) >= 2 and svc.sparql().updates[0].startswith("DELETE")


def test_reprocess_is_idempotent(svc: Any) -> None:
    data = synthetic.w2_pdf()
    _run(svc, "w2_2025_maya_fictus.pdf", data, "2.3.1")
    first = len(svc.aoss().docs["units-v2-3-1"])
    lane = _run(svc, "w2_2025_maya_fictus.pdf", data, "2.3.1")
    assert lane.status == "SUCCEEDED", lane.error
    assert len(svc.aoss().docs["units-v2-3-1"]) == first
    doc_id = doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf")
    stages, _, _ = _records(svc, doc_id)
    idx = next(s for s in stages if s.stage_id is StageId.INDEX)
    assert idx.output["deletedPrevious"] == first


def test_scanned_payslip_textract_in_231_and_empty_in_10(svc: Any) -> None:
    scan = synthetic.scanned_pdf(synthetic.payslip_pdf(name="Arjun Fictus", gross="6,500.00", ytd="58,500.00"))
    name = "payslip_2026-09_arjun_fictus_scan.pdf"
    v1 = _run(svc, name, scan, "1.0")
    assert v1.status == "SUCCEEDED", v1.error
    assert v1.state["stages"]["extract"]["output"]["emptyPages"] == [1]
    assert v1.state["stages"]["units"]["output"]["units"] == 0
    v2 = _run(svc, name, scan, "2.3.1")
    assert v2.status == "SUCCEEDED", v2.error
    assert v2.state["stages"]["route"]["output"]["scanPages"] == [1]
    assert "TextractPage" in v2.states_visited
    assert v2.state["stages"]["extract"]["output"]["engines"] == ["textract"]
    assert v2.state["stages"]["units"]["output"]["reviewItems"] == 1
    reviews = svc.table("review_queue").query(KeyConditionExpression=Key("packageId").eq(SAMPLE_PACKAGE))["Items"]
    assert len(reviews) == 1 and reviews[0]["state"] == "OPEN"


def test_bank_statement_header_held_and_blacked_out(svc: Any) -> None:
    lane = _run(svc, "bank_statement_2026-09.pdf", synthetic.bank_statement_pdf(), "2.3.1")
    assert lane.status == "SUCCEEDED", lane.error
    gate = lane.state["stages"]["pii_gate"]["output"]
    assert gate["held"] == 1 and gate["passed"] >= 1
    doc_id = doc_id_for(SAMPLE_PACKAGE, "bank_statement_2026-09.pdf")
    held_id = gate["heldUnitIds"][0]
    gold = s3keys.gold_units_key(SAMPLE_PACKAGE, doc_id, "2.3.1")
    gold_text = svc.s3.get_object(Bucket=BUCKET, Key=gold)["Body"].read().decode()
    assert held_id not in gold_text and synthetic.ACCOUNT_NUMBER not in gold_text
    decisions = svc.s3.get_object(Bucket=BUCKET, Key=s3keys.pii_decisions_key(SAMPLE_PACKAGE, doc_id, "2.3.1"))
    assert synthetic.ACCOUNT_NUMBER not in decisions["Body"].read().decode()
    held = json.loads(svc.s3.get_object(Bucket=BUCKET, Key=s3keys.held_regions_key(SAMPLE_PACKAGE, doc_id,
                                                                                   "2.3.1"))["Body"].read())
    assert held["pages"]["1"]
    assert all(d["piiFlag"] != "held" for d in svc.aoss().docs["units-v2-3-1"].values())
    # the 1.0 lane holds its chunk too, and the span is located on the page for blackout
    v1 = _run(svc, "bank_statement_2026-09.pdf", synthetic.bank_statement_pdf(), "1.0")
    assert v1.state["stages"]["pii_gate"]["output"]["held"] >= 1
    assert v1.state["stages"]["promote"]["output"]["heldRegions"] >= 1


def test_quarantine_and_skip(svc: Any) -> None:
    bad = _run(svc, "notes.pdf", b"hello, not a pdf", "2.3.1")
    assert bad.status == "REJECTED"
    doc_id = doc_id_for(SAMPLE_PACKAGE, "notes.pdf")
    doc = svc.table("documents").get_item(Key={"packageId": SAMPLE_PACKAGE, "docId": doc_id})["Item"]
    assert doc["uploadState"] == "QUARANTINED" and doc["quarantineKey"].startswith("quarantine/")
    _, lanes, _ = _records(svc, doc_id)
    assert lanes[0].state.value == "FAILED" and lanes[0].error.type == "Quarantined"
    svc.table("packages").update_item(Key={"packageId": SAMPLE_PACKAGE}, UpdateExpression="SET pipelineVersions = :p",
                                      ExpressionAttributeValues={":p": ["2.3.1"]})
    skipped = _run(svc, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf(), "1.0")
    assert skipped.status == "SKIPPED"
    stages, lanes, _ = _records(svc, doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf"))
    assert all(s.state.value == "SKIPPED" for s in stages) and lanes[0].state.value == "SKIPPED"


def test_oversized_object_is_quarantined_without_being_read(svc: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from lambdas.ingest_lib import store as store_mod
    from lambdas.register import handler as register

    monkeypatch.setattr(register, "MAX_UPLOAD_BYTES", 1024)
    reads: list[str] = []
    real_get = store_mod.DataStore.get_bytes
    monkeypatch.setattr(store_mod.DataStore, "get_bytes",
                        lambda self, key, version_id=None: reads.append(key) or real_get(self, key, version_id))
    lane = _run(svc, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf(), "2.3.1")
    assert lane.status == "REJECTED"
    bronze = s3keys.bronze_key(SAMPLE_SUB, SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf")
    assert bronze not in reads  # HeadObject size check, no GetObject of the oversized body
    doc_id = doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf")
    stages, lanes, _ = _records(svc, doc_id)
    reg = next(s for s in stages if s.stage_id is StageId.REGISTER)
    assert reg.state.value == "FAILED" and "not read" in reg.headline
    assert lanes[0].error.type == "Quarantined"


def test_same_bytes_under_another_name_is_skipped_as_duplicate(svc: Any) -> None:
    """A W-2 uploaded twice must not be summed twice by qualifying income."""
    data = synthetic.w2_pdf()
    first = _run(svc, "w2_2025_maya_fictus.pdf", data, "2.3.1")
    assert first.status == "SUCCEEDED", first.error
    dup = _run(svc, "w2_2025_maya_fictus (1).pdf", data, "2.3.1")
    assert dup.status == "SKIPPED"
    dup_id = doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus (1).pdf")
    stages, lanes, _ = _records(svc, dup_id)
    assert lanes[0].state.value == "SKIPPED" and "duplicate of w2_2025_maya_fictus.pdf" in lanes[0].skip_reason
    assert all(s.state.value == "SKIPPED" for s in stages)
    doc = svc.table("documents").get_item(Key={"packageId": SAMPLE_PACKAGE, "docId": dup_id})["Item"]
    assert doc["duplicateOf"] == doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf")
    # reprocessing the original keeps it canonical
    again = _run(svc, "w2_2025_maya_fictus.pdf", data, "2.3.1")
    assert again.status == "SUCCEEDED", again.error


def test_failure_goes_through_lane_failed(svc: Any) -> None:
    svc.clients["comprehend"] = None  # PiiGate raises
    lane = _run(svc, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf(), "1.0")
    assert lane.status == "FAILED"
    doc_id = doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf")
    stages, lanes, events = _records(svc, doc_id)
    gate = next(s for s in stages if s.stage_id is StageId.PII_GATE)
    assert gate.state.value == "FAILED" and gate.error.type == "AttributeError" and "Traceback" in gate.error.cause
    assert lanes[0].state.value == "FAILED"
    assert next(s for s in stages if s.stage_id is StageId.PROMOTE).state.value == "PENDING"


def test_execution_event_backstop(svc: Any) -> None:
    lane = _run(svc, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf(), "1.0", until="units")
    assert lane.status == "STOPPED"
    trig = put_bronze(svc, "w2_2025_maya_fictus.pdf", b"") | {"pipelineVersion": "1.0"}
    out = stage_status.handler({"op": "execution_event", "detail": {
        "status": "TIMED_OUT", "executionArn": lane.ctx["executionArn"], "input": json.dumps(trig)}})
    assert out["changed"]
    _, lanes, _ = _records(svc, lane.ctx["docId"])
    assert lanes[0].state.value == "FAILED" and lanes[0].error.type == "States.TimedOut"
    again = stage_status.handler({"op": "execution_event", "detail": {"status": "FAILED", "input": json.dumps(trig)}})
    assert not again["changed"]


def test_status_stream_rolls_up_package(svc: Any) -> None:
    _run(svc, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf(), "1.0")
    _run(svc, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf(), "2.3.1")
    event = {"Records": [{"dynamodb": {"NewImage": {"recordType": {"S": "LANE"},
                                                    "gsi1pk": {"S": SAMPLE_PACKAGE}}}}]}
    assert status_stream.handler(event) == {"packages": {SAMPLE_PACKAGE: True}}
    pkg = from_ddb(svc.table("packages").get_item(Key={"packageId": SAMPLE_PACKAGE})["Item"])
    assert pkg["lanes"]["1.0"]["ready"] and pkg["lanes"]["2.3.1"]["ready"]
    assert pkg["counters"]["2.3.1"]["triples"] > 0 and pkg["docCount"] == 1


def test_upload_url_owner_only(svc: Any) -> None:
    def call(sub: str, body: dict[str, Any]) -> dict[str, Any]:
        event = {"pathParameters": {"packageId": SAMPLE_PACKAGE}, "body": json.dumps(body),
                 "requestContext": {"authorizer": {"claims": {"sub": sub, "cognito:groups": "lending-analyst"}}}}
        return upload_url.handler(event, None)

    ok = call(SAMPLE_SUB, {"fileName": "w2_2025_maya_fictus.pdf", "contentType": "application/pdf",
                           "sizeBytes": 1000})
    assert ok["statusCode"] == 200
    body = json.loads(ok["body"])
    assert body["key"] == f"bronze/{SAMPLE_SUB}/{SAMPLE_PACKAGE}/w2_2025_maya_fictus.pdf"
    assert body["docId"] == doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf") and body["url"].startswith("https://")
    # SigV4 with the declared size signed in: a larger (or smaller) body fails the signature at S3.
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in body["url"]
    assert "X-Amz-SignedHeaders=content-length%3Bcontent-type%3Bhost" in body["url"]
    doc = svc.table("documents").get_item(Key={"packageId": SAMPLE_PACKAGE, "docId": body["docId"]})["Item"]
    assert doc["uploadState"] == "AWAITING_UPLOAD"
    assert svc.table("doc_acl").get_item(Key={"docId": body["docId"]})["Item"]["ownerSub"] == SAMPLE_SUB
    assert call("someone-else", {"fileName": "x.pdf", "contentType": "application/pdf", "sizeBytes": 10})[
        "statusCode"] == 403
    assert call(SAMPLE_SUB, {"fileName": "../x.pdf", "contentType": "application/pdf", "sizeBytes": 10})[
        "statusCode"] == 400
    assert call(SAMPLE_SUB, {"fileName": "x.pdf", "contentType": "application/pdf", "sizeBytes": 10 ** 9})[
        "statusCode"] == 400
