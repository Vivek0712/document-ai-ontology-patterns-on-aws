"""v2 routes: access decisions (SC-7), the Git-governed definition (SC-8), review decisions (W-14),
evaluate (SC-12), cost (SC-13), the ontology explorer (W-09), the graph neighbourhood (W-11) and the SC-7/SC-10
composers over logged runs."""

from __future__ import annotations

import json
from typing import Any

import pytest

from lambdas.api.routes import (
    ACCESS_DECISIONS,
    COST,
    EVAL_RUN,
    EVAL_RUNS,
    GOVERNED_DEFINITION,
    GRAPH_NEIGHBOURHOOD,
    ONTOLOGY_CLASSES,
    ONTOLOGY_TERM,
    REVIEW,
    REVIEW_DECISION,
    SHOWCASE_PAYLOAD,
    SHOWCASES,
)
from lambdas.api.tests.support import BUCKET, SUB_A, SUB_B

B_GROUPS = ["markets-analyst"]
QUESTION = "What is the total income across this package?"


def _put_json(env: Any, key: str, data: Any) -> None:
    env.s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(data).encode(), ContentType="application/json")


def put_run(env: Any, package_id: str, run_id: str, *, sub: str = SUB_A, groups: list[str] | None = None,
            pv: str = "1.0", profile: str = "default", model: str = "us.anthropic.claude-sonnet-4-6",
            total: str | None = "199,000.00", units: int = 2, status: str = "ok", resolvable: bool = False,
            created: str = "2026-11-11T10:00:00.000Z", usage: dict[str, int] | None = None,
            search_denied: bool = False, tool_spec_sha: str | None = None) -> dict[str, Any]:
    """A RunRecord as the agent's run store writes it (S3 record + rp-runs summary)."""
    from evidence_core.ddb import to_ddb

    injected = {"callerSub": sub[:6], "groups": groups or ["lending-analyst"], "pipelineVersion": pv,
                "packageId": package_id, "runId": run_id, "via": "interceptor"}
    refusal = "You don't have access to this package."
    search = {"callId": f"{run_id}-s", "tool": "search_units", "input": {"query": "income", "callerSub": "spoof"},
              "injected": injected, "startedAt": created, "ms": 40, "status": "denied" if search_denied else "ok",
              "output": ({"error": {"code": "forbidden", "message": refusal}} if search_denied else
                         {"units": [{"unitId": f"u{i}"} for i in range(units)],
                          "query": {"index": f"units-v{pv.replace('.', '-')}",
                                    "filter": {"term": {"packageId": package_id}}}}),
              **({"error": refusal} if search_denied else {})}
    claims = [{"text": "98,400.00", "value": "98400.00", "unitId": "w2:2.3.1:eu-02", "fieldId": "f-box1",
               "resolvable": resolvable, "label": "chunk c-01",
               "reason": None if resolvable else "chunk has no page region"}]
    summary = {"runId": run_id, "createdAt": created, "packageId": package_id, "pipelineVersion": pv,
               "question": QUESTION, "callerSub": sub, "modelId": model, "promptSha": "p", "configSha": f"c{pv}",
               "corpusHash": "h", "latencyMs": 1000, "status": status, "s3Key": f"runs/{run_id}.json",
               "generationProfile": profile}
    record = summary | {"groups": groups or ["lending-analyst"], "blockShas": {"generation": "g", "agent": "a"},
                        "toolsEnabled": ["search_units", "graph_hop"], "toolCalls": [search], "retrieved": [],
                        "retrievedText": ["chunk text"], "usage": usage or {"inputTokens": 10000, "outputTokens": 800},
                        "answer": ({"answer": f"Total {total}", "total": total, "claims": claims} if status == "ok"
                                   else None),
                        "modelKey": "MODEL_FRONTIER" if profile == "frontier" else "MODEL_ANSWER",
                        **({"toolSpecSha": tool_spec_sha} if tool_spec_sha else {})}
    env.deps.table("runs").put_item(Item=to_ddb(summary))
    _put_json(env, f"runs/{run_id}.json", record)
    return record


# --- GET /access/decisions ----------------------------------------------------------------------------------

