"""``make xray-fixtures``: run the X-ray core over a local package and write UI mock fixtures.

Inputs are real local artifacts, nothing is typed by hand:
- the Bronze PDFs in ``--package`` (default ``data/samples/lending``);
- the v1.0 chunks and v2.3.1 evidence units written by the local pipeline (``--build``, default
  ``build/e2e``: ``make e2e-local``);
- the stage records in ``{build}/status`` (sha256, held unit ids);
- the run's own PII gate artifacts from the world snapshot (``{build}/world``): ``pii_decisions.json``
  (held unit ids) and ``held_regions.json`` (the boxes ``promote`` filled) per lane. The gate engine is
  whatever the run used (``run.json`` ``piiGate``: real Amazon Comprehend, recorded, or the regex stand-in).

The export checks those artifacts against the stage outputs (``pii_gate.heldUnitIds`` and
``promote.heldRegions`` per lane) and stops if they differ, so the fixtures can never disagree with
the run they replay.

``answerNumbers`` come from the representative logged naive run of the headline question
in ``--runs`` (``runs/local/index.json``, written by ``make local-ask``): the figures that run's answer
used, matched to the page's value lines exactly as ``rp-xray`` does for ``?runId=``. The run is named in
``sources.answerRun`` with its provenance (``local run · Bedrock``). Page images are
rendered with pypdfium2 at the lane's dpi (150) and at 2x (300 dpi) with the union of both lanes'
regions filled black, as the rp-xray Lambda does.

Writes into ``usecases/provenant/ui/mock-public/mock/xray/{packageId}/``
(served only in mock and replay modes), one folder per package
so every package's export is laid out the same way:
    index.json                                  documents, sizes, provenance of this export
    {docId}/p{NNN}-s{size}.json                 XrayPage for every page and every what-if size
    pages/{docId}-p{NNN}.png, …@2x.png          masked page renders

    PYTHONPATH=services:packages/evidence-core .venv/bin/python scripts/xray_fixtures.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "services"), str(REPO / "packages" / "evidence-core")]

from evidence_core import s3keys  # noqa: E402
from evidence_core.bbox import BBox  # noqa: E402
from evidence_core.config import load_pipeline  # noqa: E402
from evidence_core.enums import PiiFlag  # noqa: E402
from evidence_core.models.units import PiiDecisions  # noqa: E402
from lambdas.ingest_lib.pdf import pypdf_version  # noqa: E402
from lambdas.ingest_lib.render import pdfium_version, render_pages  # noqa: E402
from lambdas.xray.core import SCHEMA, XrayInput, build_xray, evidence_from_units, read_pdf  # noqa: E402
from lambdas.xray.handler import DEFAULT_WHATIF_SIZES, SPLITTER, answer_texts  # noqa: E402

NAIVE, EVIDENCE = "1.0", "2.3.1"


def jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class FixtureMismatch(RuntimeError):
    """The run's PII gate artifacts disagree with its stage records (or are missing)."""


def held_by_lane(build: Path, package_id: str, doc_id: str
                 ) -> dict[str, tuple[dict[int, list[BBox]], list[str]]]:
    """Per lane: held boxes per page and the held unit ids, read from the run's own gate artifacts."""
    world = build / "world" / "s3"
    out: dict[str, tuple[dict[int, list[BBox]], list[str]]] = {}
    for pv in (NAIVE, EVIDENCE):
        dkey = world / s3keys.pii_decisions_key(package_id, doc_id, pv)
        rkey = world / s3keys.held_regions_key(package_id, doc_id, pv)
        if not dkey.exists() or not rkey.exists():
            raise FixtureMismatch(f"{doc_id} lane {pv}: no PII gate artifacts in {build / 'world'}")
        decisions = PiiDecisions.load(json.loads(dkey.read_text())).decisions
        ids = [d.unit_id for d in decisions if d.decision is PiiFlag.HELD]
        pages = json.loads(rkey.read_text()).get("pages") or {}
        held = {int(p): [BBox.load(b) for b in boxes] for p, boxes in pages.items()}
        out[pv] = (held, ids)
    return out


