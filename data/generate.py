#!/usr/bin/env python3
"""Generate the fictitious document packages, their gold regions and the golden set.

    python data/generate.py --out data/samples [--golden eval/golden.jsonl] [--only lending]

Deterministic: fixed seeds and reportlab invariant mode, so a rerun produces byte-identical PDFs
(the corpus hash in each gold.json changes only when the data changes).

Outputs per package directory ``<out>/<key>/``:
    *.pdf          the documents (every page carries the fictitious footer)
    gold.json      package metadata + every region the generator drew (canonical frame:
                   PDF points, top-left origin, 1-based pages) + expected answers
    gold.jsonl     the same regions, one per line, with packageId/docId/fileName
and ``<out>/index.json`` (packages, doc shas, corpus hashes), plus the golden set at ``--golden``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (HERE, REPO / "packages" / "evidence-core"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from docgen.common import FOOTER_TEXT, GENERATOR_VERSION, GoldDoc  # noqa: E402
from docgen.packages import PackageSpec, all_packages  # noqa: E402
from evidence_core.hashing import corpus_hash  # noqa: E402
from evidence_core.ids import doc_id_for  # noqa: E402
from evidence_core.models.golden import GoldenEntry  # noqa: E402


def build_package(pkg: PackageSpec, out_root: Path) -> tuple[dict, list[GoldenEntry]]:
    out_dir = out_root / pkg.key
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*.pdf"):
        stale.unlink()
    docs: dict[str, GoldDoc] = {}
    for build in pkg.builders:
        doc = build(out_dir)
        doc.doc_id = doc_id_for(pkg.package_id, doc.file_name)
        docs[doc.file_name] = doc
    questions = pkg.questions(pkg, docs)
    shas = [docs[name].sha256 or "" for name in sorted(docs)]
    gold = {
        "packageId": pkg.package_id,
        "name": pkg.name,
        "description": pkg.description,
        "ownerEmail": pkg.owner_email,
        "allowedGroups": pkg.allowed_groups,
        "seed": pkg.seed,
        "generatorVersion": GENERATOR_VERSION,
        "footer": FOOTER_TEXT,
        "bboxFrame": "pdf-points-top-left",
        "corpusHash": corpus_hash(shas),
        "expected": pkg.expected,
        "questions": [q.qid for q in questions],
        "documents": [docs[name].to_dict() for name in sorted(docs)],
    }
    (out_dir / "gold.json").write_text(json.dumps(gold, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out_dir / "gold.jsonl").open("w", encoding="utf-8") as fh:
        for name in sorted(docs):
            doc = docs[name]
            for region in doc.regions:
                line = {"packageId": pkg.package_id, "docId": doc.doc_id, "fileName": doc.file_name,
                        "docType": doc.doc_type, **region.to_dict()}
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    summary = {
        "key": pkg.key,
        "packageId": pkg.package_id,
        "name": pkg.name,
        "corpusHash": gold["corpusHash"],
        "documents": [{"fileName": d.file_name, "docId": d.doc_id, "docType": d.doc_type, "route": d.route,
                       "pages": d.page_count, "sha256": d.sha256} for d in (docs[n] for n in sorted(docs))],
    }
    return summary, questions


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE / "samples")
    ap.add_argument("--golden", type=Path, default=REPO / "eval" / "golden.jsonl")
    ap.add_argument("--only", action="append", help="package key(s) to build (golden set is only written for all)")
    args = ap.parse_args(argv)

    packages = all_packages()
    if args.only:
        packages = [p for p in packages if p.key in set(args.only)]
        if not packages:
            ap.error(f"no package matches {args.only}")
    summaries, golden = [], []
    for pkg in packages:
        summary, questions = build_package(pkg, args.out)
        summaries.append(summary)
        # The agent's golden-set check matches by packageId or by this corpus hash, so a package created
        # in the UI with the same sample PDFs gets the check too.
        golden.extend(q.model_copy(update={"corpus_hash": summary["corpusHash"]}) for q in questions)
        print(f"{pkg.key:<11} {pkg.package_id:<17} {len(summary['documents'])} docs  "
              f"{len(questions):>2} questions  corpus {summary['corpusHash'][:12]}")

    if not args.only:
        index = {"generatorVersion": GENERATOR_VERSION, "packages": summaries}
        (args.out / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
        args.golden.parent.mkdir(parents=True, exist_ok=True)
        with args.golden.open("w", encoding="utf-8") as fh:
            for q in golden:
                fh.write(json.dumps(q.dump(), ensure_ascii=False) + "\n")
        by_type: dict[str, int] = {}
        for q in golden:
            by_type[q.type] = by_type.get(q.type, 0) + 1
        print(f"golden set: {len(golden)} questions {by_type} -> {args.golden}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