def test_access_decisions_rebuilt_from_the_run_record(call, seeded, env) -> None:
    put_run(env, seeded.package_id, "r-0000000000a1")
    status, page = call("GET", ACCESS_DECISIONS, query={"runId": "r-0000000000a1"})
    assert status == 200, page
    (d,) = page["items"]
    assert d["source"] == "run-record" and d["decision"] == "allow" and "rebuilt" in page["note"]
    assert d["callerSub6"] == SUB_A[:6] and d["injected"] == {"pipelineVersion": "1.0", "packageId": seeded.package_id,
                                                             "runId": "r-0000000000a1"}
    # names only: the identity key the model supplied is reported as stripped, its value never leaves the route
    assert d["strippedModelKeys"] == ["callerSub"] and d["modelArgs"] == ["query"] and "spoof" not in json.dumps(page)
    assert d["target"] == "search-units" and d["injectionPoint"] == "interceptor"


def test_a_call_the_tool_refused_is_a_deny_row_never_allow(call, seeded, env) -> None:
    """User B's logged search reached the tool and was refused there: the rebuilt row says deny, with the reason."""
    put_run(env, seeded.package_id, "r-0000000000b1", sub=SUB_B, groups=B_GROUPS, units=0, total=None,
            search_denied=True)
    status, page = call("GET", ACCESS_DECISIONS, query={"runId": "r-0000000000b1"})
    assert status == 200, page
    (d,) = page["items"]
    assert d["decision"] == "deny" and d["reason"] == "You don't have access to this package."
    assert d["source"] == "run-record" and "tool's" in page["note"]


def test_access_decisions_prefer_the_access_log_and_check_acl(call, seeded, env) -> None:
    from evidence_core.ddb import to_ddb

    from lambdas.interceptor.access_log import decision_row

    put_run(env, seeded.package_id, "r-0000000000a2")
    row = decision_row(gateway_tool="search-units___search_units", call_id=7, sub=SUB_A, groups=["lending-analyst"],
                       values={"runId": "r-0000000000a2", "pipelineVersion": "1.0", "packageId": seeded.package_id},
                       model_args={"query": "x"}, decision="allow")
    env.deps.table("access_log").put_item(Item=to_ddb(row.dump() | {"sk": f"{row.at}#{row.call_id}"}))
    status, page = call("GET", ACCESS_DECISIONS, query={"runId": "r-0000000000a2"})
    assert status == 200 and [d["source"] for d in page["items"]] == ["access-log"] and "note" not in page
    status, _ = call("GET", ACCESS_DECISIONS, query={"runId": "r-0000000000a2"}, sub=SUB_B, groups=B_GROUPS)
    assert status == 403
    status, mine = call("GET", ACCESS_DECISIONS, query={"since": row.at})
    assert status == 200 and [d["callId"] for d in mine["items"]] == ["7"]
    status, theirs = call("GET", ACCESS_DECISIONS, query={"since": row.at}, sub=SUB_B, groups=B_GROUPS)
    assert status == 200 and theirs["items"] == []  # never another user's rows
    assert call("GET", ACCESS_DECISIONS, query={})[0] == 400
    assert call("GET", ACCESS_DECISIONS, query={"since": "2020-01-01T00:00:00Z"})[0] == 400


# --- GET /governance/definitions/{name} ---------------------------------------------------------------------

def test_governed_definition_serves_the_build_record(call, env) -> None:
    status, d = call("GET", GOVERNED_DEFINITION, path_params={"name": "qualifying_income"})
    assert status == 200, d
    assert d["source"] == "git" and d["definition"]["iri"] == "urn:rp:ont:fin:QualifyingIncome"
    assert d["definition"]["computedBy"] == "qi_sum_w2_box1_v1" and "W-2 Box 1" in d["definition"]["description"]
    assert d["check"]["command"] == "make ontology-check" and d["check"]["passed"] == d["check"]["total"] > 0
    if d["coa"]["mode"] == "off":  # make ontology-check COA_MODE=off: the Git record only
        assert d["coa"] == d["coa"] | {"state": "not-captured"} and "snapshot" not in d["coa"]
    else:  # COA_MODE=snapshot (the default): a recorded COA session, never presented as live
        assert d["coa"] == d["coa"] | {"mode": "snapshot", "state": "captured"}
        snap = d["coa"]["snapshot"]
        assert d["coa"]["snapshotAt"] == snap["capturedAt"] and snap["crossCheck"]["value"] == "169650.00"
        assert snap["ontology"]["label"] == "uploaded, not steward-reviewed" and snap["latency"]["measured"] is True
    assert call("GET", GOVERNED_DEFINITION, path_params={"name": "net_worth"})[0] == 404
    assert call("GET", GOVERNED_DEFINITION, path_params={"name": "../x"})[0] == 400