def check_against_run(doc_id: str, status: dict[str, Any], lanes: dict[str, tuple[dict[int, list[BBox]], list[str]]]
                      ) -> None:
    """Fail loudly unless the recomputed held ids and region counts equal the run's recorded stage outputs."""
    def output(stage: str, pv: str) -> dict[str, Any]:
        rec = next((s for s in status["stages"] if s["stageId"] == stage and s["pipelineVersion"] == pv), None)
        if rec is None:
            raise FixtureMismatch(f"{doc_id}: no {stage} record for lane {pv} in the run status")
        return rec.get("output") or {}

    for pv, (held, ids) in lanes.items():
        recorded_ids = output("pii_gate", pv).get("heldUnitIds", [])
        recorded_regions = output("promote", pv).get("heldRegions", 0)
        regions = sum(len(v) for v in held.values())
        if sorted(ids) != sorted(recorded_ids):
            raise FixtureMismatch(f"{doc_id} lane {pv}: held units {ids} != recorded {recorded_ids}")
        if regions != recorded_regions:
            raise FixtureMismatch(f"{doc_id} lane {pv}: {regions} held regions != recorded {recorded_regions}")


LoggedAnswer = tuple[tuple[str, list[str]], dict[str, Any]]


def headline_qid(package_id: str) -> str | None:
    """The package's headline golden question (``headline: true``), else its first question, else None (a new
    package with no golden questions yet: the X-ray is exported without answerNumbers)."""
    rows = [json.loads(line) for line in (REPO / "eval" / "golden.jsonl").read_text().splitlines() if line.strip()]
    mine = [r for r in rows if r.get("packageId") == package_id]
    return next((r["qid"] for r in mine if r.get("headline")), mine[0]["qid"] if mine else None)


def logged_answer(runs: Path, package_id: str, qid: str = "q-lend-01") -> LoggedAnswer | None:
    """(runId, answer texts) of the representative logged 1.0 run of ``qid``, and its ``sources.answerRun``."""
    index_path = runs / "index.json"
    if not index_path.exists():
        return None
    index = json.loads(index_path.read_text())
    row = next((s for s in index.get("summary", []) if s["qid"] == qid and s["pipelineVersion"] == NAIVE), None)
    if not row or not row.get("representativeRunId"):
        return None
    run = json.loads((runs / f"{row['representativeRunId']}.json").read_text())
    if run.get("packageId") != package_id or run.get("pipelineVersion") != NAIVE:
        raise FixtureMismatch(f"{row['representativeRunId']} is not a 1.0 run of {package_id}")
    source = {"runId": run["runId"], "qid": qid, "provenance": "local-run", "label": index.get("provenance"),
              "correct": row["correct"], "n": row["n"], "file": str((runs / f"{run['runId']}.json").relative_to(REPO))}
    return (run["runId"], answer_texts(run)), source


