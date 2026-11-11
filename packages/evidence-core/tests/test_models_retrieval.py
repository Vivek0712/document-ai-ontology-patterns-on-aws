from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_core import masking
from evidence_core.models.answer import AskEvent, Claim, ToolEndEvent, sse
from evidence_core.models.golden import GoldenEntry, match_golden, normalise_amount
from evidence_core.models.tools import INJECTED_FIELDS, split_arguments, tool_input_schema, tool_specs
from evidence_core.models.units import IndexDocument, Unit
from evidence_core.retrieval import Hit, bm25_query, build_filter, dense_query, fuse, index_mapping, min_max
from evidence_core.testing import make_w2_unit
from pydantic import TypeAdapter


def test_unit_roundtrip_and_index_document() -> None:
    u = make_w2_unit()
    data = json.loads(u.dump_json())
    assert data["unitId"].endswith(":2.3.1:eu-01") and data["fields"][0]["fieldId"] == "f-box1"
    assert Unit.load(data) == u
    assert u.resolvable and u.field("f-box1").value == "98,400.00"
    assert u.embedding_text().startswith("W-2 2025 › Maya Fictus")
    doc = IndexDocument.from_unit(u, embedding=[0.0] * 4, config_sha="c", indexed_at="2026-11-16T00:00:00.000Z")
    body = doc.dump()
    assert body["breadcrumb"] == "W-2 2025 › Maya Fictus › Wages (Boxes 1–6)"
    assert body["fieldIds"] == ["f-box1"] and "blockRefs" not in body["fields"][0]
    assert set(index_mapping()["mappings"]["properties"]) >= set(body)


def test_tool_schemas_are_gateway_safe() -> None:
    for spec in tool_specs():
        text = json.dumps(spec["inputSchema"])
        assert "$ref" not in text and "anyOf" not in text and '"title"' not in text
        assert spec["inputSchema"]["type"] == "object"
        assert not set(spec["inputSchema"]["properties"]) & set(INJECTED_FIELDS)
    s = tool_input_schema("search_units")
    assert s["required"] == ["query"] and s["properties"]["unitTypes"]["items"]["enum"]


def test_split_arguments_requires_injection() -> None:
    args, ctx = split_arguments({"query": "total income", "callerSub": "abc", "groups": ["lending-analyst"],
                                 "pipelineVersion": "2.3.1", "packageId": "pkg-lending-01"})
    assert args == {"query": "total income"} and ctx.pipeline_version == "2.3.1"
    with pytest.raises(PermissionError):
        split_arguments({"query": "x", "pipelineVersion": "2.3.1"})


def test_ask_events_parse_by_type() -> None:
    adapter = TypeAdapter(AskEvent)
    ev = adapter.validate_python({"type": "tool_end", "callId": "c1", "tool": "search_units", "ms": 870,
                                  "status": "ok", "summary": "5 units", "t": 0.9})
    assert isinstance(ev, ToolEndEvent)
    ans = adapter.validate_python({"type": "answer", "answer": "x", "claims": [
        {"text": "98,400.00", "unitId": "d:2.3.1:eu-01", "fieldId": "f-box1", "resolvable": True}]})
    assert isinstance(ans.claims[0], Claim)
    assert sse(ev).startswith('data: {"type":"tool_end"') and sse(ev).endswith("\n\n")


def test_golden_matching() -> None:
    e = GoldenEntry(qid="q-lend-01", type="table", question="What is the total income across this package?",
                    answer="169,650.00", answer_pattern=r"169,?650\.00", package_id="pkg-lending-01", headline=True)
    assert match_golden("what is the total income across this package", [e], "pkg-lending-01") is e
    assert match_golden("what is the total income across this package?", [e], "pkg-other") is None
    # A package created in the UI (new id) with the same sample PDFs matches by corpus hash.
    e2 = e.model_copy(update={"corpus_hash": "c" * 64})
    assert match_golden("What is the total income across this package?", [e2], "pkg-mine-1a2b", "c" * 64) is e2
    assert match_golden("What is the total income across this package?", [e2], "pkg-mine-1a2b", "d" * 64) is None
    assert normalise_amount("$169,650.00") == "169650.00"
    assert normalise_amount("n/a") is None


def test_min_max_and_fusion() -> None:
    assert min_max([2.0, 4.0, 3.0]) == [0.0, 1.0, 0.5]
    assert min_max([7.0]) == [1.0]
    dense = [Hit("a", 1.9, {}), Hit("b", 1.5, {}), Hit("c", 1.1, {})]
    bm25 = [Hit("b", 12.0, {}), Hit("d", 3.0, {})]
    fused = fuse(dense, bm25, k=5)
    by = {f.unit_id: f for f in fused}
    assert by["b"].fused == pytest.approx(0.7 * 0.5 + 0.3 * 1.0)
    assert by["d"].dense_norm == 0.0 and by["d"].fused == 0.0
    assert [f.unit_id for f in fused] == ["a", "b", "c", "d"]
    tie = fuse([Hit("z", 1.0, {}), Hit("y", 1.0, {})], [], k=2)
    assert [f.unit_id for f in tie] == ["y", "z"]


def test_filter_inside_both_subqueries() -> None:
    flt = build_filter(principals=["user:a", "group:g"], package_id="p", pipeline_version="2.3.1")
    d = dense_query([0.1, 0.2], flt, 20)
    b = bm25_query("total income", flt, 20)
    assert d["query"]["knn"]["embedding"]["filter"] == flt
    assert b["query"]["bool"]["filter"] == [flt]
    assert "post_filter" not in json.dumps(d) + json.dumps(b)
    with pytest.raises(ValueError):
        build_filter(principals=[], package_id="p", pipeline_version="1.0")


def test_masking() -> None:
    assert masking.mask_account_ids("acct 123456789012 ok") == "acct ************ ok"
    assert masking.mask_long_digits("Account 12345678901234") == "Account ••••••••••1234"
    assert masking.mask_long_digits("Box 1 98,400.00") == "Box 1 98,400.00"
    assert masking.short_execution("arn:aws:states:us-east-1:1:execution:rp-ingest:abcdef123") == "abcdef"


def test_generated_contracts_are_current(repo_root: Path) -> None:
    from evidence_core.export import build

    gen = repo_root / "contracts" / "generated"
    for rel, value in build(repo_root).items():
        path = gen / rel
        assert path.exists(), f"missing {path}; run `make contracts`"
        assert json.loads(path.read_text()) == json.loads(json.dumps(value)), f"{rel} is stale; run `make contracts`"