# --- POST /review/{packageId}/items/{reviewKey}/decision ----------------------------------------------------

@pytest.fixture
def review_item(env: Any, seeded: Any) -> str:
    from evidence_core.ddb import to_ddb

    key = f"{seeded.doc_id}#2.3.1#{seeded.unit_id}#f-box1"
    env.deps.table("review_queue").put_item(Item=to_ddb({
        "packageId": seeded.package_id, "reviewKey": key, "docId": seeded.doc_id, "pipelineVersion": "2.3.1",
        "unitId": seeded.unit_id, "fieldId": "f-box1", "label": "1 Wages, tips, other compensation",
        "value": "98,400.00", "confidence": 0.71, "threshold": 0.8, "engine": "docling",
        "reason": "confidence below threshold", "createdAt": "2026-11-11T09:00:00.000Z"}))
    return key


def _decide(call: Any, seeded: Any, key: str, body: dict[str, Any], **kw: Any) -> tuple[int, Any]:
    return call("POST", REVIEW_DECISION, path_params={"packageId": seeded.package_id, "reviewKey": key},
                body=json.dumps(body), **kw)


def test_review_decision_records_reviewer_and_keeps_the_extracted_value(call, seeded, review_item) -> None:
    status, item = _decide(call, seeded, review_item, {"action": "CORRECT", "value": "98,450.00", "note": "rescan"})
    assert status == 200, item
    assert item["state"] == "CORRECTED" and item["value"] == "98,400.00"  # the extracted value is never overwritten
    assert item["decision"] == item["decision"] | {"action": "CORRECT", "value": "98,450.00", "by": SUB_A[:6]}
    seen = item["decision"]["at"]  # the view the reviewer acts on
    status, item = _decide(call, seeded, review_item, {"action": "ACCEPT", "expectedDecisionAt": seen})
    assert status == 200 and item["state"] == "ACCEPTED" and [h["action"] for h in item["history"]] == ["CORRECT"]
    assert _decide(call, seeded, review_item, {"action": "CORRECT"})[0] == 400  # needs a value
    assert _decide(call, seeded, review_item, {"action": "REJECT", "value": "1"})[0] == 400
    assert _decide(call, seeded, "nope", {"action": "ACCEPT"})[0] == 404
    assert _decide(call, seeded, review_item, {"action": "ACCEPT"}, sub=SUB_B, groups=B_GROUPS)[0] == 403


def test_review_items_carry_the_extract_engines_model_version(call, seeded, env) -> None:
    """The local Textract stand-in has no confidences: GET /review names its model version, so the UI never shows
    its placeholder 0.0 as a measured confidence."""
    from evidence_core.ddb import to_ddb
    from evidence_core.s3keys import textract_page_key

    key = f"{seeded.doc_id}#2.3.1#{seeded.unit_id}#f-net"
    env.deps.table("review_queue").put_item(Item=to_ddb({
        "packageId": seeded.package_id, "reviewKey": key, "docId": seeded.doc_id, "pipelineVersion": "2.3.1",
        "unitId": seeded.unit_id, "fieldId": "f-net", "label": "Net pay", "value": "4,962.75", "confidence": 0.0,
        "threshold": 0.8, "engine": "textract", "bbox": {"page": 1, "x0": 1, "y0": 1, "x1": 2, "y1": 2},
        "reason": "confidence below threshold", "createdAt": "2026-11-11T09:00:00.000Z"}))
    _put_json(env, textract_page_key(seeded.package_id, seeded.doc_id, "2.3.1", 1),
              {"Blocks": [], "AnalyzeDocumentModelVersion": "LOCAL-DOCLING-OCR (not Textract)"})
    status, page = call("GET", REVIEW, query={"packageId": seeded.package_id})
    assert status == 200, page
    (item,) = [i for i in page["items"] if i["reviewKey"] == key]
    assert item["engineModelVersion"].startswith("LOCAL-")
    assert call("GET", REVIEW, query={"packageId": seeded.package_id}, sub=SUB_B, groups=B_GROUPS)[0] == 403