def export_doc(doc: dict[str, Any], package: Path, build: Path, out: Path, control: int, overlap_pct: int,
               dpi: int, exported_at: str, answer: LoggedAnswer | None = None
               ) -> dict[str, Any]:
    doc_id, file_name = doc["docId"], doc["fileName"]
    pdf = (package / file_name).read_bytes()
    naive = jsonl(build / "v1.0" / f"{doc_id}.units.jsonl")
    evidence_raw = jsonl(build / "v2.3.1" / f"{doc_id}.units.jsonl")
    status = json.loads((build / "status" / f"{doc_id}.json").read_text())
    lanes = held_by_lane(build, doc["packageId"], doc_id)
    check_against_run(doc_id, status, lanes)
    held_naive_ids, held_evidence_ids = lanes[NAIVE][1], lanes[EVIDENCE][1]
    held_ids = [*held_naive_ids, *held_evidence_ids]
    held: dict[int, list[BBox]] = {}  # the union of both lanes, per page
    for lane_held, _ in lanes.values():
        for page_no, boxes in lane_held.items():
            held.setdefault(page_no, []).extend(boxes)
    # the evidence layer reads Gold: held units never left Silver
    evidence = evidence_from_units(u for u in evidence_raw if u["unitId"] not in held_evidence_ids)
    register = next(s for s in status["stages"] if s["stageId"] == "register" and s["pipelineVersion"] == NAIVE)
    route = next((s for s in status["stages"] if s["stageId"] == "route" and s["pipelineVersion"] == EVIDENCE), {})
    sha = register["output"]["sha256"]
    pages = read_pdf(pdf)
    build_rel = str(build.relative_to(REPO))

    (out / doc_id).mkdir(parents=True, exist_ok=True)
    (out / "pages").mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, str]] = {}
    for p in pages:
        boxes = [(b.x0, b.y0, b.x1, b.y1) for b in held.get(p.page, [])]
        for size in DEFAULT_WHATIF_SIZES:
            payload = build_xray(XrayInput(
                doc_id=doc_id, page=p.page, pdf_pages=pages, chunk_texts=[u["text"] for u in naive],
                chunk_ids=[u["unitId"] for u in naive], held_chunk_ids=held_naive_ids,
                size_tokens=size, control_size=control, overlap_pct=overlap_pct, evidence=evidence, held=boxes,
                what_if_sizes=DEFAULT_WHATIF_SIZES,
                computed_at=exported_at,
                answer=answer[0] if answer else None,
                image={"url": f"pages/{doc_id}-p{p.page:03d}.png", "zoomUrl": f"pages/{doc_id}-p{p.page:03d}@2x.png",
                       "dpi": dpi},
                sources={
                    "bronze": {"key": register["inputs"][0]["key"], "versionId": register["inputs"][0].get("versionId"),
                               "sha256": sha},
                    "units": {"key": f"{build_rel}/v1.0/{doc_id}.units.jsonl", "versionId": None},
                    "evidence": {"key": f"{build_rel}/v2.3.1/{doc_id}.units.jsonl"},
                    "pdfium": pdfium_version(), "splitter": SPLITTER, "textLayer": pypdf_version(),
                    "codeSha": "local", "packageId": doc["packageId"], "fileName": file_name,
                    "pageImage": "Bronze rendered with both lanes' held regions filled",
                    "heldUnitIds": held_ids, "mode": "local-run", "build": build_rel, "exportedAt": exported_at,
                } | ({"answerRun": answer[1]} if answer else {}),
            ))
            name = f"{doc_id}/p{p.page:03d}-s{size}.json"
            (out / name).write_text(json.dumps(payload, separators=(",", ":")))
            files.setdefault(str(p.page), {})[str(size)] = name
    for scale, suffix in ((1, ""), (2, "@2x")):
        for r in render_pages(pdf, dpi=dpi * scale, held=held):
            (out / "pages" / f"{doc_id}-p{r.page:03d}{suffix}.png").write_bytes(r.png)
    return {
        "docId": doc_id, "fileName": file_name, "docType": doc.get("docType"), "title": doc.get("title"),
        "route": (route.get("output") or {}).get("route") or doc.get("route"), "pageCount": len(pages),
        "pageWidth": pages[0].width, "pageHeight": pages[0].height, "sha256": sha,
        "chunks": len(naive), "evidenceUnits": len(evidence_raw), "heldUnitIds": held_ids,
        "heldRegions": {str(k): [b.rounded().model_dump() for b in v] for k, v in held.items()},
        "files": files,
        "images": {str(p.page): {"1x": f"pages/{doc_id}-p{p.page:03d}.png",
                                 "2x": f"pages/{doc_id}-p{p.page:03d}@2x.png"} for p in pages},
        "imageDpi": dpi,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--package", default="data/samples/lending")
    ap.add_argument("--build", default="build/e2e")
    ap.add_argument("--out", help="output folder (default: usecases/provenant/ui/mock-public/mock/xray/<packageId>)")
    ap.add_argument("--runs", default="runs/local", help="logged local runs (answerNumbers from the naive one)")
    ap.add_argument("--qid", help="golden question whose representative naive run gives answerNumbers "
                                  "(default: the package's headline question, else its first)")
    args = ap.parse_args()
    package, build = REPO / args.package, REPO / args.build
    if not (build / "v1.0").is_dir() or not (build / "world").is_dir():
        print(f"no local pipeline output (or world snapshot) in {build}: run `make e2e-local` first", file=sys.stderr)
        return 2
    gold = json.loads((package / "gold.json").read_text())
    out = REPO / (args.out or f"usecases/provenant/ui/mock-public/mock/xray/{gold['packageId']}")
    naive_cfg = load_pipeline(NAIVE).config.preprocessing
    control, overlap_pct = naive_cfg.chunker.size_tokens or 300, naive_cfg.chunker.overlap_pct or 0
    dpi = load_pipeline(EVIDENCE).config.preprocessing.render.dpi
    out.mkdir(parents=True, exist_ok=True)
    exported_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        qid = args.qid or headline_qid(gold["packageId"])
        answer = logged_answer(REPO / args.runs, gold["packageId"], qid) if qid else None
    except FixtureMismatch as e:
        print(f"refusing to export: {e}", file=sys.stderr)
        return 3
    print(f"answerNumbers from {answer[1]['runId']} ({answer[1]['label']})" if answer
          else "no logged naive run in --runs: answerNumbers left out (the positional hypothesis stays untested)")
    run = json.loads((build / "run.json").read_text()) if (build / "run.json").exists() else {}
    lane_status = {e.get("docId"): {pv: (lane or {}).get("status") for pv, lane in (e.get("lanes") or {}).items()}
                   for e in run.get("documents", [])}
    docs, skipped = [], []
    for d in gold["documents"]:
        failed = sorted(pv for pv, s in lane_status.get(d["docId"], {}).items() if s == "FAILED")
        if failed:  # no PII gate ran on that lane, so its pages cannot be masked: leave the whole document out
            skipped.append(d["docId"])
            print(f"{d['docId']}: skipped (lane {', '.join(failed)} FAILED in the run; for scanned pages install "
                  "Docling, Quickstart 1 step 3, and re-run `make e2e-local`)")
            continue
        try:
            entry = export_doc({**d, "packageId": gold["packageId"]}, package, build, out, control, overlap_pct, dpi,
                               exported_at, answer)
        except FixtureMismatch as e:
            print(f"refusing to export: {e}. Re-run `make e2e-local` so the fixtures replay the current gate.",
                  file=sys.stderr)
            return 3
        docs.append(entry)
        print(f"{entry['docId']}: {entry['pageCount']} page(s) · {entry['chunks']} chunk(s) · "
              f"{len(entry['heldUnitIds'])} held unit(s)")
    index = {
        "schema": SCHEMA,
        "generatedBy": "scripts/xray_fixtures.py",
        "generatedAt": exported_at,
        "provenance": "local-run",
        "packageId": gold["packageId"],
        "corpusHash": gold.get("corpusHash"),
        "seed": gold.get("seed"),
        "generatorVersion": gold.get("generatorVersion"),
        "build": str(build.relative_to(REPO)),
        "engines": {"textLayer": pypdf_version(), "charBoxes": pdfium_version(),
                    "evidenceUnits": run.get("docling"), "aws": run.get("aws"),
                    "pii": run.get("piiGate") or "regex stand-in for Amazon Comprehend"},
        "control": {"sizeTokens": control, "overlapPct": overlap_pct},
        "whatIfSizes": list(DEFAULT_WHATIF_SIZES),
        "documents": docs,
    }
    (out / "index.json").write_text(json.dumps(index, indent=1))
    print(f"wrote {out.relative_to(REPO)}/index.json · {len(docs)} documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
