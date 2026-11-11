"""v2 inspection routes: the evidence-unit build replay, the filter funnel, graph paths and the SC-3 composer."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lambdas.api.routes import DOC_BUILD, INSPECT_FILTERS, RUN_CALL_PATH, SHOWCASE_PAYLOAD, SHOWCASES
from lambdas.api.tests.support import BUCKET, SUB_A, SUB_B

B_GROUPS = ["markets-analyst"]


def _put_json(env: Any, key: str, data: Any) -> None:
    env.s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(data).encode(), ContentType="application/json")


# --- GET /documents/{docId}/lanes/{pv}/build ---------------------------------------------------------------

def _report(seeded: Any) -> dict[str, Any]:
    unit = seeded.unit_id
    return {"docId": seeded.doc_id, "pipelineVersion": "2.3.1", "builtAt": "2026-11-11T00:00:00.000Z",
            "embedModel": "amazon.titan-embed-text-v2:0", "tau": 0.4, "phaseB": "cosine",
            "phaseCounts": {"A": 1, "B": 0, "C": 0, "marker": 0}, "derivedRoles": {"unit_label": 1},
            "allocations": [{"block": "b-0003", "pointer": "#/texts/2", "role": "text", "phase": "B",
                             "cosine": 0.2164, "tau": 0.4, "allocated": False, "unit": unit}],
            "invariants": {"I1": "deferred (pipelineVersion 2.4.0, post-lock)"}}


def test_build_replay_joins_scores_to_members_without_text(call, seeded, env) -> None:
    from evidence_core import s3keys

    _put_json(env, s3keys.build_report_key(seeded.package_id, seeded.doc_id, "2.3.1"), _report(seeded))
    status, b = call("GET", DOC_BUILD, path_params={"docId": seeded.doc_id, "pv": "2.3.1"})
    assert status == 200, b
    assert b["embedModel"] == "amazon.titan-embed-text-v2:0" and b["tau"] == 0.4
    assert b["phaseCounts"]["A"] == 1 and b["mergedTables"] == 0 and b["derivedRoles"] == {"unit_label": 1}
    (a,) = b["allocations"]  # the seeded W-2 unit has one member (its table); the held unit has none
    assert a == a | {"blockPointer": "#/tables/0", "memberId": "m-01", "phase": "A", "allocated": True,
                     "unitId": seeded.unit_id, "page": 1}
    assert b["invariants"]["mode"] == "off" and "deferred" in b["invariants"]["note"]
    assert {s["key"].rsplit("/", 1)[-1] for s in b["sources"]} == {"build_report.json", "units.jsonl"}
    assert all(s["versionId"] and len(s["sha256"]) == 64 for s in b["sources"])
    assert "900-12-3456" not in json.dumps(b) and "text" not in json.dumps(b["allocations"])


def test_build_replay_access_and_lanes(call, seeded, env) -> None:
    from evidence_core import s3keys

    status, _ = call("GET", DOC_BUILD, path_params={"docId": seeded.doc_id, "pv": "2.3.1"})
    assert status == 404  # no build report yet
    _put_json(env, s3keys.build_report_key(seeded.package_id, seeded.doc_id, "2.3.1"), _report(seeded))
    status, err = call("GET", DOC_BUILD, path_params={"docId": seeded.doc_id, "pv": "1.0"})
    assert status == 404 and "fixed chunks" in err["error"]["message"]
    status, _ = call("GET", DOC_BUILD, path_params={"docId": seeded.doc_id, "pv": "2.3.1"}, sub=SUB_B,
                     groups=B_GROUPS)
    assert status == 403


def test_compose_build_lists_phase_b_losers_and_marker_links() -> None:
    from evidence_core.bbox import BBox
    from evidence_core.enums import CanonicalRole, Engine, UnitType
    from evidence_core.models.units import BlockRef, Member, Unit, UnitProvenance

    from lambdas.api.routes.build import compose_build

    def ref(p: str) -> BlockRef:
        return BlockRef(engine=Engine.DOCLING, artifact_key="silver/k", pointer=p, engine_label="x")

    def unit(n: int, members: list[Member], unit_type: UnitType, build: dict[str, Any]) -> Unit:
        return Unit(unit_id=f"doc-aaaaaa:2.3.1:eu-0{n}", doc_id="doc-aaaaaa", package_id="pkg-x",
                    pipeline_version="2.3.1", unit_type=unit_type, label="l", file_name="f.pdf", text="t",
                    pages=[1], allowed_principals=[], members=members, chars=1,
                    provenance=UnitProvenance(engine=Engine.DOCLING, source_key="b", source_sha="s", config_sha="c",
                                              build=build))

    box = BBox(page=1, x0=1, y0=1, x1=2, y1=2)
    fn = BBox(page=2, x0=60, y0=109.77, x1=218.01, y1=120.87)
    seed = unit(1, [Member(member_id="m-01", role=CanonicalRole.TABLE, bbox=box, block_ref=ref("#/tables/0"),
                           phase="A"),
                    Member(member_id="m-02", role=CanonicalRole.TEXT, bbox=box, block_ref=ref("#/texts/1"),
                           phase="B", alloc_score=0.6766),
                    Member(member_id="m-03", role=CanonicalRole.FOOTNOTE, bbox=fn, block_ref=ref("#/texts/9"),
                           phase="marker")],
                UnitType.TABLE_PANEL,
                {"markerLinks": [{"marker": "3", "footnoteMember": "m-03", "fieldId": "f-coupon", "crossPage": True,
                                  "evidence": "raised glyph",
                                  "markerBox": {"page": 1, "x0": 277.98, "y0": 446.29, "x1": 280.8, "y1": 450.67}}],
                 "mergedTables": 1})
    rest = unit(2, [Member(member_id="m-01", role=CanonicalRole.TEXT, bbox=box, block_ref=ref("#/texts/2"),
                           phase="C")], UnitType.SECTION_TEXT, {})
    report = {"docId": "doc-aaaaaa", "pipelineVersion": "2.3.1", "embedModel": "amazon.titan-embed-text-v2:0",
              "tau": 0.4, "phaseCounts": {"A": 1, "B": 1, "C": 1, "marker": 1},
              "allocations": [{"pointer": "#/texts/1", "phase": "B", "cosine": 0.6766, "unit": seed.unit_id},
                              {"pointer": "#/texts/2", "phase": "B", "cosine": 0.1781, "unit": seed.unit_id}]}
    b = compose_build(report, [seed, rest], package_id="pkg-x", sources=[])
    by = {a.block_pointer: a for a in b.allocations}
    assert (by["#/texts/1"].phase, by["#/texts/1"].score, by["#/texts/1"].allocated) == ("B", 0.6766, True)
    loser = by["#/texts/2"]  # scored in B below tau, then residual
    assert (loser.phase, loser.score, loser.tau, loser.allocated, loser.unit_id, loser.candidate_unit_id) == (
        "C", 0.1781, 0.4, False, rest.unit_id, seed.unit_id)
    assert by["#/texts/9"].phase == "marker" and by["#/texts/9"].cross_page is True
    (link,) = b.marker_links
    assert (link.marker, link.field_id, link.footnote_page, link.marker_box.page) == ("3", "f-coupon", 2, 1)
    assert b.merged_tables == 1


# --- GET /inspect/filters, GET /runs/{runId}/calls/{callId}/path ---------------------------------------------

FILTER = {"bool": {"filter": [{"terms": {"allowedPrincipals": [f"user:{SUB_A}", "group:lending-analyst"]}},
                              {"term": {"packageId": "pkg-lending-01"}}, {"term": {"pipelineVersion": "2.3.1"}}],
                   "must_not": [{"term": {"piiFlag": "held"}}]}}


def _put_run(env: Any, package_id: str, run_id: str = "r-0000000000aa") -> str:
    from evidence_core.ddb import to_ddb

    summary = {"runId": run_id, "createdAt": "2026-11-11T10:00:00.000Z", "packageId": package_id,
               "pipelineVersion": "2.3.1", "question": "What is the total income?", "callerSub": SUB_A,
               "modelId": "m", "promptSha": "p", "configSha": "c", "corpusHash": "h", "latencyMs": 1200,
               "status": "ok", "s3Key": f"runs/{run_id}.json"}
    env.deps.table("runs").put_item(Item=to_ddb(summary))
    search = {"callId": "c-search", "tool": "search_units", "input": {"query": "W-2 Box 1 wages"}, "injected": {},
              "startedAt": "2026-11-11T10:00:01.000Z", "ms": 120, "status": "ok",
              "output": {"units": [{"unitId": "u1"}, {"unitId": "u2"}],
                         "query": {"index": "units-v2-3-1", "k": 5, "subQueryK": 20,
                                   "weights": {"dense": 0.7, "bm25": 0.3}, "normalization": "min_max",
                                   "filter": FILTER, "embedModel": "amazon.titan-embed-text-v2:0"}}}
    rows = [{"borrower": "Maya Fictus", "taxYear": "2025", "wagesBox1": "98400",
             "unit": "urn:rp:unit:w2-maya-aaaaaa:2.3.1:eu-02",
             "field": "urn:rp:field:w2-maya-aaaaaa:2.3.1:eu-02/f-box1"}]
    hop = {"callId": "c-hop", "tool": "graph_hop", "input": {"template": "qi_sum_w2_box1_v1", "params": {}},
           "injected": {}, "startedAt": "2026-11-11T10:00:02.000Z", "ms": 30, "status": "ok",
           "output": {"template": "qi_sum_w2_box1_v1", "sparql": "SELECT …", "rows": rows, "rowCount": 1,
                      "graphs": ["urn:rp:graph:tbox:rp", f"urn:rp:graph:abox:{package_id}:2.3.1"], "latencyMs": 9}}
    record = summary | {"groups": ["lending-analyst"], "blockShas": {}, "toolsEnabled": ["search_units", "graph_hop"],
                        "toolCalls": [search, hop], "retrieved": [], "retrievedText": []}
    env.s3.put_object(Bucket=BUCKET, Key=f"runs/{run_id}.json", Body=json.dumps(record).encode())
    return run_id


class FakeCounter:
    label = "test counter"

    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any]]] = []

    def count(self, index: str, query: dict[str, Any]) -> int:
        self.queries.append((index, query))
        if "terms" in query["bool"]["filter"][0]:  # the scope: package, lane, the caller's principals
            return 31
        return 9 if query["bool"].get("must") else 12


def test_inspect_filters_counts_the_logged_filter(call, seeded, env) -> None:
    run_id = _put_run(env, seeded.package_id)
    env.deps.aoss = FakeCounter()
    status, f = call("GET", INSPECT_FILTERS, query={"runId": run_id, "callId": "c-search"})
    assert status == 200, f
    assert f["counts"] == {"indexTotal": 31, "afterFilter": 12, "bm25Matches": 9, "denseTopN": 12,
                           "bm25TopN": 9, "fusedK": 2, "derived": ["denseTopN", "bm25TopN"]}
    assert f["filter"] == FILTER and f["queryText"] == "W-2 Box 1 wages" and f["countedBy"] == "test counter"
    queries = env.deps.aoss.queries
    assert all(i == "units-v2-3-1" for i, _ in queries)
    # the first bar is never the whole lane index (shared by every package): the run's package and lane, as
    # far as the caller may read them
    from evidence_core.ids import principals_for

    mine = principals_for(SUB_A, ["lending-analyst"])
    assert queries[0][1] == {"bool": {"filter": [{"terms": {"allowedPrincipals": mine}},
                                                 {"term": {"packageId": seeded.package_id}},
                                                 {"term": {"pipelineVersion": "2.3.1"}}]}}
    assert all("match_all" not in q for _, q in queries)
    assert f["scope"] == queries[0][1] and f["countedAt"] and f["counts"]["derived"] == ["denseTopN", "bm25TopN"]
    assert queries[1][1] == {"bool": {"filter": [FILTER]}}  # exactly the logged filter, inside the query
    assert queries[2][1]["bool"]["must"][0]["match"]["searchText"]["query"] == "W-2 Box 1 wages"
    assert "counterfactual" not in f or f["counterfactual"] is None


def test_inspect_filters_refuses_other_users_and_other_tools(call, seeded, env) -> None:
    run_id = _put_run(env, seeded.package_id)
    env.deps.aoss = FakeCounter()
    status, _ = call("GET", INSPECT_FILTERS, query={"runId": run_id, "callId": "c-search"}, sub=SUB_B,
                     groups=B_GROUPS)
    assert status == 403 and env.deps.aoss.queries == []
    status, _ = call("GET", INSPECT_FILTERS, query={"runId": run_id, "callId": "c-hop"})
    assert status == 400
    status, _ = call("GET", INSPECT_FILTERS, query={"runId": run_id, "callId": "nope"})
    assert status == 404
    status, _ = call("GET", INSPECT_FILTERS, query={"runId": run_id})
    assert status == 400


def test_run_call_path_draws_what_the_rows_bound(call, seeded, env) -> None:
    run_id = _put_run(env, seeded.package_id)
    status, g = call("GET", RUN_CALL_PATH, path_params={"runId": run_id, "callId": "c-hop"})
    assert status == 200, g
    assert g["source"] == "logged-rows" and g["template"] == "qi_sum_w2_box1_v1" and g["latencyMs"] == 9
    field = "urn:rp:field:w2-maya-aaaaaa:2.3.1:eu-02/f-box1"
    assert g["focus"] == field
    edges = {(e["s"], e["label"], e["o"]) for e in g["edges"]}
    assert (field, "prov:wasDerivedFrom", "urn:rp:unit:w2-maya-aaaaaa:2.3.1:eu-02") in edges
    assert ("urn:rp:ont:fin:QualifyingIncome", "fin:operandConcept", "urn:rp:ont:fin:W2WagesBox1") in edges
    kinds = {n["iri"]: n["kind"] for n in g["nodes"]}
    assert kinds[field] == "field" and kinds["urn:rp:ont:fin:W2WagesBox1"] == "class"
    assert any(n["kind"] == "literal" and n["label"] == "98400" for n in g["nodes"])
    status, _ = call("GET", RUN_CALL_PATH, path_params={"runId": run_id, "callId": "c-search"})
    assert status == 400
    status, _ = call("GET", RUN_CALL_PATH, path_params={"runId": run_id, "callId": "c-hop"}, sub=SUB_B,
                     groups=B_GROUPS)
    assert status == 403


# --- GET /showcases, GET /showcases/sc-3/payload -------------------------------------------------------------

@pytest.fixture
def tradeconf(env: Any, seeded: Any) -> dict[str, Any]:
    """A trade confirmation in the seeded package with the 2.3.1 terms unit the builder writes for D2."""
    from evidence_core import s3keys
    from evidence_core.ddb import to_ddb
    from evidence_core.models.api import DocAclItem, DocumentItem

    from lambdas.ingest_lib.tests.test_markers_step_events import _terms_unit

    principals = [f"user:{SUB_A}", "group:lending-analyst"]
    doc_id = "trade-confirmation-xs2031-9af5c5"
    unit = _terms_unit().model_copy(update={"package_id": seeded.package_id, "allowed_principals": principals})
    doc = DocumentItem(package_id=seeded.package_id, doc_id=doc_id, file_name=unit.file_name, bronze_key="bronze/x",
                       size_bytes=1, sha256="ab" * 32, page_count=2, doc_type="doc:TradeConfirmation",
                       uploaded_by=SUB_A, upload_state="IN_BRONZE", upload_url_issued_at="2026-11-11T00:00:00.000Z")
    env.deps.table("documents").put_item(Item=to_ddb(doc.dump()))
    env.deps.table("doc_acl").put_item(Item=to_ddb(DocAclItem(doc_id=doc_id, package_id=seeded.package_id,
                                                              owner_sub=SUB_A, allowed_principals=principals).dump()))
    env.s3.put_object(Bucket=BUCKET, Key=s3keys.gold_units_key(seeded.package_id, doc_id, "2.3.1"),
                      Body=(unit.dump_json() + "\n").encode())
    events: list[dict[str, Any]] = []
    row = {"security": "XS-2031", "unit": f"urn:rp:unit:{unit.unit_id}", "event": "urn:rp:step:x", "newRate": "6",
           "effectiveFromYear": "3", "triggerRating": "BBB-", "page": "2"}

    def graph_hop(event: dict[str, Any]) -> dict[str, Any]:
        events.append(event)
        return {"template": "coupon_terms_v1", "rows": [row], "rowCount": 1, "graphs": ["g"], "latencyMs": 4}

    env.deps.graph_hop = graph_hop
    return {"unit": unit, "events": events, "doc_id": doc_id}


def test_showcase_catalog_lists_server_composers(call, env) -> None:
    status, c = call("GET", SHOWCASES)
    assert status == 200 and [i["showcaseId"] for i in c["items"]][0] == "sc-3"


def test_sc3_payload_reads_units_and_the_graph_with_injected_identity(call, seeded, tradeconf) -> None:
    status, p = call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-3"},
                     query={"packageId": seeded.package_id, "security": "xs-2031"})
    assert status == 200, p
    d = p["data"]
    assert d["unitId"] == tradeconf["unit"].unit_id and d["couponField"]["value"] == "4.25%"
    assert d["markerLink"]["marker"] == "3" and d["markerLink"]["footnotePage"] == 2
    assert d["stepEventParse"] == "ok" and d["stepEvent"]["triggerRating"] == "BBB-"
    # the hop path is the row's bound patterns only (graph_paths.SHAPES), drawn without another graph call
    assert d["path"]["source"] == "logged-rows" and d["path"]["template"] == "coupon_terms_v1"
    assert {e["label"] for e in d["path"]["edges"]} == {"fin:newRate", "fin:effectiveFromYear", "fin:triggerRating"}
    (ev,) = tradeconf["events"]  # what the composer sent rp-graph-hop: the caller, never request input
    assert ev | {"runId": "x"} == {"template": "coupon_terms_v1", "params": {"security": "xs-2031"},
                                   "callerSub": SUB_A, "groups": ["lending-analyst"], "pipelineVersion": "2.3.1",
                                   "packageId": seeded.package_id, "runId": "x"}
    assert p["mode"] == "live" and len(p["payloadSha"]) == 64 and p["provenance"]["$.data.markerLink"] == "derived"


def test_sc3_payload_errors(call, seeded, tradeconf) -> None:
    q = {"packageId": seeded.package_id}
    assert call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-3"}, query=q)[0] == 400
    assert call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-3"}, query=q | {"security": "XS-9"})[0] == 404
    sc0 = call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-0"}, query=q | {"security": "XS-2031"})
    assert sc0[0] == 404
    status, _ = call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-3"},
                     query=q | {"security": "XS-2031"}, sub=SUB_B, groups=B_GROUPS)
    assert status == 403 and tradeconf["events"] == []


def test_sc3_payload_skips_documents_the_callers_doc_acl_does_not_allow(call, env, seeded, tradeconf) -> None:
    """Package access is not document access: a trade confirmation outside the caller's DocAcl is skipped, so its
    fields, classify output and graph rows never reach the payload (404, the same answer as no such security)."""
    from evidence_core.ddb import to_ddb
    from evidence_core.models.api import DocAclItem

    owner_only = DocAclItem(doc_id=tradeconf["doc_id"], package_id=seeded.package_id, owner_sub=SUB_A,
                            allowed_principals=[f"user:{SUB_A}"])
    env.deps.table("doc_acl").put_item(Item=to_ddb(owner_only.dump()))
    q = {"packageId": seeded.package_id, "security": "XS-2031"}
    status, body = call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-3"}, query=q, sub=SUB_B,
                        groups=["lending-analyst"])  # in the package's ACL through the group, not the doc's
    assert status == 404 and tradeconf["events"] == [] and "4.25" not in json.dumps(body)
    assert call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-3"}, query=q)[0] == 200  # the owner


def test_sc3_identity_comes_from_the_authorizer_never_from_the_request(call, seeded, tradeconf) -> None:
    q = {"packageId": seeded.package_id, "security": "XS-2031", "callerSub": "someone-else", "groups": "admin",
         "pipelineVersion": "1.0", "template": "qi_sum_w2_box1_v1"}
    status, _ = call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-3"}, query=q,
                     groups=["lending-analyst", "auditor"])
    assert status == 200
    (ev,) = tradeconf["events"]
    assert ev["callerSub"] == SUB_A and ev["groups"] == ["lending-analyst", "auditor"]
    assert ev["pipelineVersion"] == "2.3.1" and ev["template"] == "coupon_terms_v1"
    assert ev["params"] == {"security": "XS-2031"}


def test_sc3_rejects_parameters_outside_the_template_pattern(call, seeded, tradeconf) -> None:
    for bad in ('XS-2031" } UNION { ?s ?p ?o', "x" * 40, "-XS"):
        status, _ = call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-3"},
                         query={"packageId": seeded.package_id, "security": bad})
        assert status == 400, bad
    assert tradeconf["events"] == []


def test_api_may_run_only_allow_listed_showcase_templates(env) -> None:
    import base64

    from lambdas.api.deps import GRAPH_HOP_SOURCE, check_showcase_template

    with pytest.raises(ValueError, match="may not run"):
        check_showcase_template("qi_sum_w2_box1_v1", {})
    with pytest.raises(ValueError, match="takes parameters"):
        check_showcase_template("coupon_terms_v1", {"security": "XS-2031", "unitId": "u"})
    check_showcase_template("coupon_terms_v1", {"security": "XS-2031"})

    class FakeLambda:
        def __init__(self) -> None:
            self.kw: dict[str, Any] = {}

        def invoke(self, **kw: Any) -> dict[str, Any]:
            import io

            self.kw = kw
            return {"Payload": io.BytesIO(b'{"rows": []}')}

    env.deps.graph_hop, env.deps.lambda_client = None, FakeLambda()
    env.deps.invoke_graph_hop("coupon_terms_v1", {"security": "XS-2031"}, caller_sub=SUB_A, groups=["g"],
                              pipeline_version="2.3.1", package_id="pkg", run_id="r-1")
    kw = env.deps.lambda_client.kw
    assert kw["FunctionName"] == "rp-graph-hop:live"  # the alias the Gateway targets, never $LATEST
    assert json.loads(base64.b64decode(kw["ClientContext"]))["custom"]["rpSource"] == GRAPH_HOP_SOURCE == "rp-api"
    with pytest.raises(ValueError):
        env.deps.invoke_graph_hop("unit_lineage_v1", {"unitId": "u"}, caller_sub=SUB_A, groups=[],
                                  pipeline_version="2.3.1", package_id="pkg", run_id="r-1")


def test_marker_links_serialise_the_same_in_the_build_replay_and_sc3(seeded, tradeconf) -> None:
    from evidence_core.models.inspect import MarkerLink

    from lambdas.api.routes.build import compose_build

    unit = tradeconf["unit"]
    (raw,) = unit.provenance.build["markerLinks"]
    replay = compose_build({"tau": 0.4, "allocations": []}, [unit], package_id=seeded.package_id, sources=[])
    direct = MarkerLink.from_build(unit.unit_id, raw, {m.member_id: m for m in unit.members})
    assert replay.marker_links == [direct] and direct.evidence == "raised glyph"


def test_graph_path_labels_never_collide() -> None:
    from lambdas.api.graph_paths import path_from_rows

    rows = [{"borrower": who, "taxYear": "2025", "wagesBox1": amt, "unit": f"urn:rp:unit:{doc}:2.3.1:eu-02",
             "field": f"urn:rp:field:{doc}:2.3.1:eu-02/f-box1"}
            for who, amt, doc in (("Arjun Fictus", "71250", "w2-2025-arjun-fictus-546450"),
                                  ("Maya Fictus", "98400", "w2-2025-maya-fictus-827d37"))]
    g = path_from_rows("qi_sum_w2_box1_v1", rows, ["urn:rp:graph:abox:pkg:2.3.1"])
    labels = [n.label for n in g.nodes if n.kind in ("field", "unit")]
    assert len(labels) == len(set(labels)) == 4
    assert "field f-box1 · w2-2025-maya-fictus" in labels and "unit eu-02 · w2-2025-arjun-fictus" in labels
    one = path_from_rows("qi_sum_w2_box1_v1", rows[:1], ["urn:rp:graph:abox:pkg:2.3.1"])
    assert {n.label for n in one.nodes if n.kind == "field"} == {"field f-box1"}  # only when two collide