def test_a_decision_on_a_stale_view_is_a_409_and_writes_nothing(call, seeded, review_item) -> None:
    """Two reviewers open the item with no decision; the first decides, the second (still on the old view) gets 409."""
    status, first = _decide(call, seeded, review_item, {"action": "ACCEPT", "expectedDecisionAt": None})
    assert status == 200, first
    status, err = _decide(call, seeded, review_item, {"action": "REJECT"})  # saw no decision: stale
    assert status == 409 and err["error"]["code"] == "conflict"
    status, err = _decide(call, seeded, review_item, {"action": "REJECT", "expectedDecisionAt": "2026-01-01T00:00:00Z"})
    assert status == 409
    status, item = _decide(call, seeded, review_item,
                           {"action": "REJECT", "expectedDecisionAt": first["decision"]["at"]})  # the current view
    assert status == 200 and item["state"] == "REJECTED" and [h["action"] for h in item["history"]] == ["ACCEPT"]


# --- GET /eval/runs ------------------------------------------------------------------------------------------

def _eval_result(package_id: str) -> dict[str, Any]:
    rate = {"hits": 1, "n": 2, "rate": 0.5}
    lane = {"pipelineVersion": "1.0", "meanUnitChars": 1100.0,
            "byType": {"table": {"n": 2, "recallAt1": rate, "regionHitAt1": {"hits": 0, "n": 0},
                                 "answerCorrect": rate, "citationValid": rate, "errors": 0}}}
    return {"evalRunId": "e-logged-abc123", "createdAt": "2026-11-11T10:00:00Z", "mode": "logged",
            "modeNote": "Logged runs re-scored", "goldenPath": "eval/golden.jsonl", "goldenSha": "g" * 64,
            "packageIds": [package_id], "pipelineVersions": ["1.0"], "lanes": {"1.0": lane},
            "table": [{"type": "table", "n": 2, "recallAt1": {"1.0": 0.5}}],
            "outcomes": [{"qid": "q-lend-01", "type": "table", "pipelineVersion": "1.0", "recallAt1": True,
                          "runId": "r-1", "citation": {"valid": False}},
                         {"qid": "q-lend-01", "type": "table", "pipelineVersion": "1.0", "recallAt1": False}],
            "meta": {"goldenTypes": {"table": 15, "prose": 12, "figure": 10, "footnote": 13}}}


def test_eval_withholds_rates_unless_published(call, seeded, env, monkeypatch) -> None:
    _put_json(env, "eval/results/e-logged-abc123.json", _eval_result(seeded.package_id))
    status, listing = call("GET", EVAL_RUNS)
    assert status == 200 and [i["evalRunId"] for i in listing["items"]] == ["e-logged-abc123"]
    assert listing["published"] is False and listing["items"][0]["questions"] == 1
    status, v = call("GET", EVAL_RUN, path_params={"evalRunId": "e-logged-abc123"})
    assert status == 200, v
    assert "lanes" not in v and v["published"] is False and v["withheld"]
    assert '"rate"' not in json.dumps(v) and "0.5" not in json.dumps(v)  # unpublished: no rate in the payload
    assert [o["recallAt1"] for o in v["outcomes"]] == [True, False] and v["outcomes"][0]["citationValid"] is False
    assert v["goldenTypes"] == {"table": 15, "prose": 12, "figure": 10, "footnote": 13}
    assert v["caption"] == "Measured on our constructed corpus. Your documents will differ."
    monkeypatch.setenv("RP_PUBLIC_METRICS", "on")
    status, v = call("GET", EVAL_RUN, path_params={"evalRunId": "e-logged-abc123"})
    assert v["published"] is True and v["lanes"]["1.0"]["byType"]["table"]["recallAt1"]["rate"] == 0.5
    assert call("GET", EVAL_RUN, path_params={"evalRunId": "e-logged-abc123"}, sub=SUB_B, groups=B_GROUPS)[0] == 403
    assert call("GET", EVAL_RUNS, sub=SUB_B, groups=B_GROUPS)[1]["items"] == []
    assert call("GET", EVAL_RUN, path_params={"evalRunId": "../x"})[0] == 400


