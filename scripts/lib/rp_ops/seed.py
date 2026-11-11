"""seed.sh steps: Cognito users, PipelineVersions rows, ontology load, demo package (+ optional upload)."""

from __future__ import annotations

import secrets
import string
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError
from evidence_core import s3keys
from evidence_core.config import S3ConfigSource, load_pipeline
from evidence_core.ddb import to_ddb
from evidence_core.ids import doc_id_for, principals_for
from evidence_core.models.api import DocAclItem, DocumentItem, Package, PipelineVersionItem
from evidence_core.models.status import iso

from .auth import DEMO_GROUPS, DEMO_USERS
from .aws import SSM, Aws, lambda_name, log

PIPELINE_VERSIONS: tuple[str, ...] = ("1.0", "2.3.1")
DEMO_PACKAGE_ID = "pkg-lending-01"
DEMO_PACKAGE_NAME = "Lending package · Fictus household"
DEMO_PACKAGE_DESCRIPTION = "Constructed documents (NewAge Global Fintech, fictitious). W-2s, payslips, bank statement."


# --- users -------------------------------------------------------------------------------------

def generate_password(length: int = 20) -> str:
    """Meets the default Cognito policy: upper, lower, digit, symbol."""
    alphabet = string.ascii_letters + string.digits
    core = "".join(secrets.choice(alphabet) for _ in range(length - 4))
    return core + secrets.choice(string.ascii_uppercase) + secrets.choice(string.ascii_lowercase) + \
        secrets.choice(string.digits) + "!"


def ensure_demo_password(aws: Aws, explicit: str | None) -> str:
    if explicit:
        return explicit
    existing = aws.ssm("demo_password", required=False, decrypt=True)
    if existing:
        return existing
    pw = generate_password()
    aws.client("ssm").put_parameter(Name=SSM["demo_password"], Value=pw, Type="SecureString", Overwrite=False,
                                    Description="Regionproof demo users' password (fictitious users)")
    log(f"generated demo password, stored as SecureString {SSM['demo_password']}")
    return pw


def seed_users(aws: Aws, password: str) -> dict[str, str]:
    """Create groups and the two demo users (idempotent). Returns {user key: cognito sub}."""
    idp = aws.client("cognito-idp")
    pool_id = aws.ssm("user_pool_id")
    subs: dict[str, str] = {}
    for key, email in DEMO_USERS.items():
        group = DEMO_GROUPS[key]
        try:
            idp.create_group(UserPoolId=pool_id, GroupName=group, Description=f"Regionproof {group}")
            log(f"group {group}: created")
        except idp.exceptions.GroupExistsException:
            log(f"group {group}: exists")
        try:
            idp.admin_create_user(
                UserPoolId=pool_id, Username=email, MessageAction="SUPPRESS",
                UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}],
            )
            log(f"user {email}: created")
        except idp.exceptions.UsernameExistsException:
            log(f"user {email}: exists")
        idp.admin_set_user_password(UserPoolId=pool_id, Username=email, Password=password, Permanent=True)
        idp.admin_add_user_to_group(UserPoolId=pool_id, Username=email, GroupName=group)
        user = idp.admin_get_user(UserPoolId=pool_id, Username=email)
        subs[key] = next(a["Value"] for a in user["UserAttributes"] if a["Name"] == "sub")
    return subs


# --- pipeline versions --------------------------------------------------------------------------

def pipeline_item(pv: str, source: Any) -> PipelineVersionItem:
    lp = load_pipeline(pv, source)
    cfg = lp.config
    compact = lp.models.models.get("MODEL_COMPACT")
    return PipelineVersionItem(
        pipeline_version=pv,
        title=cfg.title,
        mode=cfg.mode,
        config_key=lp.source_key,
        config_version_id=lp.version_id,
        config_sha=lp.config_sha,
        block_shas=lp.block_shas,
        prompt_sha=lp.prompt_sha,
        model_ids={
            "answer": cfg.generation.model_id,
            "embed": cfg.retrieval.embed_model,
            **({"compact": compact.model_id} if compact else {}),
        },
        ontology_version=cfg.ontology_version,
        registered_at=iso(),
    )


