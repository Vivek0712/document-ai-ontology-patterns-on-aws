from __future__ import annotations

import json

from lambdas.api.routes import ARTIFACTS, PIPELINE, PIPELINE_DIFF, PIPELINES, REVIEW, RUN, RUNS
from lambdas.api.tests.support import BUCKET, SUB_A, SUB_B


def test_silver_artifact_preview_masked_no_url(call, seeded) -> None:
    key = f"silver/{seeded.package_id}/{seeded.doc_id}/2.3.1/units/units.jsonl"
    status, a = call("GET", ARTIFACTS, query={"key": key})
    assert status == 200
    assert a["tier"] == "silver" and a["previewMasked"] is True and "url" not in a
    assert "900-12-3456" not in a["preview"] and "•••••3456" in a["preview"]
    assert a["contentType"] == "application/x-ndjson" and a["versionId"]


def test_silver_preview_masks_held_spans_of_any_shape(call, seeded, env) -> None:
    """A held span that is not a long digit run (OCR'd cells scored as an SSN) is masked in every Silver preview
    of the document, in every lane."""
    from evidence_core import s3keys
    from evidence_core.enums import PiiFlag
    from evidence_core.models.units import PiiDecision, PiiDecisions, PiiEntityHit, Unit

    pkg, doc, pv = seeded.package_id, seeded.doc_id, "2.3.1"
    units_key = s3keys.silver_units_key(pkg, doc, pv)
    body = env.s3.get_object(Bucket=BUCKET, Key=units_key)["Body"].read().decode()
    units = [Unit.load(json.loads(line)) for line in body.splitlines() if line]
    held = units[1].model_copy(update={"text": "Medicare 94.25 848.25 YTD"})
    env.s3.put_object(Bucket=BUCKET, Key=units_key,
                      Body=("\n".join(u.dump_json() for u in (units[0], held)) + "\n").encode())
    hit = PiiEntityHit(type="SSN", score=0.959, begin=9, end=21, digits=9, masked=False)
    env.s3.put_object(Bucket=BUCKET, Key=s3keys.pii_decisions_key(pkg, doc, pv), Body=json.dumps(PiiDecisions(
        doc_id=doc, pipeline_version=pv, rule="rp-pii-v1",
        decisions=[PiiDecision(unit_id=held.unit_id, decision=PiiFlag.HELD, rule="rp-pii-v1", entities=[hit])]).dump()))
    ocr_key = s3keys.textract_page_key(pkg, doc, pv, 1)
    env.s3.put_object(Bucket=BUCKET, Key=ocr_key, Body=json.dumps({"Blocks": [{"Text": "94.25 848.25"}]}).encode())
    for key in (units_key, ocr_key):
        status, a = call("GET", ARTIFACTS, query={"key": key})
        assert status == 200
        assert "848.25" not in a["preview"] and "[held: PII]" in a["preview"], key


def test_gold_artifact_has_url(call, seeded) -> None:
    key = f"gold/{seeded.package_id}/{seeded.doc_id}/2.3.1/pages/p001.png"
    status, a = call("GET", ARTIFACTS, query={"key": key})
    assert status == 200 and a["url"].startswith("https://") and a["expiresIn"] == 300
    assert "preview" not in a  # binary


def test_bronze_url_owner_only(call, seeded) -> None:
    status, a = call("GET", ARTIFACTS, query={"key": seeded.bronze_key})
    assert status == 200 and "url" in a
    status, b = call("GET", ARTIFACTS, query={"key": seeded.bronze_key}, sub=SUB_B)  # group member, not owner
    assert status == 200 and "url" not in b


def test_artifact_access_rules(call, seeded, env) -> None:
    key = f"gold/{seeded.package_id}/{seeded.doc_id}/2.3.1/pages/p001.png"
    status, _ = call("GET", ARTIFACTS, query={"key": key}, sub=SUB_B, groups=["markets-analyst"])
    assert status == 403
    for bad in ("", "../etc/passwd", "secret/x", "gold//x"):
        status, _ = call("GET", ARTIFACTS, query={"key": bad})
        assert status == 400, bad
    status, _ = call("GET", ARTIFACTS, query={"key": f"gold/{seeded.package_id}/missing.json"})
    assert status == 404
    env.s3.put_object(Bucket=BUCKET, Key="config/models.yaml", Body=b"models: {}")
    status, c = call("GET", ARTIFACTS, query={"key": "config/models.yaml"}, sub=SUB_B, groups=[])
    assert status == 200 and c["preview"] == "models: {}" and c["url"]


