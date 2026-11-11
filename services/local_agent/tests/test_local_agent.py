"""The local ask harness without AWS: a seeded moto world saved as a snapshot, a fake Titan and a scripted
Bedrock Converse stream. Everything between them is the real code: AskRunner, the Strands driver hooks,
the tool Lambda handlers, the citation validator, the run store and the eval scoring."""

from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any

import boto3
import pytest
from evidence_core.env import Settings
from evidence_core.ids import index_name
from evidence_core.models.golden import GoldenEntry
from evidence_core.models.units import IndexDocument, Unit
from evidence_core.retrieval import bm25_query, build_filter, dense_query
from lambdas.tools._lib.aoss import AossClient, IndexMissing
from lambdas.tools._lib.stores import s3_client
from lambdas.tools._lib.testing import BUCKET, seed_world
from local_agent.index import IndexStore, MemoryOpenSearch, knn_score, matches, tokenize
from local_pipeline.recorded import RecordedComprehend, RecordedTitan, Recording, RecordingMiss
from local_pipeline.world import dump_world, open_world
from moto import mock_aws

REPO = Path(__file__).resolve().parents[3]


# --- the in-memory OpenSearch stand-in ---------------------------------------------------------------

def _doc(uid: str, text: str, vec: list[float], *, pv: str = "2.3.1", principals: list[str] | None = None,
         pii: str = "none") -> dict[str, Any]:
    return {"_id": uid, "unitId": uid, "packageId": "pkg-1", "pipelineVersion": pv, "text": text, "breadcrumb": "",
            "allowedPrincipals": principals or ["group:lending-analyst"], "piiFlag": pii, "embedding": vec}


def test_tokenizer_keeps_amounts_whole() -> None:
    assert tokenize("Box 1 Wages: 98,400.00 (TY2025)") == ["box", "1", "wages", "98,400.00", "ty2025"]


def test_filter_inside_both_queries_and_held_units_excluded() -> None:
    flt = build_filter(principals=["user:a", "group:lending-analyst"], package_id="pkg-1", pipeline_version="2.3.1")
    store = IndexStore([
        _doc("u1", "wages 98,400.00", [1.0, 0.0]),
        _doc("u2", "wages held account", [0.9, 0.1], pii="held"),
        _doc("u3", "wages other group", [0.8, 0.2], principals=["group:markets-analyst"]),
        _doc("u4", "wages wrong lane", [0.7, 0.3], pv="1.0"),
    ])
    assert matches(store.docs[0], flt) and not any(matches(d, flt) for d in store.docs[1:])
    dense = store.search(dense_query([1.0, 0.0], flt, 5))["hits"]["hits"]
    bm25 = store.search(bm25_query("wages", flt, 5))["hits"]["hits"]
    assert [h["_id"] for h in dense] == [h["_id"] for h in bm25] == ["u1"]
    assert "embedding" not in dense[0]["_source"]
    assert dense[0]["_score"] == pytest.approx(knn_score(1.0)) == pytest.approx(2.0)


def test_count_answers_the_filter_inspection_queries() -> None:
    """``_count`` as GET /inspect/filters sends it: everything, the logged filter, filter + the BM25 match."""
    flt = build_filter(principals=["user:a", "group:lending-analyst"], package_id="pkg-1", pipeline_version="2.3.1")
    os_ = MemoryOpenSearch(indexes={"units-v2-3-1": IndexStore([
        _doc("u1", "wages 98,400.00", [1.0, 0.0]), _doc("u2", "bank statement", [0.0, 1.0]),
        _doc("u3", "wages held account", [0.9, 0.1], pii="held"),
        _doc("u4", "wages other group", [0.8, 0.2], principals=["group:markets-analyst"])])})
    assert os_.count("units-v2-3-1", {"match_all": {}}) == 4
    assert os_.count("units-v2-3-1", {"bool": {"filter": [flt]}}) == 2
    assert os_.count("units-v2-3-1", bm25_query("wages", flt, 20)["query"]) == 1
    with pytest.raises(RuntimeError):
        os_.count("units-v9", {"match_all": {}})