# --- GET /cost -----------------------------------------------------------------------------------------------

def test_cost_prices_measured_usage_and_hides_dollars_by_default(call, seeded, env, monkeypatch) -> None:
    put_run(env, seeded.package_id, "r-0000000000c1", usage={"inputTokens": 10000, "outputTokens": 1000})
    put_run(env, seeded.package_id, "r-0000000000c2", profile="frontier", model="us.anthropic.claude-opus-4-6-v1",
            usage={"inputTokens": 10000, "outputTokens": 1000})
    monkeypatch.setenv("RP_PUBLIC_METRICS", "on")
    status, v = call("GET", COST, query={"packageId": seeded.package_id, "groupBy": "service"})
    assert status == 200, v
    assert v["label"] == "estimate · measured usage × list price" and v["prices"]["source"] == "config/prices.yaml"
    asks = [ln for ln in v["lines"] if ln["stageId"] == "ask"]
    assert {(ln["runId"], ln["usage"]["unit"]) for ln in asks} == {(r, u) for r in ("r-0000000000c1", "r-0000000000c2")
                                                                   for u in ("token_in", "token_out")}
    assert all(ln["usage"]["measured"] for ln in asks)
    sonnet = sum(ln["estimateUsd"] for ln in asks if ln["runId"] == "r-0000000000c1")
    assert sonnet == pytest.approx((10000 * 3 + 1000 * 15) / 1e6)  # list price × the logged tokens
    tiers = {(t["callType"], t["modelId"]): t for t in v["tiering"]}
    assert tiers[("answer · frontier profile", "us.anthropic.claude-opus-4-6-v1")]["calls"] == 1
    assert tiers[("classify tie-break", "us.amazon.nova-2-lite-v1:0")].get("estimateUsd") is None  # tokens not logged
    assert any(s["stageId"] == "extract" for s in v["stages"])
    monkeypatch.setenv("RP_PUBLIC_METRICS", "off")
    status, v = call("GET", COST, query={"packageId": seeded.package_id})
    assert status == 200 and v["published"] is False and v["withheld"]
    dollars = [ln.get("estimateUsd") for ln in v["lines"]] + [g.get("estimateUsd") for g in v["groups"]] + \
        [t.get("estimateUsd") for t in v["tiering"]] + [d.get("estimateUsd") for d in v["perDocument"]]
    assert dollars and all(x is None for x in dollars)  # unpublished: no dollar amount in the payload
    assert call("GET", COST, query={"packageId": seeded.package_id}, sub=SUB_B, groups=B_GROUPS)[0] == 403
    assert call("GET", COST, query={"packageId": seeded.package_id, "groupBy": "colour"})[0] == 400


