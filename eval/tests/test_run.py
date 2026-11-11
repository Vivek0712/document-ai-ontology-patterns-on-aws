from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_core.bbox import BBox
from evidence_core.models.answer import (
    AnswerEvent,
    CheckEvent,
    Claim,
    RunEvent,
    RunStartEvent,
    TextEvent,
    ToolEndEvent,
    ToolStartEvent,
    sse,
)
from evidence_core.models.golden import GoldenEntry
from evidence_core.models.tools import FieldBrief, RetrievedUnit, UnitScores
from evidence_core.testing import make_ctx, make_w2_unit
from rp_ops.api import parse_ask_events

from eval import run as harness

BOX1 = BBox(page=1, x0=50, y0=216, x1=306, y1=284)
HEADLINE = GoldenEntry(qid="q-lend-01", type="table", question="What is the total income across this package?",
                       answer="169,650.00", answer_pattern=r"169,?650\.00", package_id="pkg-lending-01",
                       page=1, gold_bbox=BOX1, headline=True)
BOX1_Q = GoldenEntry(qid="q-lend-02", type="table", question="Maya Fictus W-2 Box 1 wages 2025",
                     answer="98,400.00", answer_pattern=r"98,?400\.00", package_id="pkg-lending-01",
                     page=1, gold_bbox=BOX1)


def _retrieved() -> RetrievedUnit:
    u = make_w2_unit()
    return RetrievedUnit(
        rank=1, unit_id=u.unit_id, doc_id=u.doc_id, file_name=u.file_name, unit_type=u.unit_type, label=u.label,
        breadcrumb=u.breadcrumb_text(), text=u.text, pages=u.pages, bboxes=u.bboxes, resolvable=True,
        fields=[FieldBrief(field_id="f-box1", label="1 Wages", value="98,400.00", page=1, bbox=BOX1)],
        scores=UnitScores(dense_norm=1.0, bm25_norm=0.8, fused=0.94),
    )


def _stream(total: str, resolvable: bool) -> str:
    unit = _retrieved()
    claim = Claim(text="98,400.00", value="98400.00", unit_id=unit.unit_id, field_id="f-box1",
                  resolvable=resolvable, page=1 if resolvable else None, bbox=BOX1 if resolvable else None)
    events = [
        RunStartEvent(run_id="r-0123456789ab", package_id="pkg-lending-01", pipeline_version="2.3.1",
                      question=HEADLINE.question, model_id="m", config_sha="c" * 64, prompt_sha="p" * 64,
                      tools=["search_units"]),
        ToolStartEvent(call_id="t1", tool="search_units", input={"query": "income"}, t=0.1),
        ToolEndEvent(call_id="t1", tool="search_units", ms=300, status="ok", summary="1 unit",
                     units=[unit.unit_id], retrieved=[unit], t=0.4),
        TextEvent(delta="The total"),
        AnswerEvent(answer=f"The total is {total}.", total=total, claims=[claim]),
        CheckEvent(qid="q-lend-01", expected="169,650.00", actual=total, match=total == "169,650.00"),
        RunEvent(run_id="r-0123456789ab", package_id="pkg-lending-01", pipeline_version="2.3.1",
                 config_sha="c" * 64, block_shas={}, prompt_sha="p" * 64, model_id="m", corpus_hash="h" * 64,
                 latency_ms=4200, started_at="2026-11-03T00:00:00.000Z"),
    ]
    return "".join(sse(e) for e in events)


def test_outcome_from_sse_stream_pass() -> None:
    events = list(parse_ask_events(_stream("169,650.00", True).splitlines(keepends=True)))
    o = harness.outcome_from_events(HEADLINE, "2.3.1", events)
    assert o.recall_at1 is False  # the W-2 unit text does not contain the total
    assert o.region_hit_at1 is True
    assert o.answer_correct is True and o.check_match is True
    assert o.citation is not None and o.citation.valid
    assert o.run_id == "r-0123456789ab" and o.latency_ms == 4200 and o.error is None


def test_outcome_from_sse_stream_fail_unresolvable() -> None:
    events = list(parse_ask_events(_stream("180,000.00", False).splitlines(keepends=True)))
    o = harness.outcome_from_events(HEADLINE, "1.0", events)
    assert o.answer_correct is False and o.check_match is False
    assert o.citation is not None and not o.citation.valid


def test_outcome_without_answer_is_an_error() -> None:
    o = harness.outcome_from_events(HEADLINE, "2.3.1", [])
    assert o.error == "stream ended without an answer event" and o.top1_unit_id is None


def test_select_filters() -> None:
    entries = [HEADLINE, BOX1_Q]
    assert [e.qid for e in harness.select(entries, headline_only=True)] == ["q-lend-01"]
    assert [e.qid for e in harness.select(entries, qids=["q-lend-02"])] == ["q-lend-02"]
    assert harness.select(entries, types=["prose"]) == []
    assert len(harness.select(entries, limit=1)) == 1


