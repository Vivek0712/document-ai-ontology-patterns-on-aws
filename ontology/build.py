#!/usr/bin/env python3
"""Build the ontology bundle: TBox N-Quads per named graph, vocab.json, templates.

    python ontology/build.py [--dist ontology/dist]

Writes ontology/dist/{ontologyVersion}/ (deployed to s3://<data bucket>/config/ontology/{ontologyVersion}/).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent / "packages" / "evidence-core"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from rpont.build_lib import DIST_DIR, SRC_DIR, TEMPLATES_DIR, build  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", type=Path, default=SRC_DIR)
    ap.add_argument("--dist", type=Path, default=DIST_DIR)
    ap.add_argument("--templates", type=Path, default=TEMPLATES_DIR)
    args = ap.parse_args(argv)
    result = build(args.src, args.dist, args.templates)
    print(f"ontology {result.ontology_version} -> {result.out_dir}")
    for key, n in result.triples.items():
        print(f"  graph {key:<5} {n:>6} quads")
    vocab = result.vocab
    assert vocab is not None
    print(f"  vocab: {len(vocab.roles)} roles, {len(vocab.doc_types)} doc types, "
          f"{len(vocab.field_concepts)} field concepts")
    print(f"  templates: {sum(1 for f in result.files if f.startswith('templates/'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