def test_cost_tiers_count_the_owners_asks_and_groups_name_unpriced_lines(call, seeded, env, monkeypatch) -> None:
    from lambdas.api.routes import cost as cost_route

    put_run(env, seeded.package_id, "r-0000000000d1", usage={"inputTokens": 9000, "outputTokens": 500})
    # another user's ask (refused at the tools): in the ledger, never in the tier counts
    put_run(env, seeded.package_id, "r-0000000000d2", sub=SUB_B, groups=B_GROUPS, search_denied=True,
            created="2026-11-11T10:05:00.000Z", usage={"inputTokens": 3633, "outputTokens": 120})
    put_run(env, seeded.package_id, "r-0000000000d3", model="vendor.unpriced-model-v1",
            created="2026-11-11T10:06:00.000Z", usage={"inputTokens": 100, "outputTokens": 10})
    monkeypatch.setenv("RP_PUBLIC_METRICS", "on")
    status, v = call("GET", COST, query={"packageId": seeded.package_id, "groupBy": "stage"})
    assert status == 200, v
    assert {ln["runId"] for ln in v["lines"] if ln["stageId"] == "ask"} == {"r-0000000000d1", "r-0000000000d2",
                                                                           "r-0000000000d3"}
    ask_group = next(g for g in v["groups"] if g["key"] == "ask")
    assert ask_group["unpricedLines"] == 2  # d3's model has no list price: counted, never as $0
    answer = next(t for t in v["tiering"] if t["callType"] == "answer")
    assert answer["calls"] == 1 and answer["tokensIn"] == 9000 and "owner" in answer["source"]
    assert v["truncated"] is False
    monkeypatch.setattr(cost_route, "MAX_RUNS", 1)  # the newest run is kept and the view says runs were left out
    status, v = call("GET", COST, query={"packageId": seeded.package_id})
    assert v["truncated"] is True and any("newest 1 runs" in n for n in v["notes"])
    assert {ln["runId"] for ln in v["lines"] if ln["stageId"] == "ask"} == {"r-0000000000d3"}  # the newest
    from evidence_core.models.measure import CostLine, CostUsage

    line = CostLine(package_id="p", pipeline_version="1.0", stage_id="ask", service="x",
                    usage=CostUsage(unit="token_in", quantity=1, measured=True))
    (g,) = cost_route.group([line, line.model_copy(update={"estimate_usd": 0.5})], "stage")
    assert g.lines == 2 and g.unpriced_lines == 1 and g.estimate_usd == 0.5


# --- ontology explorer and graph neighbourhood ---------------------------------------------------------------

def test_ontology_classes_and_terms_come_from_the_built_tbox(call, seeded) -> None:
    status, lst = call("GET", ONTOLOGY_CLASSES, query={"module": "fin"})
    assert status == 200, lst
    assert lst["source"] == "config/ontology/1.2.0/tbox.json" and {i["module"] for i in lst["items"]} == {"fin"}
    assert "fin:QualifyingIncome" in {i["curie"] for i in lst["items"]}
    assert call("GET", ONTOLOGY_CLASSES, query={"module": "nope"})[0] == 400
    status, t = call("GET", ONTOLOGY_TERM, path_params={"iri": "fin:W2WagesBox1"})
    assert status == 200, t
    alt = {lb["value"]: lb["triple"] for lb in t["labels"] if lb["scheme"] == "altLabel"}
    assert alt["Box 1"] == 'fin:W2WagesBox1 skos:altLabel "Box 1"'
    assert isinstance(t["usedBy"], dict)
    assert call("GET", ONTOLOGY_TERM, path_params={"iri": "fin:Nope"})[0] == 404
    assert call("GET", ONTOLOGY_TERM, path_params={"iri": "zzz:Nope"})[0] == 400


