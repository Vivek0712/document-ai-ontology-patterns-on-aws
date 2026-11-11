"""The deployed state machine definition drives the real stage handlers.

contracts/generated/ingest_asl.json is exported from infra/lib/ingest-asl.ts (a jest test fails on drift). Here a
small ASL interpreter (tests/integration/asl.py) runs that exact definition, mapping each placeholder Lambda ARN
to its handler module (the same `lambdas.<dir>.handler.handler` string CDK deploys), the SQS task-token task to
`rp-docling-call` + the Step Functions callback, against moto S3/DynamoDB and B's fakes. This catches envelope,
ResultPath and Choice-variable mismatches between D's ASL and B's handlers that the hand-written local runner
cannot.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest
from boto3.dynamodb.conditions import Key
from evidence_core.ddb import from_ddb
from evidence_core.enums import StageId
from evidence_core.ids import doc_id_for
from evidence_core.testing import SAMPLE_PACKAGE
from lambdas.ingest_lib.pytest_fixtures import aws, ingest_env, put_bronze  # noqa: F401 - fixtures
from lambdas.ingest_lib.testing import FixtureDocling, load_docling_fixtures
from local_pipeline import synthetic

from tests.integration.asl import Interpreter

ROOT = Path(__file__).resolve().parents[2]
ASL = json.loads((ROOT / "contracts" / "generated" / "ingest_asl.json").read_text())
FIX = ROOT / "services" / "lambdas" / "ingest_lib" / "tests" / "fixtures"
FUNCTION_PREFIX = "arn:aws:lambda:us-east-1:000000000000:function:"


def _handler_for(arn: str) -> Any:
    name = arn.removeprefix(FUNCTION_PREFIX)
    module = importlib.import_module(f"lambdas.{name.removeprefix('rp-').replace('-', '_')}.handler")
    return module.handler


def _interpreter(svc: Any) -> Interpreter:
    def invoke(resource: str, payload: Any) -> Any:
        assert resource.startswith(FUNCTION_PREFIX), resource
        return _handler_for(resource)(payload, None)

    def send_with_token(params: dict[str, Any], token: str) -> Any:
        assert params["QueueUrl"].endswith("/rp-docling")
        body = params["MessageBody"]
        assert body["taskToken"] == token
        resp = _handler_for(FUNCTION_PREFIX + "rp-docling-call")(
            {"Records": [{"messageId": "m-1", "body": json.dumps(body)}]}, None)
        assert resp.get("batchItemFailures", []) == []
        sfn = svc.sfn
        fail = [f for f in sfn.failures if f["token"] == token]
        if fail:
            from tests.integration.asl import StatesError

            raise StatesError(fail[-1]["error"], fail[-1]["cause"])
        return json.loads(next(s for s in sfn.successes if s["token"] == token)["output"])

    return Interpreter(invoke=invoke, send_with_token=send_with_token)


@pytest.fixture
def svc(aws: Any) -> Any:  # noqa: F811
    aws.docling_override = FixtureDocling(load_docling_fixtures(FIX))
    return aws


def _records(svc: Any, doc_id: str, pv: str) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    items = [from_ddb(i) for i in svc.table("doc_status").query(KeyConditionExpression=Key("pk").eq(doc_id))["Items"]]
    stages = {i["stageId"]: i for i in items if i["recordType"] == "STAGE" and i["pipelineVersion"] == pv}
    lane = next(i for i in items if i["recordType"] == "LANE" and i["pipelineVersion"] == pv)
    return stages, lane


def _execute(svc: Any, file_name: str, data: bytes, pv: str) -> tuple[str, Any, Interpreter]:
    trig = put_bronze(svc, file_name, data) | {"pipelineVersion": pv}
    interp = _interpreter(svc)
    status, out = interp.run(ASL, trig, f"arn:aws:states:us-east-1:000000000000:execution:rp-ingest:{pv}")
    return status, out, interp


@pytest.mark.parametrize("pv", ["1.0", "2.3.1"])
def test_digital_w2_runs_through_the_deployed_definition(svc: Any, pv: str) -> None:
    status, out, interp = _execute(svc, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf(), pv)
    assert status == "SUCCEEDED", out
    assert interp.visited[-1] == "Done"
    assert set(out) == {"ctx", "stages"}  # MergeIndexGraph leaves only ctx and stages
    doc_id = doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf")
    stages, lane = _records(svc, doc_id, pv)
    assert lane["state"] == "SUCCEEDED"
    assert len(stages) == 10
    expected_skips = {"route", "classify", "graph"} if pv == "1.0" else set()
    assert {k for k, s in stages.items() if s["state"] == "SKIPPED"} == expected_skips
    assert all(s["state"] in ("SUCCEEDED", "SKIPPED") for s in stages.values()), {
        k: s["state"] for k, s in stages.items()}
    # every stage the definition ran recorded its function, artifacts and versions for the inspector
    for sid in ("extract", "units", "pii_gate", "promote", "index", "ready"):
        rec = stages[sid]
        assert rec["artifacts"], sid
        assert rec["versions"]["pipelineVersion"] == pv and rec["versions"]["configSha"], sid
        assert rec.get("durationMs") is not None, sid
    if pv == "2.3.1":
        assert "DoclingExtract" in interp.visited and "NoScanPages" in interp.visited
        assert stages["extract"]["engine"] == "docling"
        assert stages["extract"]["functionName"] == "rp-docling-call"
        assert out["stages"]["graph"]["stageId"] == "graph" and out["stages"]["graph"]["state"] == "SUCCEEDED"
    else:
        assert "TextLayer" in interp.visited and "Route" not in interp.visited
        assert out["stages"]["graph"] == {"stageId": "graph", "state": "SKIPPED"}


def test_scanned_page_takes_the_textract_map(svc: Any) -> None:
    scan = synthetic.scanned_pdf(synthetic.payslip_pdf(name="Arjun Fictus", gross="6,500.00", ytd="58,500.00"))
    status, out, interp = _execute(svc, "payslip_2026-09_arjun_fictus_scan.pdf", scan, "2.3.1")
    assert status == "SUCCEEDED", out
    assert "TextractPage" in interp.visited and "NoDigitalPages" in interp.visited
    stages, _ = _records(svc, doc_id_for(SAMPLE_PACKAGE, "payslip_2026-09_arjun_fictus_scan.pdf"), "2.3.1")
    assert stages["extract"]["state"] == "SUCCEEDED" and stages["extract"]["engine"] == "textract"
    assert stages["extract"]["functionName"] == "rp-textract-page"


def test_stage_failure_goes_through_lane_failed(svc: Any) -> None:
    svc.clients["comprehend"] = None  # PiiGate raises inside the Lambda
    status, out, interp = _execute(svc, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf(), "2.3.1")
    assert status == "FAILED" and out["Error"] == "LaneFailed"
    assert interp.visited[-2:] == ["LaneFailed", "Failed"]
    stages, lane = _records(svc, doc_id_for(SAMPLE_PACKAGE, "w2_2025_maya_fictus.pdf"), "2.3.1")
    assert lane["state"] == "FAILED"
    assert stages[StageId.PII_GATE.value]["state"] == "FAILED"
    assert "Traceback" in stages["pii_gate"]["error"]["cause"]
    assert stages["promote"]["state"] == "PENDING"


def test_rejected_upload_ends_in_rejected(svc: Any) -> None:
    status, out, interp = _execute(svc, "notes.pdf", b"hello, not a pdf", "2.3.1")
    assert status == "FAILED" and out["Error"] == "Rejected"
    assert interp.visited == ["Register", "ModeChoice", "Rejected"]


def test_url_encoded_event_key_is_decoded(svc: Any) -> None:
    """S3 event keys arrive URL-encoded (space -> '+'); register resolves the real object."""
    trig = put_bronze(svc, "w2 2025 maya fictus.pdf", synthetic.w2_pdf()) | {"pipelineVersion": "1.0"}
    trig["key"] = trig["key"].replace(" ", "+")
    status, out = Interpreter(invoke=_interpreter(svc).invoke, send_with_token=lambda p, t: {}).run(
        ASL, trig, "arn:aws:states:us-east-1:000000000000:execution:rp-ingest:enc")
    assert status == "SUCCEEDED", out
    assert out["ctx"]["bronzeKey"].endswith("/w2 2025 maya fictus.pdf")
