"""End-to-end on the lending sample, entirely local (no AWS): A's generated PDFs -> B's stage handlers (both
lanes, through `ready`) -> graph_write SPARQL UPDATE into pyoxigraph -> C's graph_hop tool with A's approved
templates and TBox -> the headline answer 169,650.00 with operands that trace back to units, fields, regions
and the Bronze object.

Needs `make data` samples, `make ontology-check` output and the optional local Docling install; skips otherwise.
One pipeline run (~20 s) is shared by every test in the module.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
LENDING = ROOT / "data" / "samples" / "lending"
ONTO = ROOT / "ontology" / "dist" / "1.2.0"
ACCOUNT_NUMBER = "40917364258107"  # fictitious, held by the PII gate; must never reach Gold/graph

pytest.importorskip("pyoxigraph")


def _have_docling() -> bool:
    from lambdas.ingest_lib.docling_client import LocalDoclingBackend

    return LocalDoclingBackend.available()


pytestmark = [
    pytest.mark.skipif(not any(LENDING.glob("*.pdf")), reason="run `make data`"),
    pytest.mark.skipif(not (ONTO / "nquads" / "rp.nq").exists(), reason="run `make ontology-check`"),
]


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not _have_docling():
        pytest.skip("local docling not installed (services/local_pipeline/requirements-docling.txt)")
    import os

    from local_pipeline.run import main

    out = tmp_path_factory.mktemp("e2e")
    saved = dict(os.environ)  # run.main sets RP_* for its in-process AWS stand-ins
    try:
        assert main(["--package", str(LENDING), "--out", str(out), "--until", "ready"]) == 0
    finally:
        os.environ.clear()
        os.environ.update(saved)
    return out


def _units(out: Path, pv: str, prefix: str) -> list[dict[str, Any]]:
    path = next((out / f"v{pv}").glob(f"{prefix}*.units.jsonl"))
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_every_lane_succeeds(run: Path) -> None:
    info = json.loads((run / "run.json").read_text())
    assert len(info["documents"]) == 5
    for d in info["documents"]:
        assert {pv: lane["status"] for pv, lane in d["lanes"].items()} == {"1.0": "SUCCEEDED", "2.3.1": "SUCCEEDED"}


@pytest.mark.parametrize(("who", "box1"), [("w2_2025_maya", "98,400.00"), ("w2_2025_arjun", "71,250.00")])
def test_v231_w2_box1_keeps_its_label_and_region(run: Path, who: str, box1: str) -> None:
    units = _units(run, "2.3.1", who.replace("_", "-"))
    fields = [f for u in units for f in u["fields"] if f["fieldId"] == "f-box1"]
    assert len(fields) == 1
    f = fields[0]
    assert f["value"] == box1 and f["concept"] == "fin:W2WagesBox1"
    assert "Wages, tips, other compensation" in f["label"]
    assert f["bbox"]["page"] == 1 and f["bbox"]["x1"] > f["bbox"]["x0"]
    unit = next(u for u in units if any(x["fieldId"] == "f-box1" for x in u["fields"]))
    assert unit["unitType"] == "field_group" and unit["bboxes"]


def test_v10_chunks_detach_labels(run: Path) -> None:
    diff = json.loads((run / "diff.json").read_text())
    for d in diff["documents"]:
        if not d["file"].startswith("w2_"):
            continue
        box1 = next(f for f in d["fields"] if f["fieldId"] == "f-box1")
        assert box1["valueInV1"] is True and box1["labelAttachedInV1"] is False
        chunk = _units(run, "1.0", d["docId"])[0]
        assert chunk["bboxes"] == [] and chunk["fields"] == []  # no region, no fields: nothing to cite


def test_graph_answers_headline_with_traceable_operands(run: Path) -> None:
    res = json.loads((run / "graph" / "qi_sum.json").read_text())["results"]
    v2 = res["2.3.1"]
    assert v2["total"] == "169650.00"
    assert Decimal(v2["total"]) == Decimal("98400.00") + Decimal("71250.00")
    ops = {o["label"]: o for o in v2["operands"]}
    assert set(ops) == {"Maya Fictus", "Arjun Fictus"}
    assert ops["Maya Fictus"]["value"] == "98400.00" and ops["Maya Fictus"]["fieldId"] == "f-box1"
    for o in ops.values():  # operand unit/field exist in the v2.3.1 units the pipeline wrote
        units = _units(run, "2.3.1", o["unitId"].split(":")[0])
        assert any(u["unitId"] == o["unitId"] and any(f["fieldId"] == o["fieldId"] for f in u["fields"])
                   for u in units)
    assert res["1.0"]["rowCount"] == 0 and res["1.0"]["note"].startswith("no ABox")


def test_held_pii_never_reaches_the_graph(run: Path) -> None:
    abox = (run / "graph" / "abox.nq").read_text()
    assert ACCOUNT_NUMBER not in abox
    assert "AgentsAndPeople/People/Person" in abox and "LegalPersons/BusinessEntity" in abox


def test_field_lookup_and_lineage_templates_on_pipeline_abox(run: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A's other approved templates, run by the real graph_hop over the ABox graph_write produced."""
    for k, v in {"RP_ENV": "test", "RP_CONFIG_SOURCE": "local", "RP_REPO_ROOT": str(ROOT),
                 "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "us-east-1",
                 "AWS_REGION": "us-east-1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    from local_pipeline.graph_check import OxiSparql, ask_graph, load_tbox
    from moto import mock_aws

    from tests.integration.support import seed_package

    client = OxiSparql()
    load_tbox(client, ROOT, "1.2.0")
    client.load_nquads((run / "graph" / "abox.nq").read_text())
    with mock_aws():
        seed_package("pkg-lending-01", "local-dev-owner-0000", ["lending-analyst"])
        look = ask_graph(client, package_id="pkg-lending-01", pipeline_version="2.3.1",
                         caller_sub="local-dev-owner-0000", groups=["lending-analyst"], template="field_lookup_v1",
                         params={"concept": "fin:W2WagesBox1", "borrower": "maya fictus"})
        assert look["rowCount"] == 1, look
        row = look["rows"][0]
        assert Decimal(row["amount"]) == Decimal("98400") and row["fileName"] == "w2_2025_maya_fictus.pdf"
        assert row["page"] == "1" and row["docType"].endswith("doc:W2")

        lineage = ask_graph(client, package_id="pkg-lending-01", pipeline_version="2.3.1",
                            caller_sub="local-dev-owner-0000", groups=["lending-analyst"],
                            template="unit_lineage_v1", params={"unitId": look["operands"][0]["unitId"]})
        assert lineage["rowCount"] >= 1, lineage
        lr = lineage["rows"][0]
        assert lr["sourceKey"].startswith("bronze/") and len(lr["sha256"]) == 64 and lr["stageId"] == "units"

        denied = ask_graph(client, package_id="pkg-lending-01", pipeline_version="2.3.1",
                           caller_sub="someone-else", groups=["markets-analyst"])
        assert denied["error"]["code"] == "forbidden"


def test_golden_headline_matches_pipeline_and_gold_region(run: Path) -> None:
    """A's golden set, C's golden check and the pipeline agree: q-lend-01's expected total equals the graph_hop
    total, and the v2.3.1 Box 1 field region (local Docling) covers A's gold box."""
    from evidence_core.bbox import BBox, coverage
    from evidence_core.models.golden import load_golden, match_golden, normalise_amount

    entries = load_golden(ROOT / "eval" / "golden.jsonl")
    assert len(entries) == 50
    entry = match_golden("What is the total income across this package?", entries, "pkg-lending-01")
    assert entry is not None and entry.qid == "q-lend-01" and entry.headline
    total = json.loads((run / "graph" / "qi_sum.json").read_text())["results"]["2.3.1"]["total"]
    assert normalise_amount(total) == normalise_amount(entry.answer)
    units = _units(run, "2.3.1", entry.doc_id)
    field = next(f for u in units for f in u["fields"] if f["fieldId"] == "f-box1")
    gold = entry.gold_bbox
    assert coverage(gold, BBox.load(field["bbox"])) >= 0.5  # RegionHit@1 rule
