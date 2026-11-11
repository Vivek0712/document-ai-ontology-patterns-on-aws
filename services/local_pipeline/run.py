"""``make units``: run the ingestion stage code locally on a folder of PDFs, for both pipeline
versions, up to and including the ``units`` stage, and write:

    {out}/v1.0/{docId}.units.jsonl        fixed chunks (the naive lane)
    {out}/v2.3.1/{docId}.units.jsonl      evidence units
    {out}/v2.3.1/{docId}.build_report.json
    {out}/status/{docId}.json             every stage record + events, as the stage inspector shows them
    {out}/diff.md, {out}/diff.json        per document: chunks vs units, fields, label-value adjacency
    {out}/run.json                        engines and versions actually used (labels any stand-in)
    {out}/graph/abox.nq, qi_sum.json      (--until ready) the ABox graph_write wrote, and the real graph_hop
                                          tool answering the headline question over it (pyoxigraph, NOT Neptune)
    {out}/world/                          (--until ready) the whole in-process AWS world at the end of the run
                                          (S3 objects with version ids, DynamoDB items, index documents); see
                                          ``local_pipeline.world``

AWS is emulated in-process: moto for S3 and DynamoDB (the handlers are unchanged). Engines:
Docling = the local ``docling`` package when installed, else a clearly labelled pypdfium2 text-run
fallback; scanned pages = local Docling OCR shaped as a Textract response (labelled, never claimed
as Textract); Phase B embeddings = a deterministic local hash embedder unless ``--bedrock``
(real Titan V2 through your AWS profile, responses recorded in ``runs/local/recorded/titan-v2.json``,
for Phase B and the index alike); the PII gate = a regex stand-in for Comprehend unless
``--pii aws`` (real ``DetectPiiEntities`` through your AWS profile, responses recorded in
``runs/local/recorded/comprehend.json`` so a rebuild replays them offline). No AWS resource is created.

    PYTHONPATH=services python -m local_pipeline.run --package data/samples/lending --out build/units
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

PV_NAIVE, PV_EU = "1.0", "2.3.1"
REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_VOCAB = Path(__file__).parent / "fixtures" / "vocab_fixture_1.2.0.json"
BUCKET = "rp-data-local"
OWNER = "local-dev-owner-0000"


def _rel(path: Path) -> str:
    """A path under the repo as repo-relative (no home directory in run.json); any other path as given."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path)


def _vocab_path(arg: str | None, ontology_version: str) -> tuple[Path, str]:
    if arg:
        return Path(arg), "explicit"
    built = REPO_ROOT / "ontology" / "dist" / ontology_version / "vocab.json"
    if built.exists():
        return built, "ontology/dist (built by ontology/build.py)"
    return FIXTURE_VOCAB, "FALLBACK test fixture (ontology/dist missing: run make ontology-check)"


DEFAULT_PACKAGE_ID = "pkg-lending-01"
DEFAULT_GROUPS = ["lending-analyst"]
CALLER_GROUPS = ["lending-analyst"]  # User A's groups: the local caller of graph checks and the ask harness


def _package_meta(pkg_dir: Path) -> dict[str, Any]:
    """Package id, name, description and allowed groups from the generator's ``gold.json`` when the folder has
    one (``make data`` writes it), else the lending defaults."""
    gold = pkg_dir / "gold.json"
    meta = json.loads(gold.read_text()) if gold.exists() else {}
    return {"packageId": meta.get("packageId") or DEFAULT_PACKAGE_ID, "name": meta.get("name") or "local",
            "description": meta.get("description"), "groups": list(meta.get("allowedGroups") or DEFAULT_GROUPS)}