def test_pipelines_list_detail_and_diff(call, env) -> None:
    status, pl = call("GET", PIPELINES)
    assert status == 200 and [i["pipelineVersion"] for i in pl["items"]] == ["1.0", "2.3.1"]
    status, d = call("GET", PIPELINE, path_params={"pv": "2.3.1"})
    assert status == 200 and d["item"]["mode"] == "evidence_units" and "preprocessing:" in d["yaml"]
    assert [s["id"] for s in d["stages"]][:3] == ["register", "route", "extract"] and d["promptText"]
    status, diff = call("GET", PIPELINE_DIFF, query={"a": "1.0", "b": "2.3.1"})
    assert status == 200
    assert diff["changedBlocks"] == ["preprocessing"]
    assert diff["promptSha"]["equal"] is True and diff["modelId"]["equal"] is True
    status, _ = call("GET", PIPELINE, path_params={"pv": "7.7"})
    assert status == 404
    status, _ = call("GET", PIPELINE_DIFF, query={"a": "x"})
    assert status == 400


def _put_run(env, run_id: str, package_id: str, created_at: str) -> None:
    from evidence_core.ddb import to_ddb

    summary = {"runId": run_id, "createdAt": created_at, "packageId": package_id, "pipelineVersion": "2.3.1",
               "question": "What is the total income?", "callerSub": SUB_A, "modelId": "m", "promptSha": "p",
               "configSha": "c", "corpusHash": "h", "latencyMs": 1200, "status": "ok", "s3Key": f"runs/{run_id}.json"}
    env.deps.table("runs").put_item(Item=to_ddb(summary))
    record = summary | {"groups": ["lending-analyst"], "blockShas": {}, "toolsEnabled": ["search_units"],
                        "toolCalls": [], "retrieved": [], "retrievedText": []}
    env.s3.put_object(Bucket=BUCKET, Key=f"runs/{run_id}.json", Body=json.dumps(record).encode())


def test_runs_list_and_get(call, seeded, env) -> None:
    _put_run(env, "r-000000000001", seeded.package_id, "2026-11-03T10:00:00.000Z")
    _put_run(env, "r-000000000002", seeded.package_id, "2026-11-03T11:00:00.000Z")
    status, page = call("GET", RUNS, query={"packageId": seeded.package_id, "limit": "1"})
    assert status == 200 and page["items"][0]["runId"] == "r-000000000002" and page["nextCursor"]
    status, page2 = call("GET", RUNS, query={"packageId": seeded.package_id, "cursor": page["nextCursor"]})
    assert [r["runId"] for r in page2["items"]] == ["r-000000000001"]
    status, rec = call("GET", RUN, path_params={"runId": "r-000000000001"})
    assert status == 200 and rec["toolsEnabled"] == ["search_units"]
    status, _ = call("GET", RUN, path_params={"runId": "r-000000000001"}, sub=SUB_B, groups=["markets-analyst"])
    assert status == 403
    status, _ = call("GET", RUNS, query={})
    assert status == 400
    status, art = call("GET", ARTIFACTS, query={"key": "runs/r-000000000001.json"})
    assert status == 200 and art["url"]


def test_review_list(call, seeded, env) -> None:
    from evidence_core.ddb import to_ddb

    env.deps.table("review_queue").put_item(Item=to_ddb({
        "packageId": seeded.package_id, "reviewKey": f"{seeded.doc_id}#2.3.1#{seeded.unit_id}#f-box1",
        "docId": seeded.doc_id, "pipelineVersion": "2.3.1", "unitId": seeded.unit_id, "fieldId": "f-box1",
        "label": "Box 1", "value": "98,400.00", "confidence": 0.71, "threshold": 0.8, "engine": "textract",
        "reason": "confidence below threshold", "createdAt": "2026-11-03T10:00:00.000Z"}))
    status, rl = call("GET", REVIEW, query={"packageId": seeded.package_id})
    assert status == 200 and rl["items"][0]["confidence"] == 0.71
