"""GET /me, GET|POST /packages, GET|DELETE /packages/{packageId}."""

from __future__ import annotations

import json
import os
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from evidence_core import s3keys
from evidence_core.ddb import to_ddb
from evidence_core.ids import (
    PIPELINE_VERSION_RE,
    group_principal,
    index_name,
    package_id_from_name,
    user_principal,
)
from evidence_core.models.api import (
    DeletePackageResponse,
    Me,
    Package,
    PackageCreate,
    PackageDetail,
    PackageList,
)
from evidence_core.models.status import iso

from .. import store
from ..acl import require_package, require_package_owner
from ..deps import Deps, get_deps
from ..http import Reply, Request, bad_request, conflict, forbidden
from . import ME, PACKAGE, PACKAGES, router

KNOWN_PIPELINE_VERSIONS = ("1.0", "2.3.1")


@router.route("GET", ME)
def get_me(req: Request) -> Reply:
    c = req.caller
    return Reply(Me(sub=c.sub, username=c.username, email=c.email, groups=list(c.groups), principals=c.principals))


@router.route("GET", PACKAGES)
def list_packages(req: Request) -> Reply:
    return Reply(PackageList(items=store.list_packages_for(get_deps(), req.caller.principals)))


def known_pipeline_versions(deps: Deps) -> set[str]:
    """Registered versions (rp-pipeline-versions, active), else the two built-in versions."""
    rows = list(store.scan_all(deps.table("pipeline_versions")))
    active = {r["pipelineVersion"] for r in rows if r.get("status", "active") == "active"}
    return active or set(KNOWN_PIPELINE_VERSIONS)