def _write_units(tmp: Path) -> Path:
    units_dir = tmp / "units"
    units_dir.mkdir()
    eu = make_w2_unit(make_ctx("2.3.1"))
    chunk = make_w2_unit(make_ctx("1.0")).model_copy(update={
        "unit_id": eu.unit_id.replace(":2.3.1:eu-01", ":1.0:c-01"), "pipeline_version": "1.0", "bboxes": [],
        "fields": [], "text": "Wages 1 3 5 6,600.00 105,000.00 105,000.00 98,400.00 13,776.00", "chars": 63,
    })
    (units_dir / "v2.3.1.jsonl").write_text(eu.dump_json() + "\n", encoding="utf-8")
    (units_dir / "v1.0.jsonl").write_text(chunk.dump_json() + "\n" + '{"diff": "not a unit"}\n', encoding="utf-8")
    return units_dir


def test_local_mode_end_to_end_and_compare(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    golden = tmp_path / "golden.jsonl"
    golden.write_text(BOX1_Q.dump_json() + "\n", encoding="utf-8")
    units_dir = _write_units(tmp_path)
    out = tmp_path / "results"
    rc = harness.main(["run", "--mode", "local", "--golden", str(golden), "--units-dir", str(units_dir),
                       "--out", str(out)])
    assert rc == 0
    files = list(out.glob("e-*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["caption"].startswith("These numbers describe our synthetic documents")
    assert data["lanes"]["2.3.1"]["byType"]["table"]["recallAt1"]["rate"] == 1.0
    assert data["lanes"]["2.3.1"]["byType"]["table"]["regionHitAt1"]["rate"] == 1.0
    assert data["lanes"]["1.0"]["byType"]["table"]["regionHitAt1"]["n"] == 0
    assert data["lanes"]["1.0"]["meanUnitChars"] == 63.0
    assert data["lanes"]["2.3.1"]["configSha"]  # from the repo config
    assert "accuracy" not in json.dumps(data).lower()
    assert "Harness · local" in capsys.readouterr().out

    # candidate where 2.3.1 loses the hit -> compare exits 1
    worse = json.loads(files[0].read_text())
    worse["evalRunId"] = "e-worse"
    worse["lanes"]["2.3.1"]["byType"]["table"]["recallAt1"] = {"hits": 0, "n": 1, "rate": 0.0}
    worse_path = tmp_path / "worse.json"
    worse_path.write_text(json.dumps(worse))
    assert harness.main(["compare", str(files[0]), str(worse_path)]) == 1
    assert harness.main(["compare", str(files[0]), str(files[0])]) == 0


def test_main_defaults_to_run_subcommand(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="not found"):
        harness.main(["--golden", str(tmp_path / "missing.jsonl")])


def test_run_ask_passes_generation_profile_and_runtime_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """--generation-profile reaches AskRequest; --via runtime aims the client at SSM /rp/agent/runtime-url."""
    import rp_ops.api
    import rp_ops.auth
    import rp_ops.aws

    seen: dict[str, object] = {}

    class FakeAws:
        def ssm(self, name: str, required: bool = True) -> str:
            return {"api_url": "https://api.example/v1/", "agent_runtime_url": "https://runtime.example/inv"}[name]

    class FakeClient:
        def __init__(self, base_url: str, authorization: str, runtime_url: str | None = None) -> None:
            seen["runtime_url"] = runtime_url

        def ask(self, req):  # noqa: ANN001, ANN202
            seen.setdefault("profiles", []).append(req.generation_profile)  # type: ignore[union-attr]
            return iter(())

    monkeypatch.setattr(rp_ops.aws, "Aws", FakeAws)
    monkeypatch.setattr(rp_ops.api, "ApiClient", FakeClient)
    monkeypatch.setattr(rp_ops.auth, "sign_in", lambda aws, user: object())
    monkeypatch.setattr(rp_ops.auth, "bearer", lambda tokens: "Bearer x")
    outcomes, _meta, _corpus = harness.run_ask([HEADLINE], ["1.0"], package_id="pkg-lending-01", repeat=2,
                                               user="user-b", generation_profile="frontier", via="runtime")
    assert seen == {"runtime_url": "https://runtime.example/inv", "profiles": ["frontier", "frontier"]}
    assert len(outcomes) == 2 and all(o.error for o in outcomes)  # no answer event -> scored as an error
    harness.run_ask([HEADLINE], ["1.0"], package_id="pkg-lending-01", repeat=1, user="user-a")
    assert seen["runtime_url"] is None and seen["profiles"][-1] == "default"  # type: ignore[index]
    args = harness.build_parser().parse_args(["run", "--generation-profile", "frontier", "--via", "runtime"])
    assert (args.generation_profile, args.via) == ("frontier", "runtime")
