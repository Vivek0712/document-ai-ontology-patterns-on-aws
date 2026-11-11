#!/usr/bin/env python3
"""Run every graph_hop SPARQL template on pyoxigraph over the built TBox + reference ABoxes.

    python ontology/check.py [--dist ontology/dist/<ver>] [--samples data/samples]

Fails (exit 1) unless, among other checks, qi_sum_w2_box1_v1 over the lending sample totals 169,650.00
with operands 98,400.00 and 71,250.00 traced to their units, fields and page regions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (HERE, REPO / "packages" / "evidence-core"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from rpont.build_lib import DIST_DIR, Manifest  # noqa: E402
from rpont.checks import run_all  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dist", type=Path, default=None, help="default: ontology/dist/<manifest version>")
    ap.add_argument("--samples", type=Path, default=REPO / "data" / "samples")
    args = ap.parse_args(argv)
    dist = args.dist or DIST_DIR / Manifest.load().ontology_version
    failures = 0
    for name, ok, detail in run_all(dist, args.samples, REPO):
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    print(f"{'all checks passed' if not failures else f'{failures} check(s) failed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