def test_summary_counts_agent_errors_apart() -> None:
    from local_agent.harness import summarise

    base = {"qid": "q-1", "pipelineVersion": "1.0", "expected": "1.00"}
    rows = [base | {"runId": "r-1", "total": "2.00", "answerCorrect": False},
            base | {"runId": "r-2", "total": "2.00", "answerCorrect": False},
            base | {"runId": None, "total": None, "answerCorrect": None, "error": "agent_error"}]
    (s,) = summarise(rows)
    assert (s["n"], s["errors"], s["correct"], s["totals"], s["representativeRunId"]) == (2, 1, 0, {"2.00": 2}, "r-1")


def test_bm25_prefers_rarer_terms_and_skips_non_matches() -> None:
    flt = {"bool": {"filter": [{"term": {"packageId": "pkg-1"}}]}}
    store = IndexStore([_doc("a", "wages wages total", [1, 0]), _doc("b", "wages box 1", [0, 1]),
                        _doc("c", "bank statement", [0, 1])])
    hits = store.search(bm25_query("box wages", flt, 5))["hits"]["hits"]
    assert [h["_id"] for h in hits] == ["b", "a"]  # "box" is rarer than "wages"; "c" matches nothing


def test_transport_through_the_real_aoss_client() -> None:
    mem = MemoryOpenSearch({"units-v2-3-1": IndexStore([_doc("u1", "wages", [1.0, 0.0])])})
    client = AossClient(endpoint="https://local", sign=False, transport=mem.transport)
    flt = {"bool": {"filter": [{"term": {"packageId": "pkg-1"}}]}}
    assert client.search("units-v2-3-1", bm25_query("wages", flt, 3))["hits"]["hits"][0]["_id"] == "u1"
    with pytest.raises(IndexMissing):
        client.search("units-v9-9", bm25_query("wages", flt, 3))


# --- recorded AWS calls --------------------------------------------------------------------------------

class _CountingComprehend:
    calls = 0

    def detect_pii_entities(self, Text: str, LanguageCode: str) -> dict[str, Any]:  # noqa: N803
        self.calls += 1
        return {"Entities": [{"Type": "SSN", "Score": 0.9, "BeginOffset": 0, "EndOffset": 3, "Extra": "dropped"}]}


def test_recorded_comprehend_replays_without_aws_and_stores_no_text(tmp_path: Path) -> None:
    real = _CountingComprehend()
    rec = Recording.open(tmp_path / "c.json", "comprehend")
    c = RecordedComprehend(rec, lambda: real)
    first = c.detect_pii_entities(Text="123 secret", LanguageCode="en")
    assert c.detect_pii_entities(Text="123 secret", LanguageCode="en") == first and real.calls == 1
    rec.save()
    assert "secret" not in (tmp_path / "c.json").read_text() and "Extra" not in (tmp_path / "c.json").read_text()
    offline = RecordedComprehend(Recording.open(tmp_path / "c.json", "comprehend"), lambda: real, offline=True)
    assert offline.detect_pii_entities(Text="123 secret", LanguageCode="en") == first
    with pytest.raises(RecordingMiss):
        offline.detect_pii_entities(Text="other", LanguageCode="en")


# --- fakes for the end-to-end harness test ------------------------------------------------------------------

def _vector(text: str, dims: int = 1024) -> list[float]:
    v = [0.0] * dims
    for w in tokenize(text):
        h = int(hashlib.sha256(w.encode()).hexdigest(), 16)
        v[h % dims] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class FakeTitanClient:
    def __init__(self) -> None:
        self.calls = 0

    def invoke_model(self, *, modelId: str, body: str, **_: Any) -> dict[str, Any]:  # noqa: N803
        self.calls += 1
        req = json.loads(body)
        payload = {"embedding": _vector(req["inputText"], req["dimensions"]), "inputTextTokenCount": 3}
        return {"body": io.BytesIO(json.dumps(payload).encode())}


