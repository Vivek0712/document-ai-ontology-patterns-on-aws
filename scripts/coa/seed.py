#!/usr/bin/env python3
"""Re-seed a fresh COA deployment with the governed-metric demo (the recorded session, runs/local/coa/2026-11-11).

Run by the ``rp-coa-lifecycle`` build after ``make deploy-dev`` (POST_BUILD), or by hand: ``make coa-seed``. Every
step is idempotent, so a re-run after a partial seed finishes the job. Standard library + boto3 only.

1. S3: ``<bucketPrefix>-<account>-<region>`` (``coa-`` prefixed, so COA's serve role may read it), SSE-S3, public
   access blocked, holding ``data/coa/w2_box1.csv`` (fictitious W-2 Box 1 rows from ``data/samples/lending``).
2. Glue: database + external CSV table over that object (the metric SQL names them, as the recording does).
   Once the namespace exists, the database is tagged ``<prefix>:namespace=<namespaceId>``: COA registers a Glue
   source only for a database whose owner opted that namespace in, and re-checks it on every scan. Every deploy
   makes a new namespace id, so the tag is (re)applied on every run.
3. COA principal: a seed user in COA's own Cognito pool (``/coa/userpool-id``), group ``Admin`` (platform-admin),
   random password never printed or stored, disabled again at the end. COA's ``initialAdminEmail`` is untouched.
4. COA REST (``coa-dev-api`` output ``ApiEndpoint``): namespace, ``fin.ttl`` upload + ingest, Glue source
   (register, table review, approve), metric validate + create, then the Tier-1 query, which must return the
   expected total.

A redeploy creates a new COA user pool and API, so nothing here is cached between runs.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import secrets
import string
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CSV_FIELDS = ("package_id", "doc_id", "field_id", "concept", "borrower", "tax_year", "amount_box1", "currency")
GLUE_COLUMNS = [
    {"Name": "package_id", "Type": "string", "Comment": "Lending package id"},
    {"Name": "doc_id", "Type": "string", "Comment": "Source W-2 document id"},
    {"Name": "field_id", "Type": "string", "Comment": "Field id on the W-2"},
    {"Name": "concept", "Type": "string", "Comment": "Field concept IRI (fin:W2WagesBox1)"},
    {"Name": "borrower", "Type": "string", "Comment": "Borrower name (fictitious)"},
    {"Name": "tax_year", "Type": "int", "Comment": "Tax year of the W-2"},
    {"Name": "amount_box1", "Type": "decimal(12,2)", "Comment": "W-2 Box 1 wages, tips, other compensation (USD)"},
    {"Name": "currency", "Type": "string", "Comment": "ISO 4217 currency"},
]
TAGS = {"project": "provenant", "purpose": "coa-governed-metric-demo"}


def log(msg: str) -> None:
    print(f"[coa-seed] {msg}", flush=True)


# --- data -----------------------------------------------------------------------------------------------------

def rows_from_gold(gold_jsonl: Path) -> list[dict[str, str]]:
    """The W-2 Box 1 rows of a package's gold regions: one per W-2."""
    rows = []
    for line in gold_jsonl.read_text(encoding="utf-8").splitlines():
        g = json.loads(line) if line.strip() else {}
        if g.get("kind") == "field" and g.get("concept") == "fin:W2WagesBox1":
            rows.append({"package_id": g["packageId"], "doc_id": g["docId"], "field_id": g["fieldId"],
                         "concept": g["concept"], "borrower": g["borrower"], "tax_year": str(g["taxYear"]),
                         "amount_box1": g["valueNumber"], "currency": g["currency"]})
    return sorted(rows, key=lambda r: r["doc_id"])