def seed_pipelines(aws: Aws, bucket: str) -> list[PipelineVersionItem]:
    """Rows in rp-pipeline-versions, pinned to the S3 versionId of the deployed config objects."""
    source = S3ConfigSource(bucket=bucket, client=aws.client("s3"))
    table = aws.table("pipeline_versions")
    items = []
    for pv in PIPELINE_VERSIONS:
        item = pipeline_item(pv, source)
        table.put_item(Item=to_ddb(item.dump()))
        log(f"pipeline {pv}: configSha {item.config_sha[:8]} versionId {item.config_version_id} "
            f"promptSha {item.prompt_sha[:8]}")
        items.append(item)
    shas = {i.block_shas["retrieval"] for i in items}, {i.block_shas["generation"] for i in items}
    if any(len(s) != 1 for s in shas) or len({i.prompt_sha for i in items}) != 1:
        raise SystemExit("pipeline versions differ outside preprocessing; refusing to seed "
                         "(only the preprocessing block may differ)")
    return items


# --- ontology -----------------------------------------------------------------------------------

def load_ontology(aws: Aws, ontology_version: str) -> dict[str, Any]:
    """TBox into Neptune via rp-neptune-load (the laptop has no route into the VPC), then the vocab
    snapshot re-export via rp-vocab-export."""
    loaded = aws.invoke(lambda_name("neptune_load"), {"op": "load_tbox", "ontologyVersion": ontology_version})
    log(f"neptune_load: {str(loaded)[:300]}")
    vocab = aws.invoke(lambda_name("vocab_export"), {"ontologyVersion": ontology_version})
    log(f"vocab_export: {str(vocab)[:300]}")
    return {"neptuneLoad": loaded, "vocabExport": vocab}


# --- demo package -------------------------------------------------------------------------------

def demo_package(owner_sub: str, *, package_id: str = DEMO_PACKAGE_ID) -> Package:
    return Package(
        package_id=package_id,
        name=DEMO_PACKAGE_NAME,
        description=DEMO_PACKAGE_DESCRIPTION,
        owner_sub=owner_sub,
        created_at=iso(),
        allowed_principals=principals_for(owner_sub, [DEMO_GROUPS["user-a"]]),
        pipeline_versions=list(PIPELINE_VERSIONS),
    )


def seed_package(aws: Aws, owner_sub: str, *, package_id: str = DEMO_PACKAGE_ID) -> Package:
    """Create the package row if absent (never overwrites lanes/counters of a live package)."""
    pkg = demo_package(owner_sub, package_id=package_id)
    table = aws.table("packages")
    try:
        table.put_item(Item=to_ddb(pkg.dump()), ConditionExpression="attribute_not_exists(packageId)")
        log(f"package {package_id}: created (owner user-a)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        log(f"package {package_id}: exists")
    return pkg


def sample_files(samples_dir: Path) -> list[Path]:
    files = sorted(p for p in samples_dir.glob("*.pdf") if p.is_file())
    if not files:
        raise SystemExit(f"no PDFs in {samples_dir}: run `make data` first")
    return files


def upload_documents(aws: Aws, bucket: str, pkg: Package, files: list[Path]) -> list[DocumentItem]:
    """Do what POST /upload-url + the presigned PUT do: DocumentItem (AWAITING_UPLOAD), DocAcl, then the
    Bronze PUT, which fires EventBridge -> two rp-ingest executions per file."""
    s3 = aws.client("s3")
    docs_t, acl_t, pkg_t = aws.table("documents"), aws.table("doc_acl"), aws.table("packages")
    out: list[DocumentItem] = []
    for path in files:
        key = s3keys.bronze_key(pkg.owner_sub, pkg.package_id, path.name)
        doc = DocumentItem(
            package_id=pkg.package_id, doc_id=doc_id_for(pkg.package_id, path.name), file_name=path.name,
            bronze_key=key, size_bytes=path.stat().st_size, uploaded_by=pkg.owner_sub,
            upload_url_issued_at=iso(),
        )
        acl = DocAclItem(doc_id=doc.doc_id, package_id=pkg.package_id, owner_sub=pkg.owner_sub,
                         allowed_principals=pkg.allowed_principals)
        docs_t.put_item(Item=to_ddb(doc.dump()))
        acl_t.put_item(Item=to_ddb(acl.dump()))
        s3.put_object(Bucket=bucket, Key=key, Body=path.read_bytes(), ContentType="application/pdf")
        log(f"uploaded {path.name} -> {key} (docId {doc.doc_id})")
        out.append(doc)
    pkg_t.update_item(Key={"packageId": pkg.package_id}, UpdateExpression="SET docCount = :n, updatedAt = :t",
                      ExpressionAttributeValues={":n": len(_package_docs(docs_t, pkg.package_id)), ":t": iso()})
    return out


def _package_docs(docs_t: Any, package_id: str) -> list[dict[str, Any]]:
    from boto3.dynamodb.conditions import Key

    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("packageId").eq(package_id)}
    while True:
        resp = docs_t.query(**kwargs)
        items += resp.get("Items", [])
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
