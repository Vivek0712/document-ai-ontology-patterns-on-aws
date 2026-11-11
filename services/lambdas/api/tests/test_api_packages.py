from __future__ import annotations

from lambdas.api.handler import handler
from lambdas.api.routes import ME, PACKAGE, PACKAGE_STATUS, PACKAGES, router
from lambdas.api.tests.support import SUB_A, SUB_B


def test_every_contract_route_is_registered() -> None:
    expected = {
        ("GET", "/me"), ("GET", "/packages"), ("POST", "/packages"), ("GET", "/packages/{packageId}"),
        ("DELETE", "/packages/{packageId}"), ("GET", "/packages/{packageId}/status"), ("GET", "/documents/{docId}"),
        ("GET", "/documents/{docId}/lanes/{pv}/stages/{stageId}"), ("GET", "/documents/{docId}/lanes/{pv}/events"),
        ("GET", "/documents/{docId}/lanes/{pv}/stages/{stageId}/overlay"),
        ("GET", "/documents/{docId}/lanes/{pv}/units"), ("POST", "/documents/{docId}/reprocess"),
        ("GET", "/units/{unitId}"), ("GET", "/units/{unitId}/lineage"), ("GET", "/artifacts"),
        ("GET", "/pipelines"), ("GET", "/pipelines/{pv}"), ("GET", "/pipelines/diff"), ("GET", "/runs"),
        ("GET", "/runs/{runId}"), ("GET", "/review"),
        # v2 inspection routes (rp-xray is its own Lambda)
        ("GET", "/documents/{docId}/lanes/{pv}/build"), ("GET", "/inspect/filters"),
        ("GET", "/runs/{runId}/calls/{callId}/path"), ("GET", "/showcases"), ("GET", "/showcases/{showcaseId}/payload"),
        # governance, measurement, knowledge
        ("GET", "/access/decisions"), ("GET", "/governance/definitions/{name}"),
        ("POST", "/review/{packageId}/items/{reviewKey}/decision"), ("GET", "/eval/runs"),
        ("GET", "/eval/runs/{evalRunId}"), ("GET", "/cost"), ("GET", "/ontology/classes"),
        ("GET", "/ontology/terms/{iri}"), ("GET", "/graph/neighbourhood"),
        # COA environment lifecycle (forwarded to rp-coa-control)
        ("GET", "/coa/environment"), ("POST", "/coa/environment/up"), ("POST", "/coa/environment/down"),
        ("PUT", "/coa/environment/settings"), ("POST", "/coa/environment/keep-alive"),
    }
    assert set(router.routes) == expected


def test_me(call) -> None:
    status, body = call("GET", ME, groups=["lending-analyst"])
    assert status == 200
    assert body["sub"] == SUB_A
    assert body["principals"] == [f"user:{SUB_A}", "group:lending-analyst"]


def test_unauthenticated_is_401(env) -> None:
    from lambdas.api.tests.support import make_event

    resp = handler(make_event("GET", ME, sub=""), None)
    assert resp["statusCode"] == 401
    assert '"unauthorized"' in resp["body"]


def test_unknown_route_404(call) -> None:
    status, body = call("GET", "/nope")
    assert status == 404 and body["error"]["code"] == "not_found"


def test_cors_echoes_only_configured_origin(env) -> None:
    from lambdas.api.tests.support import make_event

    ok = handler(make_event("GET", ME), None)
    assert ok["headers"]["Access-Control-Allow-Origin"] == "https://web.example.com"
    other = handler(make_event("GET", ME, headers={"Origin": "https://evil.example"}), None)
    assert "Access-Control-Allow-Origin" not in other["headers"]


def test_create_list_get_package(call) -> None:
    status, pkg = call("POST", PACKAGES, body={"name": "My Loan", "shareWithGroups": ["lending-analyst"]})
    assert status == 201
    assert pkg["packageId"].startswith("pkg-my-loan-")
    assert pkg["allowedPrincipals"] == [f"user:{SUB_A}", "group:lending-analyst"]
    assert pkg["pipelineVersions"] == ["1.0", "2.3.1"]

    status, listing = call("GET", PACKAGES)
    assert status == 200 and [p["packageId"] for p in listing["items"]] == [pkg["packageId"]]

    # user B (other group) sees nothing and gets 403 on the detail
    status, listing_b = call("GET", PACKAGES, sub=SUB_B, groups=["markets-analyst"])
    assert listing_b["items"] == []
    status, err = call("GET", PACKAGE, path_params={"packageId": pkg["packageId"]}, sub=SUB_B,
                       groups=["markets-analyst"])
    assert status == 403 and err["error"]["code"] == "forbidden"

    # a lending-analyst colleague sees it through the group, read-only
    status, detail = call("GET", PACKAGE, path_params={"packageId": pkg["packageId"]}, sub=SUB_B)
    assert status == 200 and detail["canWrite"] is False and detail["documents"] == []


