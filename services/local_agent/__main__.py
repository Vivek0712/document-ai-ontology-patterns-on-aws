"""``make local-ask``: golden questions asked of each lane's local outputs with the v1 agent on Bedrock.

    PYTHONPATH=services:packages/evidence-core .venv/bin/python -m local_agent ask --qid q-lend-01 --repeat 10
    PYTHONPATH=services:packages/evidence-core .venv/bin/python -m local_agent ask --package pkg-lending-01 --repeat 3
    PYTHONPATH=services:packages/evidence-core .venv/bin/python -m local_agent summary

Runs append to ``runs/local/`` (one RunRecord per run, its event stream, and ``index.json``). Every run
is a real Bedrock Converse call: ``--budget-usd`` stops the batch before its own spend would pass it (the
index keeps the running total). ``--build`` must hold the questions' package (one local build per package).
"""

from __future__ import annotations

import argparse
import os
import sys

from evidence_core.models.golden import load_golden

from .harness import LANES, REPO, RUNS_DIR, Plan, load_index, run, summarise, table, write_parity, write_summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="local_agent", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask", help="ask golden questions (appends to runs/local)")
    a.add_argument("--qid", action="append", default=[], help="golden question id (repeatable)")
    a.add_argument("--package", help="every golden question of this package (e.g. pkg-lending-01)")
    a.add_argument("--exclude", action="append", default=[], help="qid to leave out (repeatable)")
    a.add_argument("--repeat", type=int, default=1, help="runs per question and lane")
    a.add_argument("--lanes", default=",".join(LANES))
    a.add_argument("--build", default="build/e2e", help="local pipeline output with a world snapshot")
    a.add_argument("--out", default=str(RUNS_DIR.relative_to(REPO)))
    a.add_argument("--budget-usd", type=float, default=0.5,
                   help="stop this batch before its model spend (list price) would pass this")
    a.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    a.add_argument("--generation-profile", choices=("default", "frontier", "compact"), default="default",
                   help="swap the answer model for these runs only (frontier = MODEL_FRONTIER, compact = "
                        "MODEL_COMPACT); the lane config is unchanged")
    a.add_argument("--as", dest="caller", choices=("a", "b"), default="a",
                   help="ask as User A (package owner) or User B (markets-analyst; invokes the agent directly)")
    s = sub.add_parser("summary", help="recompute the N-of-N table and lane parity of runs/local (no model calls)")
    s.add_argument("--out", default=str(RUNS_DIR.relative_to(REPO)))
    args = ap.parse_args(argv)

    out = REPO / args.out
    if args.cmd == "summary":
        rows, meta = load_index(out)
        print(table(write_summary(out)))
        parity = write_parity(out)
        for ax in parity.get("modelAxis", []):
            print(f"model axis {ax['lane']} {ax['profile']}: {'ok' if ax['ok'] else 'FAILED'} "
                  f"(differs: {', '.join(ax['differing']) or 'nothing'}; "
                  f"{' → '.join([*ax['defaultModelIds'], *ax['modelIds']])}"
                  f"{'; unverified: ' + ', '.join(ax['unverified']) if ax['unverified'] else ''})")
        print(f"\nlane parity over {parity['runs']} RunRecords: {'ok' if parity['ok'] else 'FAILED'} "
              f"(identical: {', '.join(k for k in parity['checked'] if k not in parity['differing'])}; "
              f"differing: {', '.join(parity['differing'] + parity['mixedWithinLane']) or 'none'})")
        if parity["unverified"]:
            print(f"  not verifiable: {', '.join(parity['unverified'])} is unrecorded on "
                  f"{parity['toolSpecUnrecorded']} of {parity['runs']} runs")
            for note in parity["notes"]:
                print(f"  {note}")
        print(f"\n{len(rows)} runs · spent ${meta.get('spentUsd', 0):.2f} (model list price)")
        return 0

    golden = load_golden(REPO / "eval" / "golden.jsonl")
    entries = [g for g in golden if (g.qid in args.qid) or (args.package and g.package_id == args.package)]
    entries = [g for g in entries if g.qid not in args.exclude]
    if not entries:
        print("no golden questions selected (use --qid or --package)", file=sys.stderr)
        return 2
    build = REPO / args.build
    plan = Plan(entries=entries, lanes=[x.strip() for x in args.lanes.split(",") if x.strip()],
                repeat_for=lambda _g: args.repeat, generation_profile=args.generation_profile, caller=args.caller)
    rows = run(plan, build=build, out=out, profile=args.profile, budget_usd=args.budget_usd)
    print("\n" + table(summarise(rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