def test_graph_neighbourhood_runs_the_api_only_template_with_the_callers_identity(call, seeded, env) -> None:
    seen: list[dict[str, Any]] = []
    box1 = "urn:rp:ont:fin:W2WagesBox1"
    field = "urn:rp:field:w2:2.3.1:eu-02/f-box1"

    def graph_hop(event: dict[str, Any]) -> dict[str, Any]:
        seen.append(event)
        return {"rows": [{"s": box1, "p": "http://www.w3.org/2004/02/skos/core#altLabel", "o": "Box 1",
                          "oIsLiteral": "true", "hop": "1"},
                         {"s": field, "p": "urn:rp:ont:fin:concept", "o": box1, "oIsLiteral": "false", "hop": "1"}],
                "graphs": ["urn:rp:graph:tbox:rp", f"urn:rp:graph:abox:{seeded.package_id}:2.3.1"],
                "sparql": "SELECT …", "latencyMs": 4}

    env.deps.graph_hop = graph_hop
    status, g = call("GET", GRAPH_NEIGHBOURHOOD, query={"iri": "fin:W2WagesBox1", "packageId": seeded.package_id,
                                                        "callerSub": "spoofed"})
    assert status == 200, g
    (ev,) = seen
    assert ev["template"] == "neighbourhood_v1" and ev["params"] == {"iri": box1}  # depth 1: no depth param
    assert ev["callerSub"] == SUB_A and ev["pipelineVersion"] == "2.3.1"
    kinds = {n["iri"]: n["kind"] for n in g["nodes"]}
    assert kinds[box1] == "class" and kinds[field] == "field" and "literal" in kinds.values()
    assert {n["graph"] for n in g["nodes"] if n["kind"] == "field"} == {f"urn:rp:graph:abox:{seeded.package_id}:2.3.1"}
    assert g["source"] == "neptune" and g["truncated"] is False and len(g["edges"]) == 2
    call("GET", GRAPH_NEIGHBOURHOOD, query={"iri": box1, "packageId": seeded.package_id, "depth": "2"})
    assert seen[-1]["params"] == {"iri": box1, "depth": "2"}
    status, g = call("GET", GRAPH_NEIGHBOURHOOD, query={"iri": box1, "packageId": seeded.package_id, "pv": "1.0"})
    assert status == 200 and g["nodes"] == [] and "binds no ontology" in g["sparql"] and len(seen) == 2
    assert call("GET", GRAPH_NEIGHBOURHOOD, query={"iri": box1, "packageId": seeded.package_id},
                sub=SUB_B, groups=B_GROUPS)[0] == 403
    assert call("GET", GRAPH_NEIGHBOURHOOD, query={"iri": "zzz:x", "packageId": seeded.package_id})[0] == 400


def test_neighbourhood_template_is_api_only() -> None:
    """The agent's graph_hop schema (mcp_tools.json, toolSpecSha) never lists the API-only template."""
    from evidence_core.models.tools import API_TOOL_ARG_MODELS, GRAPH_TEMPLATES, GraphHopArgs

    assert "neighbourhood_v1" not in GRAPH_TEMPLATES
    with pytest.raises(ValueError):
        GraphHopArgs.model_validate({"template": "neighbourhood_v1", "params": {"iri": "urn:rp:ont:fin:X"}})
    API_TOOL_ARG_MODELS["graph_hop"].model_validate({"template": "neighbourhood_v1", "params": {"iri": "urn:rp:x"}})


def test_graph_hop_accepts_the_api_only_template_only_from_rp_api(env) -> None:
    """A soft gate: ``rpSource`` is caller-set Lambda client context; IAM limits who may invoke."""
    from types import SimpleNamespace

    from lambdas.tools.graph_hop import handler as graph_hop

    event = {"template": "neighbourhood_v1", "params": {"iri": "urn:rp:ont:fin:X"}, "callerSub": SUB_A,
             "groups": ["g"], "pipelineVersion": "2.3.1", "packageId": "pkg-x", "runId": "r-000000000001"}
    out = graph_hop.handler(event, None)  # a direct or Gateway caller: rejected at validation
    assert out["error"]["code"] == "bad_request" and "template" in out["error"]["message"]
    ctx = SimpleNamespace(client_context=SimpleNamespace(custom={"rpSource": "rp-api"}))
    out = graph_hop.handler(event, ctx)  # rp-api: validated, then the package ACL applies (no such package)
    assert out["error"]["code"] == "forbidden", out  # past validation: the ACL (no such package) answers


# --- composers over logged runs -------------------------------------------------------------------------------

def test_catalog_lists_the_run_composers(call, env) -> None:
    status, c = call("GET", SHOWCASES)
    assert status == 200 and [i["showcaseId"] for i in c["items"]] == ["sc-3", "sc-7", "sc-10"]


