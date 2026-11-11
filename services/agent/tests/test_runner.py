from __future__ import annotations

import asyncio
import copy
from typing import Any

from agent_fixtures import (
    CHUNK,
    GOOD_ANSWER,
    LEO,
    MAYA,
    ScriptedDriver,
    Turn,
    chunk_unit,
    graph_payload,
    loader,
    region_payload,
    retrieved,
    search_payload,
)
from evidence_core.models.answer import AskRequest
from evidence_core.models.golden import GoldenEntry
from rp_agent.driver import DriverSpec
from rp_agent.runner import AskRunner, CallerIdentity
from rp_agent.runs import MemoryRunStore, summary_of

SUB = "7c41e2aa-0000-4000-8000-00000000000a"
CALLER = CallerIdentity(sub=SUB, groups=["lending-analyst"], authorization="Bearer tok")
GOLDEN = [GoldenEntry(qid="q-lend-01", type="table", question="What is the total income across this package?",
                      answer="169,650.00", answer_pattern="169,?650", package_id="pkg-lending-01", headline=True)]
QUESTION = "What is the total income across this package?"


def run(turns: list[Turn], *, pv: str = "2.3.1", direct: dict[str, Any] | None = None,
        tools_disabled: list[str] | None = None, mode: str = "interceptor", factory_error: bool = False,
        package_id: str = "pkg-lending-01", golden: list[GoldenEntry] | None = None):
    store = MemoryRunStore(corpus="c" * 64)
    drivers: list[ScriptedDriver] = []

    def factory(spec: DriverSpec) -> ScriptedDriver:
        if factory_error:
            raise ConnectionError("gateway unreachable")
        d = ScriptedDriver(spec, turns, direct=direct or {})
        drivers.append(d)
        return d

    runner = AskRunner(pipeline_loader=loader, driver_factory=factory, run_store=store,
                       golden=GOLDEN if golden is None else golden,
                       gateway_url="https://gw.example/mcp", identity_mode=mode)
    req = AskRequest(question=QUESTION, package_id=package_id, pipeline_version=pv,
                     tools_disabled=tools_disabled or [])

    async def collect() -> list[dict[str, Any]]:
        return [e async for e in runner.run(req, CALLER, session_id="s" * 36)]

    events = asyncio.run(collect())
    return events, store, drivers


