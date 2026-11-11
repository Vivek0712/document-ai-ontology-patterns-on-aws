"""The stage-inspection product surface over real pipeline output: the deployed state machine definition runs
B's handlers (moto + fakes), then D's REST API (stage detail, overlay, units, lineage, artifacts) and C's
/region route read exactly what those handlers wrote. Every stage of every version must be inspectable.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import boto3
import pytest
from evidence_core.env import Settings
from evidence_core.ids import doc_id_for
from evidence_core.stages import STAGE_IDS
from evidence_core.testing import SAMPLE_PACKAGE, SAMPLE_SUB
from lambdas.api import handler as api
from lambdas.api.deps import Deps, set_deps
from lambdas.get_region_http import handler as region_http
from lambdas.ingest_lib.pytest_fixtures import BUCKET, aws, ingest_env  # noqa: F401 - fixtures
from lambdas.ingest_lib.testing import FixtureDocling, load_docling_fixtures
from lambdas.tools._lib import stores as tool_stores
from local_pipeline import synthetic

from tests.integration.test_asl_handlers import ASL, FIX, _interpreter


def _event(method: str, resource: str, path: dict[str, str], query: dict[str, str] | None = None,
           sub: str = SAMPLE_SUB, groups: str = "[lending-analyst]") -> dict[str, Any]:
    p = resource
    for k, v in path.items():
        p = p.replace("{" + k + "}", quote(v, safe=""))
    return {"resource": resource, "path": p, "httpMethod": method, "headers": {},
            "pathParameters": {k: quote(v, safe="") for k, v in path.items()}, "queryStringParameters": query,
            "body": None, "isBase64Encoded": False,
            "requestContext": {"requestId": "r", "authorizer": {"claims": {"sub": sub, "cognito:groups": groups}}}}


def _get(resource: str, path: dict[str, str], query: dict[str, str] | None = None, **kw: Any) -> tuple[int, Any]:
    resp = api.handler(_event("GET", resource, path, query, **kw))
    return resp["statusCode"], json.loads(resp["body"])


@pytest.fixture
def world(aws: Any) -> Any:  # noqa: F811
    aws.docling_override = FixtureDocling(load_docling_fixtures(FIX))
    interp = _interpreter(aws)
    docs = {"w2": ("w2_2025_maya_fictus.pdf", synthetic.w2_pdf()),
            "bank": ("bank_statement_2026-09.pdf", synthetic.bank_statement_pdf())}
    for name, data in docs.values():
        trig = None
        for pv in ("1.0", "2.3.1"):
            if trig is None:
                from lambdas.ingest_lib.pytest_fixtures import put_bronze

                trig = put_bronze(aws, name, data)
            status, out = interp.run(ASL, trig | {"pipelineVersion": pv}, f"arn:x:execution:rp-ingest:{name}:{pv}")
            assert status == "SUCCEEDED", out
    set_deps(Deps(settings=Settings.from_env(), ddb=boto3.resource("dynamodb", region_name="us-east-1"),
                  s3=aws.s3))
    tool_stores.use_stores(None)
    yield {k: doc_id_for(SAMPLE_PACKAGE, v[0]) for k, v in docs.items()}
    set_deps(None)
    tool_stores.use_stores(None)


STAGE = "/documents/{docId}/lanes/{pv}/stages/{stageId}"
OVERLAY = STAGE + "/overlay"


@pytest.mark.parametrize("pv", ["1.0", "2.3.1"])
def test_every_stage_is_inspectable(world: dict[str, str], pv: str) -> None:
    doc = world["w2"]
    code, detail = _get("/documents/{docId}", {"docId": doc})
    assert code == 200 and detail["lanes"][pv]["lane"]["state"] == "SUCCEEDED"
    for sid in STAGE_IDS:
        code, st = _get(STAGE, {"docId": doc, "pv": pv, "stageId": sid.value})
        assert code == 200, (sid, st)
        rec = st["record"]
        assert rec["state"] in ("SUCCEEDED", "SKIPPED"), (sid, rec["state"])
        if rec["state"] == "SUCCEEDED":
            assert rec["versions"]["configSha"] and rec["versions"]["pipelineVersion"] == pv
            assert rec["startedAt"] and rec["endedAt"] and rec["durationMs"] is not None
            assert st["events"], sid  # STARTED/SUCCEEDED at least
            assert rec["functionName"], sid
        else:
            assert rec["skipReason"], sid
        for art in rec.get("artifacts", []):  # every output artifact is openable
            code, view = _get("/artifacts", {}, {"key": art["key"]})
            assert code == 200, art["key"]
            if art["key"].startswith("silver/"):
                assert view.get("url") is None
        code, ov = _get(OVERLAY, {"docId": doc, "pv": pv, "stageId": sid.value}, {"page": "1"})
        assert code == 200, (sid, ov)


def test_overlays_draw_engine_blocks_units_fields_and_held_regions(world: dict[str, str]) -> None:
    w2, bank = world["w2"], world["bank"]
    _, ext = _get(OVERLAY, {"docId": w2, "pv": "2.3.1", "stageId": "extract"}, {"page": "1"})
    assert ext["boxes"] and all(b["kind"] == "block" for b in ext["boxes"]) and ext["sources"][0].endswith(
        "docling.json")
    assert ext["pageImageUrl"] and ext["pageWidth"] == 612.0
    _, units = _get(OVERLAY, {"docId": w2, "pv": "2.3.1", "stageId": "units"}, {"page": "1"})
    kinds = {b["kind"] for b in units["boxes"]}
    assert {"unit", "member", "field"} <= kinds
    assert any(b["kind"] == "field" and b["ref"] == "f-box1" for b in units["boxes"])
    _, gate = _get(OVERLAY, {"docId": bank, "pv": "2.3.1", "stageId": "pii_gate"}, {"page": "1"})
    assert any(b["kind"] == "held" for b in gate["boxes"])
    assert any(b.get("piiFlag") == "held" for b in gate["boxes"] if b["kind"] == "unit")
    _, promote = _get(OVERLAY, {"docId": bank, "pv": "2.3.1", "stageId": "promote"}, {"page": "1"})
    assert any(b["kind"] == "held" for b in promote["boxes"])
    assert all(b.get("piiFlag") != "held" for b in promote["boxes"] if b["kind"] == "unit")
    text = json.dumps([ext, units, gate, promote])
    assert synthetic.ACCOUNT_NUMBER not in text and "98,400.00" not in text  # boxes only, no document text
    _, v1 = _get(OVERLAY, {"docId": w2, "pv": "1.0", "stageId": "extract"}, {"page": "1"})
    assert v1["boxes"] == [] and "no regions" in v1["note"]
    code, _ = _get(OVERLAY, {"docId": w2, "pv": "2.3.1", "stageId": "units"}, {"page": "1"}, sub="someone-else",
                   groups="[markets-analyst]")
    assert code == 403


def test_lineage_and_region_from_a_cited_field_back_to_bronze(world: dict[str, str]) -> None:
    w2 = world["w2"]
    _, ov = _get(OVERLAY, {"docId": w2, "pv": "2.3.1", "stageId": "units"}, {"page": "1"})
    unit_id = next(b["unitId"] for b in ov["boxes"] if b["kind"] == "field" and b["ref"] == "f-box1")
    _, units = _get("/documents/{docId}/lanes/{pv}/units", {"docId": w2, "pv": "2.3.1"})
    unit = next(u for u in units["items"] if u["unitId"] == unit_id)
    code, lin = _get("/units/{unitId}/lineage", {"unitId": unit["unitId"]}, {"field": "f-box1"})
    assert code == 200, lin
    kinds = [s["kind"] for s in lin["steps"]]
    assert kinds[:2] == ["field", "unit"] and "source" in kinds and "page_image" in kinds and "region" in kinds
    src = next(s for s in lin["steps"] if s["kind"] == "source")
    assert src["data"]["bronzeKey"].startswith("bronze/") and len(src["data"]["sha256"]) == 64
    for step in lin["steps"]:
        if step.get("stage"):
            assert step["stage"]["stageId"] in {s.value for s in STAGE_IDS}
    resp = region_http.handler(_event("GET", "/region/{unitId}", {"unitId": unit["unitId"]}, {"field": "f-box1"}))
    assert resp["statusCode"] == 200, resp["body"]
    region = json.loads(resp["body"])
    assert region["url"] and region["bbox"]["page"] == 1 and region["fieldId"] == "f-box1"
    denied = region_http.handler(_event("GET", "/region/{unitId}", {"unitId": unit["unitId"]}, {"field": "f-box1"},
                                        sub="someone-else", groups="[markets-analyst]"))
    assert denied["statusCode"] == 403
    assert json.loads(denied["body"])["error"]["message"] == "You don't have access to this region."
    assert BUCKET in region["url"]
