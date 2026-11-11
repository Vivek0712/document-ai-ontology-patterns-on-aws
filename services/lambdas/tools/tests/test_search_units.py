from __future__ import annotations

import io
import json
from typing import Any

import pytest
from evidence_core.models.status import iso
from evidence_core.models.tools import SearchUnitsResult
from evidence_core.models.units import IndexDocument
from lambdas.tools._lib.aoss import AossClient
from lambdas.tools._lib.testing import USER_B_SUB, injected
from lambdas.tools.search_units import handler as mod


class FakeBedrock:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke_model(self, **kw: Any) -> dict[str, Any]:
        self.calls.append(kw)
        body = json.loads(kw["body"])
        return {"body": io.BytesIO(json.dumps({"embedding": [0.01] * body["dimensions"]}).encode())}


class FakeAoss:
    """Records request bodies; answers k-NN and BM25 with canned hits."""

    def __init__(self, dense: list[tuple[dict[str, Any], float]], bm25: list[tuple[dict[str, Any], float]],
                 missing: bool = False) -> None:
        self.dense, self.bm25, self.missing = dense, bm25, missing
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
        req = json.loads(body)
        self.requests.append((url, req))
        if self.missing:
            return 404, b'{"error":{"type":"index_not_found_exception"}}'
        hits = self.dense if "knn" in req["query"] else self.bm25
        return 200, json.dumps({"hits": {"hits": [{"_id": f"id-{i}", "_score": s, "_source": src}
                                                   for i, (src, s) in enumerate(hits)]}}).encode()


def _doc(unit: Any) -> dict[str, Any]:
    d = IndexDocument.from_unit(unit, embedding=[0.0], config_sha="c" * 64, indexed_at=iso()).dump()
    d.pop("embedding")
    return d


@pytest.fixture
def fakes(world):
    w2 = _doc(world.w2)
    other = w2 | {"unitId": world.w2.unit_id.replace("eu-01", "eu-03"), "label": "W-2 · employer block",
                  "fields": [], "bboxes": []}
    aoss = FakeAoss(dense=[(w2, 1.82), (other, 1.40)], bm25=[(other, 7.5), (w2, 3.0)])
    bedrock = FakeBedrock()
    mod.use_clients(AossClient(endpoint="https://x.aoss.amazonaws.com", sign=False, transport=aoss), bedrock)
    yield aoss, bedrock
    mod.use_clients(None, None)


def test_hybrid_fusion_filters_inside_both_subqueries(world, fakes):
    aoss, bedrock = fakes
    out = mod.handler({"query": "W-2 box 1 wages", **injected("2.3.1")})
    res = SearchUnitsResult.load(out)
    assert [u.unit_id for u in res.units] == [world.w2.unit_id, world.w2.unit_id.replace("eu-01", "eu-03")]
    top = res.units[0]
    # dense 1.0 (max), bm25 0.0 (min) -> 0.7 ; other: dense 0 bm25 1 -> 0.3
    assert top.scores.dense_norm == 1.0 and top.scores.bm25_norm == 0.0 and top.scores.fused == 0.7
    assert res.units[1].scores.fused == 0.3
    assert top.resolvable and top.fields[0].field_id == "f-box1" and top.fields[0].bbox.page == 1
    assert top.fields[0].value_number == "98400.00"
    assert res.query.index == "units-v2-3-1" and res.query.k == 5 and res.query.sub_query_k == 20
    assert res.query.weights == {"dense": 0.7, "bm25": 0.3}
    assert len(aoss.requests) == 2
    (u1, dense_body), (u2, bm25_body) = aoss.requests
    assert u1.endswith("/units-v2-3-1/_search") and u2.endswith("/units-v2-3-1/_search")
    dense_filter = dense_body["query"]["knn"]["embedding"]["filter"]
    bm25_filter = bm25_body["query"]["bool"]["filter"][0]
    assert dense_filter == bm25_filter == res.query.filter
    terms = dense_filter["bool"]["filter"]
    assert {"terms": {"allowedPrincipals": [f"user:{injected()['callerSub']}", "group:lending-analyst"]}} in terms
    assert {"term": {"packageId": "pkg-lending-01"}} in terms
    assert {"term": {"pipelineVersion": "2.3.1"}} in terms
    assert "post_filter" not in dense_body and "post_filter" not in bm25_body
    assert json.loads(bedrock.calls[0]["body"]) == {"inputText": "W-2 box 1 wages", "dimensions": 1024,
                                                     "normalize": True}
    assert bedrock.calls[0]["modelId"] == "amazon.titan-embed-text-v2:0"


def test_pipeline_version_selects_index_and_k(world, fakes):
    aoss, _ = fakes
    out = mod.handler({"query": "wages", "k": 1, "unitTypes": ["chunk"], **injected("1.0")})
    res = SearchUnitsResult.load(out)
    assert res.query.index == "units-v1-0" and len(res.units) == 1
    flt = aoss.requests[0][1]["query"]["knn"]["embedding"]["filter"]["bool"]["filter"]
    assert {"terms": {"unitType": ["chunk"]}} in flt and {"term": {"pipelineVersion": "1.0"}} in flt


def test_user_b_is_denied_before_any_query(world, fakes):
    aoss, bedrock = fakes
    out = mod.handler({"query": "wages", **injected(sub=USER_B_SUB, groups=["markets-analyst"])})
    assert out["error"]["code"] == "forbidden"
    assert aoss.requests == [] and bedrock.calls == []


def test_missing_injection_is_refused(world, fakes):
    out = mod.handler({"query": "wages", "packageId": "pkg-lending-01"})
    assert out["error"]["code"] == "forbidden"


def test_bad_arguments(world, fakes):
    assert mod.handler({"query": "", **injected()})["error"]["code"] == "bad_request"
    assert mod.handler({"query": "x", "k": 99, **injected()})["error"]["code"] == "bad_request"


def test_missing_index_is_a_readiness_error_not_an_empty_search(world):
    """The trace must show 'not ready', so the agent does not answer as if the package had no evidence."""
    mod.use_clients(AossClient(endpoint="https://x", sign=False, transport=FakeAoss([], [], missing=True)),
                    FakeBedrock())
    try:
        out = mod.handler({"query": "wages", **injected()})
        assert out["error"]["code"] == "not_ready" and "does not exist yet" in out["error"]["message"]
    finally:
        mod.use_clients(None, None)


def test_wrong_function_for_tool_name(world, fakes):
    class Ctx:
        class client_context:  # noqa: N801
            custom = {"bedrockAgentCoreToolName": "graph-hop___graph_hop"}

    assert mod.handler({"query": "x", **injected()}, Ctx())["error"]["code"] == "bad_request"