def test_sc10_groups_runs_by_generation_profile_and_diffs_the_records(call, seeded, env) -> None:
    for i in range(3):
        put_run(env, seeded.package_id, f"r-00000000001{i}", created=f"2026-11-11T10:0{i}:00.000Z")
    put_run(env, seeded.package_id, "r-000000000020", profile="frontier", model="us.anthropic.claude-opus-4-6-v1",
            total="169,650.00", created="2026-11-11T11:00:00.000Z")
    put_run(env, seeded.package_id, "r-000000000021", profile="frontier", model="us.anthropic.claude-opus-4-6-v1",
            status="error", total=None, created="2026-11-11T11:01:00.000Z")
    put_run(env, seeded.package_id, "r-000000000030", sub=SUB_B, created="2026-11-11T12:00:00.000Z")  # not the owner
    status, p = call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-10"},
                     query={"packageId": seeded.package_id, "question": QUESTION})
    assert status == 200, p
    d = p["data"]
    assert d["profiles"]["default"]["n"] == 3 and d["profiles"]["frontier"] == d["profiles"]["frontier"] | {
        "n": 1, "errors": 1, "modelIds": ["us.anthropic.claude-opus-4-6-v1"]}
    assert [x["key"] for x in d["configDiff"]["differing"]] == ["modelId"]  # only the model differs
    # neither side logged its tool specs: unverified, never counted as identical
    assert [x["key"] for x in d["configDiff"]["unverified"]] == ["toolSpecSha"]
    assert "toolSpecSha" not in d["configDiff"]["identical"]
    assert d["sameRetrievedText"] is True
    assert {r["total"] for r in d["profiles"]["frontier"]["runs"] if r["status"] == "ok"} == {"169,650.00"}
    assert all(c["resolvable"] is False for r in d["profiles"]["frontier"]["runs"] for c in r["chips"])
    assert "r-000000000030" not in p["runIds"] and p["provenance"]["$.data.profiles"] == "measured"
    assert call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-10"},
                query={"packageId": seeded.package_id})[0] == 400


def test_config_diff_reports_tool_specs_logged_on_one_side_as_unverified() -> None:
    from evidence_core.models.answer import RunRecord

    from lambdas.api.compose_runs import config_diff

    base = {"createdAt": "t", "packageId": "p", "pipelineVersion": "1.0", "question": "q", "callerSub": "s",
            "groups": [], "modelId": "m", "promptSha": "p", "configSha": "c", "blockShas": {}, "corpusHash": "h",
            "toolsEnabled": ["search_units"], "toolCalls": [], "retrieved": [], "retrievedText": [], "latencyMs": 1,
            "status": "ok", "s3Key": "k"}
    a = RunRecord.load(base | {"runId": "r-a"})
    b = RunRecord.load(base | {"runId": "r-b", "modelId": "m2", "toolSpecSha": "ba677bd6"})
    d = config_diff(a, b)
    assert [x["key"] for x in d["differing"]] == ["modelId"]
    assert d["unverified"] == [{"key": "toolSpecSha", "a": None, "b": "ba677bd6"}]
    c = RunRecord.load(base | {"runId": "r-c", "toolSpecSha": "ba677bd6"})
    assert "toolSpecSha" in config_diff(b, c)["identical"] and config_diff(b, c)["unverified"] == []


def test_sc7_pairs_the_owners_run_with_another_users_and_their_access_rows(call, seeded, env) -> None:
    put_run(env, seeded.package_id, "r-000000000040", pv="2.3.1", resolvable=True, total="169,650.00")
    put_run(env, seeded.package_id, "r-000000000041", pv="2.3.1", sub=SUB_B, groups=B_GROUPS, units=0, total=None,
            created="2026-11-11T12:00:00.000Z")
    status, p = call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-7"},
                     query={"packageId": seeded.package_id, "question": QUESTION})
    assert status == 200, p
    owner, other = p["data"]["users"]
    assert owner["role"] == "owner" and owner["callerSub6"] == SUB_A[:6]
    assert other["callerSub6"] == SUB_B[:6] and other["groups"] == B_GROUPS
    assert other["runs"]["2.3.1"]["search"][0]["units"] == 0 and owner["runs"]["2.3.1"]["search"][0]["units"] == 2
    assert set(p["data"]["access"]) == {"r-000000000040", "r-000000000041"}
    assert p["data"]["regionProbe"] == {"unitId": "w2:2.3.1:eu-02", "fieldId": "f-box1"}
    status, _ = call("GET", SHOWCASE_PAYLOAD, path_params={"showcaseId": "sc-7"},
                     query={"packageId": seeded.package_id, "question": "Something else?"})
    assert status == 404
