"""CLI: ``python -m rp_ops <command>`` (PYTHONPATH=scripts/lib:packages/evidence-core, set by scripts/lib/common.sh)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evidence_core.env import Settings

from . import agentcore, auth, reset, runs, seed, smoke, verify, warm
from .aws import Aws, log


def _print_checks(title: str, checks: list[verify.Check]) -> int:
    print(f"\n{title}\n{verify.render(checks)}\n")
    failed = [c for c in checks if c.status == "FAIL"]
    print(f"  {len(checks) - len(failed)} ok, {len(failed)} failed")
    return 1 if failed else 0


def cmd_verify(aws: Aws, a: argparse.Namespace) -> int:
    return _print_checks("verify_account", verify.run_checks(aws, read_only=a.read_only))


def cmd_seed(aws: Aws, a: argparse.Namespace) -> int:
    bucket = aws.ssm("data_bucket")
    assert bucket
    subs: dict[str, str] = {}
    if not a.skip_users:
        log("users: Cognito groups and demo users")
        subs = seed.seed_users(aws, seed.ensure_demo_password(aws, a.password))
    log("pipeline versions: rp-pipeline-versions rows pinned to the deployed config objects")
    items = seed.seed_pipelines(aws, bucket)
    if not a.skip_ontology:
        versions = sorted({i.ontology_version for i in items if i.ontology_version})
        for ver in versions:
            log(f"ontology {ver}: TBox load into Neptune and vocab snapshot export")
            seed.load_ontology(aws, ver)
    owner = subs.get("user-a") or a.owner_sub
    if not owner:
        log("package: skipped (no owner sub; rerun without --skip-users or pass --owner-sub)")
        return 0
    for package_id, samples in seed_targets(a):
        pkg = seed.seed_package(aws, owner, package_id=package_id, samples_root=samples.parent)
        if a.upload:
            files = seed.sample_files(samples)
            log(f"upload {package_id}: {len(files)} PDF(s) to Bronze; each starts two rp-ingest executions "
                "(1.0 and 2.3.1)")
            seed.upload_documents(aws, bucket, pkg, files)
        else:
            log(f"upload {package_id}: skipped; sign in as user-a and upload from the UI (or rerun with --upload)")
    return 0


def seed_targets(a: argparse.Namespace) -> list[tuple[str, Path]]:
    """(package id, samples dir) to seed: every demo package with --all-packages, else --package-id (default
    pkg-lending-01). The samples dir is --samples when given, else data/samples/<key> of the demo package."""
    root = Path(a.samples_root)
    if a.all_packages:
        if a.samples:
            raise SystemExit("--samples names one directory; it cannot be combined with --all-packages")
        return [(s.package_id, root / s.key) for s in seed.DEMO_PACKAGES]
    if a.samples:
        return [(a.package_id, Path(a.samples))]
    spec = next((s for s in seed.DEMO_PACKAGES if s.package_id == a.package_id), None)
    return [(a.package_id, root / (spec.key if spec else "lending"))]


def cmd_prewarm(aws: Aws, a: argparse.Namespace) -> int:
    log("neptune: make sure the cluster is available")
    warm.neptune_set(aws, running=True)
    if not a.skip_docling:
        log("docling-serve: one warm Fargate task")
        print(json.dumps(warm.docling_scale(aws, 1)))
    log("provisioned concurrency on the ask-path tools and the region route")
    warm.set_provisioned_concurrency(aws, 1)
    log("warm queries: one search per index, one SPARQL template")
    warm.warm_search(aws, a.package_id, ["1.0", "2.3.1"])
    warm.warm_graph(aws, a.package_id)
    log("models: one call per configured model key")
    failed = 0
    for key, spec in warm.load_models().items():
        if key == "MODEL_FRONTIER" and not a.include_frontier:
            continue
        p = warm.ping_model(aws, key, spec)
        failed += not p.ok
        log(f"{'ok  ' if p.ok else 'FAIL'} {key} {p.model_id} {p.ms} ms {p.detail}")
    return 1 if failed else 0


def cmd_pause(aws: Aws, a: argparse.Namespace) -> int:
    warm.docling_scale(aws, 0, wait=False)
    warm.set_provisioned_concurrency(aws, 0)
    warm.neptune_set(aws, running=False, wait=a.wait)
    return 0


def cmd_resume(aws: Aws, a: argparse.Namespace) -> int:
    warm.neptune_set(aws, running=True, wait=a.wait)
    return 0


def cmd_reset(aws: Aws, a: argparse.Namespace) -> int:
    rep = reset.reset_package(aws, a.package_id, dry_run=a.dry_run, include_runs=a.include_runs,
                              drop_package=a.drop_package)
    print(json.dumps({"packageId": rep.package_id, "dryRun": rep.dry_run, "deleted": rep.deleted}, indent=2))
    return 0


def cmd_export_runs(aws: Aws, a: argparse.Namespace) -> int:
    print(json.dumps(runs.export_runs(aws, Path(a.out), package_id=a.package_id, with_records=not a.summaries_only)))
    return 0


def cmd_agentcore_delete(aws: Aws, a: argparse.Namespace) -> int:
    agentcore.delete_agentcore(aws, dry_run=a.dry_run)
    return 0


def cmd_empty_bucket(aws: Aws, a: argparse.Namespace) -> int:
    agentcore.empty_bucket(aws, a.bucket, dry_run=a.dry_run)
    return 0


def cmd_smoke(aws: Aws, a: argparse.Namespace) -> int:
    return _print_checks("smoke", smoke.run_smoke(aws, package_id=a.package_id, ask=a.ask))


def cmd_ssm(aws: Aws, a: argparse.Namespace) -> int:
    value = aws.ssm(a.name, required=not a.optional)
    if value is not None:
        print(value)
    return 0


def cmd_token(aws: Aws, a: argparse.Namespace) -> int:
    print(auth.sign_in(aws, a.user).access_token)
    return 0


def build_parser() -> argparse.ArgumentParser:
    repo = Settings.from_env().repo_root
    p = argparse.ArgumentParser(prog="rp_ops")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("verify", help="account, model, service and quota checks")
    s.add_argument("--read-only", action="store_true", help="skip model invocations and Textract/Comprehend calls")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("seed", help="users, pipeline versions, ontology, demo package")
    s.add_argument("--password", help="demo users' password (default: SSM /rp/demo/password or generated)")
    s.add_argument("--skip-users", action="store_true")
    s.add_argument("--skip-ontology", action="store_true")
    s.add_argument("--owner-sub", help="owner for the package when --skip-users")
    s.add_argument("--package-id", default=seed.DEMO_PACKAGE_ID,
                   help="one package (a demo id gets its groups and samples from seed.DEMO_PACKAGES)")
    s.add_argument("--all-packages", action="store_true",
                   help="every demo package: " + ", ".join(p.package_id for p in seed.DEMO_PACKAGES))
    s.add_argument("--upload", action="store_true", help="also put the sample PDFs into Bronze (starts ingestion)")
    s.add_argument("--samples", help="PDF directory for --package-id (default data/samples/<the package's key>)")
    s.add_argument("--samples-root", default=str(repo / "data" / "samples"))
    s.set_defaults(fn=cmd_seed)

    s = sub.add_parser("prewarm")
    s.add_argument("--package-id", default=seed.DEMO_PACKAGE_ID)
    s.add_argument("--skip-docling", action="store_true")
    s.add_argument("--include-frontier", action="store_true", help="also ping MODEL_FRONTIER")
    s.set_defaults(fn=cmd_prewarm)

    s = sub.add_parser("pause")
    s.add_argument("--wait", action="store_true", help="wait until Neptune reports stopped")
    s.set_defaults(fn=cmd_pause)

    s = sub.add_parser("resume")
    s.add_argument("--no-wait", dest="wait", action="store_false")
    s.set_defaults(fn=cmd_resume)

    s = sub.add_parser("reset")
    s.add_argument("package_id")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--include-runs", action="store_true", help="also delete run records (the logged runs)")
    s.add_argument("--drop-package", action="store_true", help="delete the package row instead of zeroing it")
    s.set_defaults(fn=cmd_reset)

    s = sub.add_parser("export-runs")
    s.add_argument("--out", default=str(repo / "runs"))
    s.add_argument("--package-id")
    s.add_argument("--summaries-only", action="store_true")
    s.set_defaults(fn=cmd_export_runs)

    s = sub.add_parser("agentcore-delete")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_agentcore_delete)

    s = sub.add_parser("empty-bucket")
    s.add_argument("bucket")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_empty_bucket)

    s = sub.add_parser("smoke")
    s.add_argument("--package-id", default=seed.DEMO_PACKAGE_ID)
    s.add_argument("--ask", action="store_true", help="also run the headline question on both versions")
    s.set_defaults(fn=cmd_smoke)

    s = sub.add_parser("ssm")
    s.add_argument("name", help="logical name (data_bucket, api_url, ...) or a full /rp/... parameter name")
    s.add_argument("--optional", action="store_true")
    s.set_defaults(fn=cmd_ssm)

    s = sub.add_parser("token", help="print an access token for a demo user (for curl)")
    s.add_argument("--user", default="user-a")
    s.set_defaults(fn=cmd_token)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.fn(Aws(), args))


if __name__ == "__main__":
    sys.exit(main())