def _setup_aws(package_id: str, name: str = "local", description: str | None = None,
               groups: list[str] | None = None) -> Any:
    import boto3
    from evidence_core.ddb import create_tables_for_tests, to_ddb
    from evidence_core.models.api import Package
    from evidence_core.models.status import iso

    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    create_tables_for_tests(ddb)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
    pkg = Package(package_id=package_id, name=name, description=description, owner_sub=OWNER, created_at=iso(),
                  allowed_principals=[f"user:{OWNER}", *(f"group:{g}" for g in groups or DEFAULT_GROUPS)])
    ddb.Table("rp-packages").put_item(Item=to_ddb(pkg.dump()))
    return s3


def _ddb() -> Any:
    import boto3

    return boto3.resource("dynamodb", region_name="us-east-1")


def _records(table: Any, doc_id: str) -> dict[str, Any]:
    from boto3.dynamodb.conditions import Key
    from evidence_core.ddb import from_ddb

    items = [from_ddb(i) for i in table.query(KeyConditionExpression=Key("pk").eq(doc_id))["Items"]]
    return {
        "stages": [i for i in items if i["recordType"] == "STAGE"],
        "lanes": [i for i in items if i["recordType"] == "LANE"],
        "events": sorted((i for i in items if i["recordType"] == "EVENT"), key=lambda e: e["sk"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--package", default="data/samples/lending", help="folder of PDFs")
    ap.add_argument("--out", default="build/units")
    ap.add_argument("--package-id", help="default: packageId from the folder's gold.json, else pkg-lending-01")
    ap.add_argument("--vocab", help="vocab.json (default: ontology/dist/<ver>/vocab.json, else the test fixture)")
    ap.add_argument("--synthetic", action="store_true", help="generate small synthetic PDFs when --package is missing")
    ap.add_argument("--docling", choices=["auto", "local", "fallback"], default="auto")
    ap.add_argument("--bedrock", action="store_true",
                    help="Titan V2 embeddings (real, recorded; offline when recorded) for Phase B and the index")
    ap.add_argument("--until", default="units", choices=["units", "ready"],
                    help="ready = also run pii_gate..ready with local stand-ins (no AWS)")
    ap.add_argument("--pii", choices=["local", "aws"], default="local",
                    help="aws = real Amazon Comprehend DetectPiiEntities (recorded; replayed offline when recorded)")
    ap.add_argument("--no-world", action="store_true", help="do not write the {out}/world snapshot")
    args = ap.parse_args(argv)

    out = Path(args.out)
    pkg_dir = Path(args.package)
    source_label = str(pkg_dir)
    if not pkg_dir.exists() or not any(pkg_dir.glob("*.pdf")):
        if not args.synthetic:
            print(f"no PDFs in {pkg_dir}; run `make data` or pass --synthetic", file=sys.stderr)
            return 2
        from .synthetic import write_package

        pkg_dir = out / "inputs"
        write_package(pkg_dir)
        source_label = f"SYNTHETIC test PDFs ({pkg_dir}), not the data/ corpus"
    pdfs = sorted(pkg_dir.glob("*.pdf"))

    from evidence_core.config import load_pipeline

    ontology_version = load_pipeline(PV_EU).config.ontology_version or "1.2.0"
    vocab_path, vocab_label = _vocab_path(args.vocab, ontology_version)
    profile = os.environ.get("AWS_PROFILE")
    os.environ.pop("AWS_PROFILE", None)  # moto never sees real credentials; real calls name ``profile`` themselves
    os.environ.update({"RP_ENV": "local", "RP_CONFIG_SOURCE": "local", "RP_REPO_ROOT": str(REPO_ROOT),
                       "RP_DATA_BUCKET": BUCKET, "RP_HEARTBEAT_SECONDS": "0", "RP_VOCAB_PATH": str(vocab_path),
                       "RP_CODE_SHA": "local", "AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1"})
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "local")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "local")

    from evidence_core.ids import doc_id_for
    from lambdas.ingest_lib.aws import Services, use_services
    from lambdas.ingest_lib.docling_client import LocalDoclingBackend, TextRunLayoutBackend
    from lambdas.ingest_lib.embedder import HashEmbedder
    from lambdas.ingest_lib.testing import FakeAoss, FakeComprehend, FakeSfn, FakeSparql
    from moto import mock_aws

    from .diff import write_diff
    from .local_textract import LocalOcrTextract, TextractUnavailable
    from .runner import LocalIngest

    have_docling = LocalDoclingBackend.available()
    use_local = args.docling == "local" or (args.docling == "auto" and have_docling)
    if args.docling == "local" and not have_docling:
        print("docling is not installed (pip install -r services/local_pipeline/requirements-docling.txt)",
              file=sys.stderr)
        return 2
    docling = LocalDoclingBackend() if use_local else TextRunLayoutBackend()
    textract = LocalOcrTextract() if have_docling else TextractUnavailable()

    from .recorded import RECORDED_DIR, RecordedComprehend, RecordedTitan, Recording, aws_client_factory

    titan_rec = None
    bedrock_client = None
    if args.bedrock:
        titan_rec = Recording.open(RECORDED_DIR / "titan-v2.json",
                                   "bedrock-runtime:InvokeModel amazon.titan-embed-text-v2:0")
        bedrock_client = RecordedTitan(titan_rec, aws_client_factory("bedrock-runtime", profile))

    from .graph_check import NOT_NEPTUNE, OxiSparql, load_tbox, write_graph_check

    sparql: Any = FakeSparql()
    tbox_files: list[str] = []
    if args.until == "ready" and OxiSparql.available():
        sparql = OxiSparql()
        tbox_files = load_tbox(sparql, REPO_ROOT, ontology_version)

    comprehend: Any = FakeComprehend()
    pii_label = "regex stand-in for Amazon Comprehend (ingest_lib.testing.FakeComprehend)"
    recording = None
    if args.pii == "aws":
        recording = Recording.open(RECORDED_DIR / "comprehend.json", "comprehend:DetectPiiEntities")
        comprehend = RecordedComprehend(recording, aws_client_factory("comprehend", profile))
        pii_label = RecordedComprehend.LABEL + f" ({recording.path.relative_to(REPO_ROOT)})"

    aoss = FakeAoss()
    # real calls (on a recording miss) bypass moto by URL; moto mocks bedrock-runtime even when listed by name
    real = [svc for svc, on in (("comprehend", args.pii == "aws"), ("bedrock-runtime", args.bedrock)) if on]
    passthrough = {"core": {"passthrough": {"urls": [rf"https://{svc}\.[a-z0-9-]+\.amazonaws\.com/.*"
                                                     for svc in real]}}} if real else None
    meta = _package_meta(pkg_dir)
    package_id = args.package_id or meta["packageId"]
    with mock_aws(config=passthrough) if passthrough else mock_aws():
        s3 = _setup_aws(package_id, meta["name"], meta["description"], meta["groups"])
        clients = {"s3": s3, "textract": textract, "comprehend": comprehend, "stepfunctions": FakeSfn()}
        svc = Services(clients=clients, embedder_override=None if args.bedrock else HashEmbedder(),
                       aoss_override=aoss, docling_override=docling, sparql_override=sparql)
        if bedrock_client is not None:
            svc.clients["bedrock-runtime"] = bedrock_client
        run_info: dict[str, Any] = {
            "source": source_label, "vocab": f"{_rel(vocab_path)} ({vocab_label})",
            "docling": docling.version(),
            "scanPages": MODEL_VERSION_LABEL if have_docling else "unavailable (docling not installed)",
            "phaseBEmbeddings": RecordedTitan.LABEL if args.bedrock
            else "local-hash-embedder (NOT Titan; deterministic, not semantic)",
            "aws": "moto (S3, DynamoDB) in-process; no AWS resources",
            "piiGate": pii_label,
            "until": args.until, "documents": [],
        }
        until = None if args.until == "ready" else "units"
        if until is None:
            graph_label = (f"graph: SPARQL UPDATE into {NOT_NEPTUNE}" if isinstance(sparql, OxiSparql)
                           else "graph: N-Quads written, no SPARQL store (pip install pyoxigraph)")
            run_info["afterUnits"] = (f"pii_gate: {pii_label}; index: in-memory stand-in for "
                                      f"OpenSearch Serverless; {graph_label}")
        with use_services(svc):
            for pdf in pdfs:
                doc_id = doc_id_for(package_id, pdf.name)
                key = f"bronze/{OWNER}/{package_id}/{pdf.name}"
                put = s3.put_object(Bucket=BUCKET, Key=key, Body=pdf.read_bytes(), ContentType="application/pdf")
                entry: dict[str, Any] = {"file": pdf.name, "docId": doc_id, "lanes": {}}
                for pv in (PV_NAIVE, PV_EU):
                    trig = {"pipelineVersion": pv, "bucket": BUCKET, "key": key, "versionId": put.get("VersionId"),
                            "size": pdf.stat().st_size, "trigger": "s3"}
                    lane = LocalIngest(until=until).run(trig)
                    entry["lanes"][pv] = {"status": lane.status, "error": lane.error, "states": lane.states_visited}
                    print(f"{pv:>6}  {pdf.name:<45} {lane.status}"
                          + (f"  {lane.error['Error']}: {json.loads(lane.error['Cause'])['errorMessage'][:120]}"
                             if lane.error else "")
                          + ("  (stopped at units by design: --until units)"
                             if lane.status == "STOPPED" and until == "units" else ""))
                    units_key = f"silver/{package_id}/{doc_id}/{pv}/units/units.jsonl"
                    dest = out / f"v{pv}"
                    dest.mkdir(parents=True, exist_ok=True)
                    try:
                        body = s3.get_object(Bucket=BUCKET, Key=units_key)["Body"].read()
                        (dest / f"{doc_id}.units.jsonl").write_bytes(body)
                        report = s3.get_object(Bucket=BUCKET, Key=units_key.replace("units.jsonl",
                                                                                     "build_report.json"))
                        (dest / f"{doc_id}.build_report.json").write_bytes(report["Body"].read())
                    except s3.exceptions.NoSuchKey:
                        (dest / f"{doc_id}.units.jsonl").write_text("")
                status_dir = out / "status"
                status_dir.mkdir(parents=True, exist_ok=True)
                (status_dir / f"{doc_id}.json").write_text(
                    json.dumps(_records(svc.table("doc_status"), doc_id), indent=1, default=str))
                run_info["documents"].append(entry)
            graph_summary = None
            if isinstance(sparql, OxiSparql):
                graph_summary = write_graph_check(sparql, out, package_id=package_id, owner_sub=OWNER,
                                                  groups=CALLER_GROUPS, tbox_files=tbox_files)
                run_info["graphCheck"] = graph_summary
            if args.until == "ready" and not args.no_world:
                from .world import dump_world

                dump_world(out, s3=s3, dynamodb=_ddb(), bucket=BUCKET,
                           aoss_docs=aoss.docs, meta=run_info | {"packageId": package_id, "ownerSub": OWNER,
                                                                 "groups": CALLER_GROUPS})
        if titan_rec is not None:
            titan_rec.save()
            run_info["embeddingCalls"] = {"recorded": titan_rec.hits, "aws": titan_rec.misses,
                                          "inputTokens": bedrock_client.input_tokens}
        if recording is not None:
            recording.save()
            run_info["piiGateCalls"] = {"recorded": recording.hits, "aws": recording.misses}
        (out / "run.json").write_text(json.dumps(run_info, indent=1))
        summary = write_diff(out, [(e["file"], e["docId"]) for e in run_info["documents"]], run_info)
    print(f"\nwrote {out}/ (diff.md, diff.json, run.json, v1.0/, v2.3.1/, status/)")
    print(summary)
    if graph_summary:
        print(graph_summary)
    return 0


MODEL_VERSION_LABEL = "local Docling OCR shaped as a Textract response (NOT Amazon Textract)"

if __name__ == "__main__":
    raise SystemExit(main())