@router.route("POST", PACKAGES)
def create_package(req: Request) -> Reply:
    deps = get_deps()
    body: PackageCreate = req.parse(PackageCreate)
    caller_groups = set(req.caller.groups)
    extra = [g for g in body.share_with_groups if g not in caller_groups]
    if extra:
        raise forbidden(f"You can only share with groups you belong to (not {', '.join(sorted(extra))}).")
    versions = list(dict.fromkeys(body.pipeline_versions))
    if not versions:
        raise bad_request("pipelineVersions must not be empty")
    bad = [v for v in versions if not PIPELINE_VERSION_RE.match(v)]
    if bad:
        raise bad_request(f"invalid pipelineVersion {bad[0]!r}")
    unknown = sorted(set(versions) - known_pipeline_versions(deps))
    if unknown:
        raise bad_request(f"unknown pipelineVersion {', '.join(unknown)}")
    now = iso()
    pkg = Package(
        package_id=package_id_from_name(body.name),
        name=body.name.strip(),
        description=body.description,
        owner_sub=req.caller.sub,
        created_at=now,
        updated_at=now,
        allowed_principals=[user_principal(req.caller.sub)]
        + [group_principal(g) for g in sorted(set(body.share_with_groups))],
        pipeline_versions=versions,
    )
    try:
        deps.table("packages").put_item(Item=to_ddb(pkg.dump()), ConditionExpression="attribute_not_exists(packageId)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise conflict("Package id collision, retry.") from exc
        raise
    return Reply(pkg, status=201)


@router.route("GET", PACKAGE)
def get_package(req: Request) -> Reply:
    deps = get_deps()
    pkg = require_package(deps, req.caller, req.param("packageId"))
    docs = store.query_documents(deps, pkg.package_id)
    return Reply(PackageDetail(package=pkg, documents=docs, can_write=pkg.owner_sub == req.caller.sub))


# --- delete ------------------------------------------------------------------------------------


def _delete_prefix(deps: Deps, prefix: str) -> int:
    """Delete every object version and delete marker under a prefix (the bucket is versioned)."""
    deleted = 0
    paginator = deps.s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=deps.bucket, Prefix=prefix):
        objs = [{"Key": v["Key"], "VersionId": v["VersionId"]}
                for v in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        for i in range(0, len(objs), 1000):
            batch = objs[i:i + 1000]
            deps.s3.delete_objects(Bucket=deps.bucket, Delete={"Objects": batch, "Quiet": True})
            deleted += len(batch)
    return deleted


def _delete_rows(table: Any, keys: list[dict[str, str]]) -> int:
    with table.batch_writer() as bw:
        for k in keys:
            bw.delete_item(Key=k)
    return len(keys)


def _drop_graphs(deps: Deps, package_id: str, versions: list[str]) -> int:
    fn = _graph_admin_function()
    if deps.lambda_client is None or not fn:
        return 0
    payload = {"op": "drop_package", "packageId": package_id, "pipelineVersions": versions}
    resp = deps.lambda_client.invoke(FunctionName=fn, InvocationType="RequestResponse",
                                     Payload=json.dumps(payload).encode())
    body = json.loads(resp["Payload"].read() or b"{}")
    if resp.get("FunctionError"):
        raise RuntimeError(f"{fn} failed: {body.get('errorMessage', body)}")
    for key in ("dropped", "graphs", "graphsDropped"):
        if isinstance(body.get(key), int):
            return body[key]
        if isinstance(body.get(key), list):
            return len(body[key])
    return len(versions)


def _graph_admin_function() -> str:
    return os.environ.get("RP_GRAPH_ADMIN_FUNCTION", "rp-graph-admin")


@router.route("DELETE", PACKAGE)
def delete_package(req: Request) -> Reply:
    deps = get_deps()
    pkg = require_package_owner(deps, req.caller, req.param("packageId"))
    pid = pkg.package_id
    docs = store.query_documents(deps, pid)
    counts: dict[str, int] = {}

    # 1. index docs by their stored auto _id, collected before the UnitStatus rows go away
    units = [u for pv in pkg.pipeline_versions for u in store.unit_status_for_package_lane(deps, pid, pv)]
    aoss_deleted = 0
    if deps.aoss is not None:
        for u in units:
            if u.aoss_id and deps.aoss.delete_doc(index_name(u.pipeline_version), u.aoss_id):
                aoss_deleted += 1
    counts["aossDocs"] = aoss_deleted

    # 2. ABox graphs (VPC Lambda rp-graph-admin; the API Lambda has no route into the VPC)
    counts["graphs"] = _drop_graphs(deps, pid, pkg.pipeline_versions)

    # 3. objects
    counts["s3Objects"] = sum(
        _delete_prefix(deps, prefix)
        for prefix in (
            s3keys.bronze_prefix(pkg.owner_sub, pid),
            f"{s3keys.SILVER}/{pid}/",
            f"{s3keys.GOLD}/{pid}/",
            f"{s3keys.QUARANTINE}/{pid}/",
        )
    )

    # 4. rows
    status_tbl = deps.table("doc_status")
    status_keys: list[dict[str, str]] = []
    for d in docs:
        status_keys += [{"pk": r["pk"], "sk": r["sk"]} for r in store.query_all(
            status_tbl, KeyConditionExpression=Key("pk").eq(d.doc_id), ProjectionExpression="pk, sk")]
    counts["docStatusItems"] = _delete_rows(status_tbl, status_keys)
    counts["unitStatusItems"] = _delete_rows(deps.table("unit_status"), [{"unitId": u.unit_id} for u in units])
    counts["docAclItems"] = _delete_rows(deps.table("doc_acl"), [{"docId": d.doc_id} for d in docs])
    review_rows = list(store.query_all(deps.table("review_queue"), KeyConditionExpression=Key("packageId").eq(pid)))
    counts["reviewItems"] = _delete_rows(
        deps.table("review_queue"), [{"packageId": pid, "reviewKey": r["reviewKey"]} for r in review_rows])
    counts["documents"] = _delete_rows(deps.table("documents"), [{"packageId": pid, "docId": d.doc_id} for d in docs])
    deps.table("packages").delete_item(Key={"packageId": pid})
    counts["packages"] = 1
    # Runs are kept: they are the reproducibility record of past answers.
    return Reply(DeletePackageResponse(package_id=pid, deleted=counts), status=202)