class ScriptedConverse:
    """bedrock-runtime ``converse_stream``: turn 1 calls search_units, turn 2 answers citing what it got."""

    class meta:  # noqa: N801 - the botocore client attribute BedrockModel logs
        region_name = "us-east-1"

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def converse_stream(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        results = [c["toolResult"] for m in request["messages"] for c in m["content"] if "toolResult" in c]
        if not results:
            return {"stream": self._tool_use("t-1", "search_units", {"query": "W-2 Box 1 wages", "docType": "doc:W2"})}
        units = json.loads(results[-1]["content"][0]["text"])["units"]
        w2 = next(u for u in units if any(f["fieldId"] == "f-box1" for f in u["fields"]))
        answer = {"answer": "Box 1 wages are 98,400.00.", "total": "98,400.00", "currency": "USD", "arithmetic": None,
                  "claims": [{"text": "98,400.00", "value": "98400.00", "unitId": w2["unitId"], "fieldId": "f-box1"}],
                  "caveats": []}
        return {"stream": self._text(json.dumps(answer))}

    @staticmethod
    def _end(reason: str) -> list[dict[str, Any]]:
        return [{"messageStop": {"stopReason": reason}},
                {"metadata": {"usage": {"inputTokens": 1000, "outputTokens": 100, "totalTokens": 1100},
                              "metrics": {"latencyMs": 5}}}]

    def _tool_use(self, tid: str, name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
        return [{"messageStart": {"role": "assistant"}},
                {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tid, "name": name}}, "contentBlockIndex": 0}},
                {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}, "contentBlockIndex": 0}},
                {"contentBlockStop": {"contentBlockIndex": 0}}, *self._end("tool_use")]

    def _text(self, text: str) -> list[dict[str, Any]]:
        return [{"messageStart": {"role": "assistant"}},
                {"contentBlockDelta": {"delta": {"text": text}, "contentBlockIndex": 0}},
                {"contentBlockStop": {"contentBlockIndex": 0}}, *self._end("end_turn")]