def types(events: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for e in events:
        if not (e["type"] == "text" and out and out[-1] == "text"):
            out.append(e["type"])
    return out


V2_TOOLS = [
    ("search_units", {"query": "W-2 box 1 wages"},
     search_payload([retrieved(MAYA, "Maya Fictus", "98,400.00", 1), retrieved(LEO, "Leo Example", "71,250.00", 2)]),
     "ok"),
    ("graph_hop", {"template": "qi_sum_w2_box1_v1"}, graph_payload(), "ok"),
]


def test_v231_happy_path_event_order_answer_check_and_run_record():
    events, store, (driver,) = run([Turn(V2_TOOLS, GOOD_ANSWER)])
    assert types(events) == ["run_start", "tool_start", "tool_end", "tool_start", "tool_end", "text", "answer",
                             "check", "run"]
    start = events[0]
    assert start["pipelineVersion"] == "2.3.1" and start["modelId"] == "us.anthropic.claude-sonnet-4-6"
    assert start["tools"] == ["search_units", "graph_hop", "get_region", "doc_status"]
    s_end = events[2]
    assert s_end["summary"] == "2 units" and s_end["units"] == [MAYA, LEO]
    assert s_end["scores"][0]["unitId"] == MAYA and len(s_end["retrieved"]) == 2
    g_end = events[4]
    assert g_end["summary"] == "2 rows · total 169,650.00" and g_end["template"] == "qi_sum_w2_box1_v1"
    answer = next(e for e in events if e["type"] == "answer")
    assert answer["total"] == "169,650.00" and answer["uncitedNumbers"] == [] and answer["citationRetries"] == 0
    assert [c["page"] for c in answer["claims"]] == [1, 1] and all(c["resolvable"] for c in answer["claims"])
    check = next(e for e in events if e["type"] == "check")
    assert check == {"type": "check", "qid": "q-lend-01", "expected": "169,650.00", "actual": "169,650.00",
                     "match": True}
    footer = events[-1]
    assert footer["memoryRecordsInjected"] == 0 and footer["corpusHash"] == "c" * 64
    assert set(footer["blockShas"]) == {"preprocessing", "retrieval", "generation", "agent"}
    assert footer["usage"]["totalTokens"] == 150
    # Gateway headers carry the user's token and the lane; identity is NOT injected by agent code in this mode
    assert driver.spec.headers == {"Authorization": "Bearer tok", "X-Rp-Pipeline-Version": "2.3.1",
                                   "X-Rp-Package": "pkg-lending-01", "X-Rp-Run": start["runId"]}
    assert driver.spec.inject is None and driver.spec.temperature == 0 and driver.spec.max_tokens == 700
    assert driver.closed
    (rec,) = store.records
    assert rec.run_id == start["runId"] and rec.s3_key == f"runs/{rec.run_id}.json" and rec.status == "ok"
    assert [c.tool for c in rec.tool_calls] == ["search_units", "graph_hop"]
    assert rec.tool_calls[0].injected["callerSub"] == SUB[:6] and rec.tool_calls[0].injected["via"] == "interceptor"
    assert rec.retrieved_text[0].endswith("98,400.00") and rec.check.match
    s = summary_of(rec)
    assert s.tool_sequence == ["search_units", "graph_hop"] and s.claims == 2 and s.resolvable_claims == 2
    assert all(e.get("t", 0) >= 0 for e in events)


def test_golden_check_runs_for_a_ui_created_package_with_the_same_pdfs():
    """User packages get a random id; the same sample PDFs give the same corpus hash, so the check runs."""
    same = [GOLDEN[0].model_copy(update={"corpus_hash": "c" * 64})]
    events, _, _ = run([Turn(V2_TOOLS, GOOD_ANSWER)], package_id="pkg-my-lending-1a2b", golden=same)
    assert next(e for e in events if e["type"] == "check")["match"] is True
    other = [GOLDEN[0].model_copy(update={"corpus_hash": "d" * 64})]
    events, _, _ = run([Turn(V2_TOOLS, GOOD_ANSWER)], package_id="pkg-my-lending-1a2b", golden=other)
    assert not any(e["type"] == "check" for e in events)


def test_v10_chunk_answer_has_no_region_and_wrong_total_fails_check():
    wrong = {"answer": "Total income is 98,400.00.", "total": "98,400.00", "claims": [
        {"text": "98,400.00", "value": "98400.00", "unitId": CHUNK}]}
    tools = [("search_units", {"query": "total income"}, search_payload([chunk_unit()], "1.0"), "ok"),
             ("graph_hop", {"template": "qi_sum_w2_box1_v1"},
              {"template": "qi_sum_w2_box1_v1", "sparql": "", "graphs": [], "columns": [], "rows": [], "rowCount": 0,
               "note": "no ABox for pipelineVersion 1.0 (ontology: none)", "latencyMs": 0}, "ok")]
    events, store, (driver,) = run([Turn(tools, wrong)], pv="1.0")
    answer = next(e for e in events if e["type"] == "answer")
    (claim,) = answer["claims"]
    assert claim["resolvable"] is False and claim["chunkLabel"] == "chunk c-03"
    assert claim["reason"] == "chunk has no page region"
    assert driver.direct_calls == []  # no region lookups for chunks
    check = next(e for e in events if e["type"] == "check")
    assert check["match"] is False and check["actual"] == "98,400.00"
    g_end = [e for e in events if e["type"] == "tool_end"][1]
    assert g_end["summary"].startswith("0 rows · no ABox")
    assert driver.spec.model_id == "us.anthropic.claude-sonnet-4-6"  # same model both lanes
    assert store.records[0].pipeline_version == "1.0"


def test_uncited_figure_triggers_one_retry_then_reports_leftovers():
    bad = copy.deepcopy(GOOD_ANSWER)
    bad["claims"] = bad["claims"][:1]  # 71,250.00 and the total are uncited
    events, store, (driver,) = run([Turn(V2_TOOLS[:1], bad), Turn([], bad)])
    assert len(driver.prompts) == 2 and "71,250.00" in driver.prompts[1]
    answer = next(e for e in events if e["type"] == "answer")
    assert answer["citationRetries"] == 1 and answer["uncitedNumbers"] == ["169,650.00", "71,250.00"]


def test_retry_that_fixes_the_citation():
    bad = copy.deepcopy(GOOD_ANSWER)
    bad["claims"] = bad["claims"][:1]
    events, _, (driver,) = run([Turn(V2_TOOLS, bad), Turn([], GOOD_ANSWER)])
    answer = next(e for e in events if e["type"] == "answer")
    assert answer["citationRetries"] == 1 and answer["uncitedNumbers"] == []


def test_agent_resolves_missing_regions_with_get_region():
    tools = [("graph_hop", {"template": "qi_sum_w2_box1_v1"}, graph_payload(), "ok")]
    events, store, (driver,) = run([Turn(tools, GOOD_ANSWER)], direct={MAYA: region_payload(MAYA),
                                                                       LEO: region_payload(LEO)})
    assert driver.direct_calls == [{"unitId": MAYA, "fieldId": "f-box1"}, {"unitId": LEO, "fieldId": "f-box1"}]
    answer = next(e for e in events if e["type"] == "answer")
    assert all(c["resolvable"] and c["bbox"]["x0"] == 50 for c in answer["claims"])
    assert [c.tool for c in store.records[0].tool_calls] == ["graph_hop", "get_region", "get_region"]


def test_tools_disabled_and_agent_hook_mode():
    events, _, (driver,) = run([Turn(V2_TOOLS[:1], GOOD_ANSWER)], tools_disabled=["graph_hop", "get_region"],
                               mode="agent_hook")
    assert events[0]["tools"] == ["search_units", "doc_status"]
    assert driver.spec.inject["callerSub"] == SUB and driver.spec.inject["packageId"] == "pkg-lending-01"
    assert driver.direct_calls == []


def test_denied_tool_is_shown_as_denied():
    denied = {"error": {"code": "forbidden", "message": "You don't have access to this package."}}
    reply = {"answer": "I cannot answer from this package.", "claims": [], "caveats": []}
    events, store, _ = run([Turn([("search_units", {"query": "x"}, denied, "denied")], reply)])
    end = next(e for e in events if e["type"] == "tool_end")
    assert end["status"] == "denied" and end["summary"].startswith("denied:")
    assert store.records[0].tool_calls[0].status == "denied"


def test_unparseable_reply_and_failures_end_with_events():
    events, _, _ = run([Turn([], "I think it is 5,000.00"), Turn([], "still 5,000.00")])
    answer = next(e for e in events if e["type"] == "answer")
    assert answer["uncitedNumbers"] == ["5,000.00"] and answer["citationRetries"] == 1
    events, store, _ = run([], factory_error=True)
    assert types(events) == ["run_start", "error"] and events[-1]["runId"] == events[0]["runId"]
    assert store.records[0].status == "error" and "gateway unreachable" in store.records[0].error