def csv_text(rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writerows(rows)  # no header: the Glue table reads every line
    return buf.getvalue()


def load_config(root: Path) -> dict[str, Any]:
    """contracts/generated/coa_control.json (what the build bundle ships) or config/coa.yaml."""
    gen = root / "contracts" / "generated" / "coa_control.json"
    if gen.exists():
        return json.loads(gen.read_text(encoding="utf-8"))
    import yaml  # the repo venv has it; the build bundle ships the JSON

    return yaml.safe_load((root / "config" / "coa.yaml").read_text(encoding="utf-8"))


# --- COA REST -------------------------------------------------------------------------------------------------

class CoaError(RuntimeError):
    pass


@dataclass
class Coa:
    base: str
    token: str
    timeout: float = 120.0
    opener: Callable[..., Any] = urllib.request.urlopen

    def call(self, method: str, path: str, body: Any = None, ok: tuple[int, ...] = (200, 201, 202)
             ) -> tuple[int, Any]:
        if not self.base.startswith("https://"):
            raise CoaError("the COA REST endpoint must be https")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base.rstrip("/") + path, data=data, method=method, headers={  # noqa: S310
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with self.opener(req, timeout=self.timeout) as r:
                status, text = r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            status, text = e.code, e.read().decode()
        payload = json.loads(text) if text.strip().startswith(("{", "[")) else text
        if status not in ok:
            raise CoaError(f"{method} {path} -> {status}: {str(payload)[:300]}")
        return status, payload


def items(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    for k in (*keys, "items", "results"):
        if isinstance(payload, dict) and isinstance(payload.get(k), list):
            return payload[k]
    return []


def poll(fn: Callable[[], Any], done: Callable[[Any], bool], *, what: str, timeout_s: float = 600,
         every_s: float = 5, sleep: Callable[[float], None] = time.sleep) -> Any:
    waited = 0.0
    while True:
        value = fn()
        if done(value):
            return value
        if waited >= timeout_s:
            raise CoaError(f"timed out after {int(waited)} s waiting for {what}")
        sleep(every_s)
        waited += every_s


def ensure_namespace(coa: Coa, ns: dict[str, str], owner: str) -> str:
    _, listing = coa.call("GET", "/namespaces?maxResults=100")
    for n in items(listing, "namespaces"):
        if n.get("name") == ns["name"]:
            log(f"namespace {ns['name']}: exists")
            return str(n["namespaceId"])
    _, created = coa.call("POST", "/namespaces", {"name": ns["name"], "displayName": ns["displayName"],
                                                  "description": ns["description"], "owner": owner, "language": "en",
                                                  "clientToken": f"{ns['name']}-ns"})
    log(f"namespace {ns['name']}: created")
    return str((created.get("namespace") or created)["namespaceId"])


def ensure_ontology(coa: Coa, ns_id: str, ontology_id: str, ttl: bytes, sha: str,
                    put: Callable[[str, bytes], None], sleep: Callable[[float], None] = time.sleep) -> None:
    enc = urllib.parse.quote(ontology_id, safe="")
    _, listing = coa.call("GET", f"/namespaces/{ns_id}/ontologies")
    for o in items(listing, "ontologies"):
        if o.get("ontologyId") == ontology_id and o.get("parseStatus") == "ok":
            log(f"ontology {ontology_id}: exists ({o.get('classCount')} classes)")
            return
    title = f"Regionproof fin: extension (sha256 {sha[:12]})"
    _, up = coa.call("POST", f"/namespaces/{ns_id}/ontologies/upload-url",
                     {"filename": "fin.ttl", "contentType": "text/turtle", "ontologyId": ontology_id, "title": title})
    put(str(up["uploadUrl"]), ttl)
    _, job = coa.call("POST", f"/namespaces/{ns_id}/ontologies/{enc}/ingest-from-s3",
                      {"s3Key": up["s3Key"], "format": "turtle", "title": title, "ontologyType": "user_uploaded",
                       "clientToken": f"fin-{sha[:12]}"})
    job_id = (job.get("result") or job)["jobId"]
    final = poll(lambda: coa.call("GET", f"/namespaces/{ns_id}/ontologies/{enc}/ingest-status/{job_id}")[1],
                 lambda r: (r.get("result") or r).get("status") in ("completed", "failed"),
                 what="ontology ingest", sleep=sleep)
    res = final.get("result") or final
    if res.get("status") != "completed":
        raise CoaError(f"ontology ingest {res.get('status')}: {res.get('error')}")
    log(f"ontology {ontology_id}: ingested ({(res.get('result') or {}).get('classCount')} classes)")


def ensure_source(coa: Coa, ns_id: str, account: str, region: str, database: str,
                  sleep: Callable[[float], None] = time.sleep) -> str:
    _, listing = coa.call("GET", f"/namespaces/{ns_id}/sources")
    src_id = None
    for s in items(listing, "sources"):
        glue = ((s.get("databaseDetails") or s.get("databaseSource") or {}).get("glueConfiguration") or {})
        if glue.get("databaseName") == database:
            src_id = str(s["sourceId"])
            log(f"source over {database}: exists ({s.get('status')})")
    if src_id is None:
        _, created = coa.call("POST", f"/namespaces/{ns_id}/sources", {
            "sourceType": "DATABASE", "clientToken": f"{database}-src",
            "databaseSource": {"name": "com305 W-2 Box 1 (fictitious)", "metadataEnrichmentEnabled": False,
                               "glueConfiguration": {"catalogId": account, "region": region,
                                                     "databaseName": database}}})
        src_id = str(created["sourceId"])
        log(f"source over {database}: registered")
    status = poll(lambda: coa.call("GET", f"/namespaces/{ns_id}/sources/{src_id}")[1],
                  lambda s: s.get("status") in ("PENDING_REVIEW", "APPROVED", "FAILED", "SCAN_FAILED"),
                  what="source scan", sleep=sleep)
    if status.get("status") == "APPROVED":
        return src_id
    if status.get("status") != "PENDING_REVIEW":
        raise CoaError(f"source scan ended {status.get('status')}")
    _, tables = coa.call("GET", f"/namespaces/{ns_id}/sources/{src_id}/tables")
    for t in items(tables, "tables"):
        if t.get("reviewStatus") != "APPROVED":
            coa.call("PUT", f"/namespaces/{ns_id}/sources/{src_id}/tables/{t['tableId']}/review",
                     {"decision": "APPROVED"})
    coa.call("POST", f"/namespaces/{ns_id}/sources/{src_id}/approve", {})  # one table's review leaves it pending
    poll(lambda: coa.call("GET", f"/namespaces/{ns_id}/sources/{src_id}")[1],
         lambda s: s.get("status") == "APPROVED", what="source approval", sleep=sleep)
    log("source: approved")
    return src_id


def ensure_metric(coa: Coa, ns_id: str, metric: dict[str, Any], source_id: str) -> None:
    status, _ = coa.call("GET", f"/namespaces/{ns_id}/metrics/{metric['name']}", ok=(200, 404))
    if status == 200:
        log(f"metric {metric['name']}: exists")
        return
    body = {**metric, "dataSourceId": source_id}
    _, checked = coa.call("POST", f"/namespaces/{ns_id}/metrics/validate", body)
    if checked.get("warnings"):
        log(f"metric validate warnings: {checked['warnings']}")
    coa.call("POST", f"/namespaces/{ns_id}/metrics", {**body, "clientToken": f"{metric['name']}-metric"})
    log(f"metric {metric['name']}: created")


def check_total(coa: Coa, ns_id: str, metric: dict[str, Any], package_id: str, expected: str) -> str:
    _, res = coa.call("POST", f"/namespaces/{ns_id}/query", {
        "query": metric["aiContext"]["synonyms"][0], "execute": True, "tierOverride": 1, "mode": "standard",
        "dimensions": [{"name": "package_id", "value": package_id}], "timeoutMs": 29000})
    rows = (res.get("result") or {}).get("resultRows") or []
    got = str(rows[0].get(metric["name"])) if rows else "none"
    if got != expected:
        raise CoaError(f"Tier-1 {metric['name']} for {package_id} = {got}, expected {expected}")
    log(f"check: Tier-1 {metric['name']}({package_id}) = {got} ✓")
    return got


# --- AWS -----------------------------------------------------------------------------------------------------

def random_password(n: int = 24) -> str:
    core = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n - 4))
    return core + secrets.choice(string.ascii_uppercase) + secrets.choice(string.ascii_lowercase) + \
        secrets.choice(string.digits) + "#"


def seed_token(idp: Any, pool_id: str, client_id: str, user: str) -> str:
    """An ID token for the seed user (created on first use, group Admin, enabled for this run only)."""
    try:
        idp.admin_create_user(UserPoolId=pool_id, Username=user, MessageAction="SUPPRESS",
                              UserAttributes=[{"Name": "email", "Value": user},
                                              {"Name": "email_verified", "Value": "true"}])
        log("seed user: created")
    except idp.exceptions.UsernameExistsException:
        idp.admin_enable_user(UserPoolId=pool_id, Username=user)
    password = random_password()
    idp.admin_set_user_password(UserPoolId=pool_id, Username=user, Password=password, Permanent=True)
    idp.admin_add_user_to_group(UserPoolId=pool_id, Username=user, GroupName="Admin")
    res = idp.initiate_auth(ClientId=client_id, AuthFlow="USER_PASSWORD_AUTH",
                            AuthParameters={"USERNAME": user, "PASSWORD": password})
    return str(res["AuthenticationResult"]["IdToken"])


def stack_output(cfn: Any, stack: str, prefix: str) -> str:
    for out in cfn.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs") or []:
        if str(out["OutputKey"]).startswith(prefix):
            return str(out["OutputValue"])
    raise CoaError(f"{stack} has no output {prefix}*")


def ensure_bucket(s3: Any, bucket: str, region: str) -> None:
    from botocore.exceptions import ClientError

    try:
        s3.head_bucket(Bucket=bucket)
        log(f"bucket {bucket[:18]}…: exists")
        return
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket", "NotFound"):
            raise
    kw: dict[str, Any] = {"Bucket": bucket}
    if region != "us-east-1":
        kw["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kw)
    s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    s3.put_bucket_encryption(Bucket=bucket, ServerSideEncryptionConfiguration={
        "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]})
    s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in TAGS.items()]})
    log(f"bucket {bucket[:18]}…: created")


def ensure_glue(glue: Any, database: str, table: str, location: str) -> None:
    try:
        glue.create_database(DatabaseInput={"Name": database, "Description": "Provenant COA demo: fictitious "
                                            "W-2 Box 1 rows (pkg-lending-01)"}, Tags=TAGS)
        log(f"glue database {database}: created")
    except glue.exceptions.AlreadyExistsException:
        log(f"glue database {database}: exists")
    table_input = {
        "Name": table, "TableType": "EXTERNAL_TABLE",
        "Description": "Fictitious W-2 Box 1 (Wages, tips, other compensation) values, one row per W-2, lending "
                       "package pkg-lending-01",
        "Parameters": {"classification": "csv", "project": "provenant"},
        "StorageDescriptor": {
            "Columns": GLUE_COLUMNS, "Location": location,
            "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
            "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
            "SerdeInfo": {"SerializationLibrary": "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe",
                          "Parameters": {"field.delim": ","}}}}
    try:
        glue.create_table(DatabaseName=database, TableInput=table_input)
        log(f"glue table {table}: created")
    except glue.exceptions.AlreadyExistsException:
        glue.update_table(DatabaseName=database, TableInput=table_input)
        log(f"glue table {table}: updated (location)")


def namespace_tag_key(prefix: str) -> str:
    """COA's ownership tag (``<prefix>:namespace``; libs/common namespace_tag_key, docs sources.md)."""
    return f"{prefix}:namespace"


def tag_database(glue: Any, account: str, region: str, database: str, key: str, ns_id: str) -> None:
    """Opt the database in to ``ns_id`` (overwrites a stale id from the previous deploy)."""
    glue.tag_resource(ResourceArn=f"arn:aws:glue:{region}:{account}:database/{database}", TagsToAdd={key: ns_id})
    log(f"glue database {database}: tagged {key}={ns_id}")


def put_presigned(url: str, body: bytes) -> None:
    if not url.startswith("https://"):
        raise CoaError("COA's upload URL must be https")
    req = urllib.request.Request(url, data=body, method="PUT", headers={"Content-Type": "text/turtle"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - COA's presigned S3 URL
        if r.status not in (200, 204):
            raise CoaError(f"ontology upload -> {r.status}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=ROOT, help="repo root or the build bundle")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no AWS or COA call")
    args = ap.parse_args(argv)
    cfg = load_config(args.root)
    coa_cfg, seed = cfg["coa"], cfg["seed"]
    csv_body = (args.root / seed["csv"]).read_bytes()
    ttl = (args.root / seed["ontology"]).read_bytes()
    metric = json.loads((args.root / seed["metric"]).read_text(encoding="utf-8"))
    import hashlib

    sha = hashlib.sha256(ttl).hexdigest()
    if args.dry_run:
        log(f"would seed namespace {seed['namespace']['name']}, ontology {seed['ontologyId']} (sha256 {sha[:12]}), "
            f"Glue {seed['glueDatabase']}.{seed['glueTable']} ({len(csv_body)} B), metric {metric['name']}")
        return 0

    import boto3

    region = coa_cfg["region"]
    session = boto3.session.Session(region_name=region)
    account = session.client("sts").get_caller_identity()["Account"]
    ssm = session.client("ssm")
    pool_id = ssm.get_parameter(Name=f"/{coa_cfg['prefix']}/userpool-id")["Parameter"]["Value"]
    client_id = ssm.get_parameter(Name=f"/{coa_cfg['prefix']}/userpool-client-id")["Parameter"]["Value"]
    api = stack_output(session.client("cloudformation"), cfg["outputs"]["restEndpoint"]["stack"],
                       cfg["outputs"]["restEndpoint"]["outputPrefix"])

    bucket = f"{seed['bucketPrefix']}-{account}-{region}"
    s3 = session.client("s3")
    ensure_bucket(s3, bucket, region)
    key = f"{seed['glueTable']}/{seed['glueTable']}.csv"
    s3.put_object(Bucket=bucket, Key=key, Body=csv_body, ContentType="text/csv")
    glue = session.client("glue")
    ensure_glue(glue, seed["glueDatabase"], seed["glueTable"], f"s3://{bucket}/{seed['glueTable']}/")

    idp = session.client("cognito-idp")
    user = seed["seedUser"]
    try:
        coa = Coa(api, seed_token(idp, pool_id, client_id, user))
        ns_id = ensure_namespace(coa, seed["namespace"], user)
        tag_database(glue, account, region, seed["glueDatabase"], namespace_tag_key(coa_cfg["prefix"]), ns_id)
        ensure_ontology(coa, ns_id, seed["ontologyId"], ttl, sha, put_presigned)
        src = ensure_source(coa, ns_id, account, region, seed["glueDatabase"])
        ensure_metric(coa, ns_id, metric, src)
        check_total(coa, ns_id, metric, seed["check"]["packageId"], seed["check"]["expected"])
    finally:
        try:
            idp.admin_disable_user(UserPoolId=pool_id, Username=user)
            log("seed user: disabled")
        except Exception as exc:  # noqa: BLE001 - reported, the seed result stands
            log(f"seed user: could not disable ({type(exc).__name__})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CoaError as exc:
        log(f"FAILED: {exc}")
        sys.exit(1)