class FakeSession:
    region_name = "us-east-1"

    def __init__(self, client: ScriptedConverse) -> None:
        self._client = client

    def client(self, *_: Any, **__: Any) -> ScriptedConverse:
        return self._client


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A seeded world (one W-2 evidence unit, one v1.0 chunk, one held unit) saved as ``{tmp}/world``."""
    for k, v in {"RP_ENV": "test", "RP_CONFIG_SOURCE": "local", "RP_REPO_ROOT": str(REPO), "RP_DATA_BUCKET": BUCKET,
                 "AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "testing",
                 "AWS_SECRET_ACCESS_KEY": "testing"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        w = seed_world(ddb, s3_client("us-east-1"), Settings.from_env())
        aoss: dict[str, dict[str, dict[str, Any]]] = {}
        for u in (w.w2, w.chunk):
            doc = IndexDocument.from_unit(u, embedding=[0.0] * 1024, config_sha="test",
                                          indexed_at="2026-11-11T00:00:00Z")
            aoss.setdefault(index_name(u.pipeline_version), {})[u.unit_id] = doc.dump()
        dump_world(tmp_path, s3=w.stores.s3, dynamodb=ddb, bucket=BUCKET, aoss_docs=aoss,
                   meta={"ownerSub": w.w2.allowed_principals[0].split(":", 1)[1], "groups": ["lending-analyst"]})
    (tmp_path / "graph").mkdir()
    (tmp_path / "graph" / "abox.nq").write_text("")
    return tmp_path


def test_snapshot_round_trip_keeps_items_and_bytes(snapshot: Path) -> None:
    with open_world(snapshot) as world:
        assert {i["packageId"] for i in world.table_items("packages")} == {"pkg-lending-01"}
        key = next(k for k in world.objects if k.endswith(".json"))
        body = world.s3.get_object(Bucket=world.bucket, Key=key)["Body"].read()
        assert hashlib.sha256(body).hexdigest() == world.objects[key]["sha256"]


def test_harness_end_to_end_with_scripted_bedrock(snapshot: Path, tmp_path: Path) -> None:
    import asyncio

    from evidence_core.config import load_pipeline
    from evidence_core.models.answer import AskEvent, AskRequest
    from local_agent.driver import LocalStrandsDriver, gateway_tool_specs
    from local_agent.gateway import Caller, LocalGateway
    from local_agent.harness import LocalRunStore, outcome_row, summarise
    from pydantic import TypeAdapter
    from rp_agent.runner import AskRunner, CallerIdentity

    from eval.run import outcome_from_events

    converse, titan_client = ScriptedConverse(), FakeTitanClient()
    out = tmp_path / "runs"
    gold = GoldenEntry(qid="q-test-01", type="table", question="What are the Box 1 wages?", answer="98,400.00",
                       answer_pattern=r"98,?400", package_id="pkg-lending-01", headline=True)
    with open_world(snapshot) as world:
        titan = RecordedTitan(Recording.open(tmp_path / "titan.json", "titan"), lambda: titan_client)
        gateway = LocalGateway(world=world, bedrock=titan, abox=snapshot / "graph" / "abox.nq").install()
        caller = Caller(sub=world.meta["ownerSub"], groups=world.meta["groups"])
        store = LocalRunStore(documents=world.table_items("documents"), out=out, provenance={"label": "test"})
        specs = gateway_tool_specs()
        runner = AskRunner(pipeline_loader=load_pipeline, run_store=store, golden=[gold], gateway_url="local://",
                           identity_mode="local-gateway",
                           driver_factory=lambda spec: LocalStrandsDriver(spec, gateway=gateway, caller=caller,
                                                                          boto_session=FakeSession(converse),
                                                                          tool_specs=specs))
        req = AskRequest(question=gold.question, package_id="pkg-lending-01", pipeline_version="2.3.1")
        try:
            raw = asyncio.run(_collect(runner.run(req, CallerIdentity(caller.sub, caller.groups, "Bearer t"))))
        finally:
            gateway.close()

    events = [TypeAdapter(AskEvent).validate_python(e) for e in raw]
    types = [e["type"] for e in raw]
    assert types[0] == "run_start" and types[-1] == "run", types
    # the model saw the Gateway's tool definitions, the lane's prompt and model id
    first = converse.requests[0]
    assert first["modelId"] == load_pipeline("2.3.1").config.generation.model_id
    assert {t["toolSpec"]["name"] for t in first["toolConfig"]["tools"]} == set(specs)
    assert first["system"][0]["text"] == load_pipeline("2.3.1").prompt_text
    # identity was injected by the local gateway, never by the model
    rec = store.written[0]
    assert [c.tool for c in rec.tool_calls] == ["search_units"]  # the field bbox came with the unit
    assert all(c.injected["via"] == "local-gateway" and "callerSub" not in c.input for c in rec.tool_calls)
    assert rec.answer is not None and rec.answer.claims[0].resolvable and rec.answer.claims[0].bbox is not None
    assert rec.check is not None and rec.check.match
    saved = json.loads((out / f"{rec.run_id}.json").read_text())
    assert saved["provenance"] == {"label": "test"} and saved["runId"] == rec.run_id
    # Titan embedded the index documents and the query
    assert titan_client.calls >= 3
    row = outcome_row(gold, "2.3.1", 0, events)
    assert row["answerCorrect"] and row["citationValid"] and row["costUsd"] > 0
    assert outcome_from_events(gold, "2.3.1", events).check_match is True
    assert summarise([row])[0]["correct"] == 1


async def _collect(agen: Any) -> list[dict[str, Any]]:
    return [e async for e in agen]


def test_representative_run_is_the_modal_total() -> None:
    from local_agent.harness import representative

    rows = [{"runId": "r-1", "total": "1.00"}, {"runId": "r-2", "total": "2.00"}, {"runId": "r-3", "total": "2.00"}]
    assert representative(rows)["runId"] == "r-2"


def test_unit_embedding_text_is_what_the_gateway_embeds() -> None:
    from evidence_core.testing import make_w2_unit

    u: Unit = make_w2_unit()
    assert u.embedding_text().endswith(u.text)


# --- lane parity -------------------------------------------------------------------------------------------

def _record(pv: str, **over: Any) -> dict[str, Any]:
    base = {"pipelineVersion": pv, "modelId": "m", "promptSha": "p", "toolsEnabled": ["a", "b"],
            "blockShas": {"preprocessing": f"pre-{pv}", "retrieval": "r", "generation": "g", "agent": "x"}}
    return base | over


def test_record_parity_allows_only_preprocessing_to_differ() -> None:
    from local_agent.harness import record_parity

    ok = record_parity([_record("1.0"), _record("1.0"), _record("2.3.1")])
    assert ok["ok"] and ok["differing"] == [] and "blockShas.preprocessing" not in ok["checked"]
    drift = record_parity([_record("1.0"), _record("2.3.1", promptSha="q")])
    assert not drift["ok"] and drift["differing"] == ["promptSha"]
    mixed = record_parity([_record("1.0"), _record("1.0", modelId="n"), _record("2.3.1")])
    assert not mixed["ok"] and mixed["mixedWithinLane"] == ["1.0:modelId"]


def test_record_parity_checks_the_tool_specs_and_says_when_they_are_unrecorded() -> None:
    from local_agent.harness import record_parity

    full = record_parity([_record("1.0", toolSpecSha="t1"), _record("2.3.1", toolSpecSha="t1")])
    assert full["ok"] and full["complete"] and "toolSpecSha" in full["checked"] and full["unverified"] == []
    schema = record_parity([_record("1.0", toolSpecSha="t1"), _record("2.3.1", toolSpecSha="t2")])
    assert not schema["ok"] and schema["differing"] == ["toolSpecSha"]
    batches = record_parity([_record("1.0", toolSpecSha="t1"), _record("1.0", toolSpecSha="t2"),
                             _record("2.3.1", toolSpecSha="t1")])
    assert not batches["ok"] and batches["mixedWithinLane"] == ["1.0:toolSpecSha"]
    old = record_parity([_record("1.0"), _record("2.3.1")])
    assert old["ok"] and not old["complete"] and old["unverified"] == ["toolSpecSha"]
    assert old["toolSpecUnrecorded"] == 2 and old["notes"]


def test_tool_spec_sha_covers_descriptions_and_schemas() -> None:
    from local_agent.harness import tool_spec_sha

    specs = {"graph_hop": {"name": "graph_hop", "description": "d", "inputSchema": {"json": {"enum": ["a"]}}}}
    wider = {"graph_hop": specs["graph_hop"] | {"inputSchema": {"json": {"enum": ["a", "b"]}}}}
    assert tool_spec_sha(specs, ["graph_hop"]) != tool_spec_sha(wider, ["graph_hop"])
    assert tool_spec_sha(specs, ["graph_hop", "other"]) == tool_spec_sha(specs, ["graph_hop"])


def test_config_parity_on_the_real_lane_configs() -> None:
    from local_agent.driver import gateway_tool_specs
    from local_agent.harness import LANES, config_parity

    parity = config_parity(LANES, tool_specs=gateway_tool_specs())
    assert parity["ok"], parity["differing"]
    assert {"modelId", "promptSha", "toolSpecSha", "blockShas.retrieval", "blockShas.generation"} <= set(
        parity["checked"])


def test_logged_runs_differ_only_in_preprocessing() -> None:
    from local_agent.harness import RUNS_DIR, record_parity

    records = [json.loads(p.read_text()) for p in sorted(RUNS_DIR.glob("r-*.json"))]
    if not records:
        pytest.skip("no logged runs")
    assert record_parity(records)["ok"]