def test_create_package_rejects_foreign_group_and_unknown_version(call) -> None:
    status, err = call("POST", PACKAGES, body={"name": "x", "shareWithGroups": ["markets-analyst"]})
    assert status == 403
    status, err = call("POST", PACKAGES, body={"name": "x", "pipelineVersions": ["9.9"]})
    assert status == 400 and "unknown pipelineVersion" in err["error"]["message"]
    status, err = call("POST", PACKAGES, body={"name": ""})
    assert status == 400


def test_package_status_board(call, seeded) -> None:
    status, st = call("GET", PACKAGE_STATUS, path_params={"packageId": seeded.package_id})
    assert status == 200
    assert st["objectsInBronze"] == 1
    doc = st["docs"][0]
    assert doc["docId"] == seeded.doc_id
    lane2, lane1 = doc["lanes"]["2.3.1"], doc["lanes"]["1.0"]
    assert lane2["state"] == "SUCCEEDED"
    assert [c["stageId"] for c in lane2["stages"]] == ["register", "route", "extract", "classify", "units",
                                                       "pii_gate", "promote", "index", "graph", "ready"]
    assert all(c["state"] == "SUCCEEDED" for c in lane2["stages"])
    assert lane2["stages"][2]["heartbeatCount"] == 1
    by_id = {c["stageId"]: c for c in lane1["stages"]}
    assert by_id["route"]["state"] == "SKIPPED" and "router" in by_id["route"]["skipReason"]
    assert by_id["graph"]["state"] == "SKIPPED"
    assert by_id["extract"]["state"] == "RUNNING" and by_id["extract"]["engine"] == "text-layer"
    # rollups computed until rp-status-stream writes them
    assert st["lanes"]["2.3.1"]["ready"] is True and st["lanes"]["2.3.1"]["expected"] == 1
    assert st["lanes"]["1.0"]["state"] == "RUNNING" and st["lanes"]["1.0"]["ready"] is False
    assert st["counters"]["2.3.1"] == {"units": 2, "held": 1, "indexed": 1, "triples": 40}
    assert st["askReady"] is False


def test_status_for_new_doc_without_lanes_renders_registry(call, env) -> None:
    from evidence_core.ddb import to_ddb
    from evidence_core.models.api import DocumentItem

    status, pkg = call("POST", PACKAGES, body={"name": "fresh"})
    env.deps.table("documents").put_item(Item=to_ddb(DocumentItem(
        package_id=pkg["packageId"], doc_id="paystub-abc123", file_name="paystub.pdf",
        bronze_key=f"bronze/{SUB_A}/{pkg['packageId']}/paystub.pdf", uploaded_by=SUB_A,
        upload_url_issued_at="2026-11-03T00:00:00.000Z").dump()))
    status, st = call("GET", PACKAGE_STATUS, path_params={"packageId": pkg["packageId"]})
    assert status == 200 and st["objectsInBronze"] == 0
    lane = st["docs"][0]["lanes"]["1.0"]
    assert lane["state"] == "PENDING"
    assert {c["stageId"]: c["state"] for c in lane["stages"]}["classify"] == "SKIPPED"
    assert st["lanes"]["1.0"]["state"] == "PENDING"


def test_delete_package_owner_only(call, seeded, env) -> None:
    status, _ = call("DELETE", PACKAGE, path_params={"packageId": seeded.package_id}, sub=SUB_B)
    assert status == 403
    status, body = call("DELETE", PACKAGE, path_params={"packageId": seeded.package_id})
    assert status == 202
    d = body["deleted"]
    assert d["s3Objects"] >= 8 and d["documents"] == 1 and d["docAclItems"] == 1 and d["unitStatusItems"] == 2
    assert d["docStatusItems"] > 20
    assert env.s3.list_object_versions(Bucket="rp-data-test", Prefix="gold/").get("Versions") is None
    status, _ = call("GET", PACKAGE, path_params={"packageId": seeded.package_id})
    assert status == 404
