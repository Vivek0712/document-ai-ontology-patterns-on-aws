"""reset.sh: remove everything one package produced so it can be uploaded again from a clean state.

Order matters: stop in-flight executions first (they would write again), then the knowledge stores
(AOSS docs, Neptune ABox graphs), then S3 and DynamoDB, and finally zero the package row's rollup.
Runs (rp-runs + runs/*.json) are the logged evidence and are kept unless ``include_runs`` is set.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from boto3.dynamodb.conditions import Key
from evidence_core import s3keys
from evidence_core.ddb import from_ddb
from evidence_core.models.status import iso
from evidence_core.retrieval import INDEX_NAMES

from .aws import Aws, lambda_name, log

MAX_AOSS_ROUNDS = 50


@dataclass
class ResetReport:
    package_id: str
    dry_run: bool
    deleted: dict[str, int] = field(default_factory=dict)

    def add(self, what: str, n: int) -> None:
        self.deleted[what] = self.deleted.get(what, 0) + n


def _query_all(table: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
    while True:
        resp = table.query(**kwargs)
        yield from resp.get("Items", [])
        if "LastEvaluatedKey" not in resp:
            return
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def package_doc_ids(aws: Aws, package_id: str) -> set[str]:
    docs = {i["docId"] for i in _query_all(aws.table("documents"),
                                           KeyConditionExpression=Key("packageId").eq(package_id))}
    status = {i["pk"] for i in _query_all(aws.table("doc_status"), IndexName="byPackage",
                                          KeyConditionExpression=Key("gsi1pk").eq(package_id))}
    return docs | status


def execution_matches(input_json: str, package_id: str) -> bool:
    try:
        key = json.loads(input_json).get("key", "")
    except (json.JSONDecodeError, AttributeError):
        return False
    try:
        return s3keys.parse_bronze_key(key).package_id == package_id
    except ValueError:
        return False


def stop_executions(aws: Aws, package_id: str, report: ResetReport) -> None:
    arn = aws.ssm("state_machine_arn", required=False)
    if not arn:
        log("state machine not deployed; nothing to stop")
        return
    sfn = aws.client("stepfunctions")
    for page in sfn.get_paginator("list_executions").paginate(stateMachineArn=arn, statusFilter="RUNNING"):
        for ex in page["executions"]:
            desc = sfn.describe_execution(executionArn=ex["executionArn"])
            if execution_matches(desc.get("input", "{}"), package_id):
                report.add("executionsStopped", 1)
                if not report.dry_run:
                    sfn.stop_execution(executionArn=ex["executionArn"], error="rp.Reset",
                                       cause=f"scripts/reset.sh {package_id}")


def purge_aoss(aws: Aws, package_id: str, report: ResetReport) -> None:
    """OpenSearch Serverless has no _delete_by_query: search by packageId, delete each _id, repeat."""
    endpoint = aws.ssm("aoss_endpoint", required=False)
    if not endpoint:
        log("AOSS endpoint not in SSM; skipping index cleanup")
        return
    body = {"size": 500, "_source": False, "query": {"term": {"packageId": package_id}}}
    for index in INDEX_NAMES.values():
        for _ in range(MAX_AOSS_ROUNDS):
            status, res = aws.signed_request("POST", f"{endpoint}/{index}/_search", service="aoss", body=body)
            if status == 404:
                log(f"{index}: not created yet")
                break
            if status >= 400:
                raise RuntimeError(f"AOSS search {index} -> {status}: {str(res)[:300]}")
            hits = res.get("hits", {}).get("hits", [])
            if not hits:
                break
            report.add(f"aoss:{index}", len(hits))
            if report.dry_run:
                break
            for h in hits:
                d_status, d_res = aws.signed_request("DELETE", f"{endpoint}/{index}/_doc/{h['_id']}", service="aoss")
                if d_status >= 400 and d_status != 404:
                    raise RuntimeError(f"AOSS delete {h['_id']} -> {d_status}: {str(d_res)[:300]}")
        else:
            raise RuntimeError(f"{index}: documents for {package_id} still present after {MAX_AOSS_ROUNDS} rounds")


def drop_graphs(aws: Aws, package_id: str, report: ResetReport) -> None:
    if report.dry_run:
        report.add("graphs", 0)
        return
    res = aws.invoke(lambda_name("graph_admin"), {"op": "drop_package", "packageId": package_id})
    graphs = res.get("graphs") if isinstance(res, dict) else None
    report.add("graphs", graphs if isinstance(graphs, int) else 0)
    log(f"graph_admin: {str(res)[:300]}")


def _delete_versions(aws: Aws, bucket: str, prefix: str, report: ResetReport, label: str) -> None:
    s3 = aws.client("s3")
    batch: list[dict[str, str]] = []

    def flush() -> None:
        if batch and not report.dry_run:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": list(batch), "Quiet": True})
        batch.clear()

    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix):
        for v in page.get("Versions", []) + page.get("DeleteMarkers", []):
            batch.append({"Key": v["Key"], "VersionId": v["VersionId"]})
            report.add(label, 1)
            if len(batch) == 1000:
                flush()
    flush()


def purge_s3(aws: Aws, bucket: str, package_id: str, owner_sub: str | None, report: ResetReport) -> None:
    prefixes = {
        "s3:silver": f"{s3keys.SILVER}/{package_id}/",
        "s3:gold": f"{s3keys.GOLD}/{package_id}/",
        "s3:quarantine": f"{s3keys.QUARANTINE}/{package_id}/",
    }
    if owner_sub:
        prefixes["s3:bronze"] = s3keys.bronze_prefix(owner_sub, package_id)
    for label, prefix in prefixes.items():
        _delete_versions(aws, bucket, prefix, report, label)


def purge_tables(aws: Aws, package_id: str, doc_ids: set[str], pipeline_versions: list[str], *,
                 include_runs: bool, bucket: str, report: ResetReport) -> None:
    status_t, unit_t = aws.table("doc_status"), aws.table("unit_status")
    acl_t, docs_t, review_t = aws.table("doc_acl"), aws.table("documents"), aws.table("review_queue")
    dry = report.dry_run

    with status_t.batch_writer() as bw:
        for doc_id in sorted(doc_ids):
            for item in _query_all(status_t, KeyConditionExpression=Key("pk").eq(doc_id),
                                   ProjectionExpression="pk, sk"):
                report.add("ddb:docStatus", 1)
                if not dry:
                    bw.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
    with unit_t.batch_writer() as bw:
        for pv in pipeline_versions:
            for item in _query_all(unit_t, IndexName="byPackageLane",
                                   KeyConditionExpression=Key("packageLane").eq(f"{package_id}#{pv}")):
                report.add("ddb:unitStatus", 1)
                if not dry:
                    bw.delete_item(Key={"unitId": item["unitId"]})
    for doc_id in sorted(doc_ids):
        report.add("ddb:docAcl", 1)
        report.add("ddb:documents", 1)
        if not dry:
            acl_t.delete_item(Key={"docId": doc_id})
            docs_t.delete_item(Key={"packageId": package_id, "docId": doc_id})
    with review_t.batch_writer() as bw:
        for item in _query_all(review_t, KeyConditionExpression=Key("packageId").eq(package_id)):
            report.add("ddb:reviewQueue", 1)
            if not dry:
                bw.delete_item(Key={"packageId": package_id, "reviewKey": item["reviewKey"]})
    if include_runs:
        runs_t, s3 = aws.table("runs"), aws.client("s3")
        for item in _query_all(runs_t, IndexName="byPackage", KeyConditionExpression=Key("packageId").eq(package_id)):
            report.add("ddb:runs", 1)
            if not dry:
                runs_t.delete_item(Key={"runId": item["runId"]})
                s3.delete_object(Bucket=bucket, Key=s3keys.run_key(item["runId"]))


def reset_package_row(aws: Aws, package_id: str, *, drop: bool, report: ResetReport) -> None:
    table = aws.table("packages")
    if report.dry_run:
        return
    if drop:
        table.delete_item(Key={"packageId": package_id})
        report.add("ddb:packages", 1)
        return
    table.update_item(
        Key={"packageId": package_id},
        UpdateExpression="SET lanes = :e, counters = :e, docCount = :z, updatedAt = :t",
        ConditionExpression="attribute_exists(packageId)",
        ExpressionAttributeValues={":e": {}, ":z": 0, ":t": iso()},
    )


def reset_package(aws: Aws, package_id: str, *, dry_run: bool = False, include_runs: bool = False,
                  drop_package: bool = False) -> ResetReport:
    report = ResetReport(package_id=package_id, dry_run=dry_run)
    bucket = aws.ssm("data_bucket")
    assert bucket
    pkg_item = aws.table("packages").get_item(Key={"packageId": package_id}).get("Item")
    pkg = from_ddb(pkg_item) if pkg_item else None
    if pkg is None:
        log(f"package {package_id} has no row in rp-packages; cleaning stores anyway")
    owner = pkg.get("ownerSub") if pkg else None
    pvs = list(pkg.get("pipelineVersions", INDEX_NAMES)) if pkg else list(INDEX_NAMES)

    log("1/6 stop running rp-ingest executions for this package")
    stop_executions(aws, package_id, report)
    doc_ids = package_doc_ids(aws, package_id)
    log(f"     {len(doc_ids)} documents found")
    log("2/6 OpenSearch Serverless: delete unit documents (units-v1-0, units-v2-3-1)")
    purge_aoss(aws, package_id, report)
    log("3/6 Neptune: drop ABox named graphs via rp-graph-admin")
    drop_graphs(aws, package_id, report)
    log("4/6 S3: delete every object version under bronze/silver/gold/quarantine for the package")
    purge_s3(aws, bucket, package_id, owner, report)
    log("5/6 DynamoDB: doc-status, unit-status, doc-acl, documents, review-queue" + (", runs" if include_runs else ""))
    purge_tables(aws, package_id, doc_ids, pvs, include_runs=include_runs, bucket=bucket, report=report)
    log("6/6 package row: " + ("delete" if drop_package else "zero lanes, counters and docCount"))
    if pkg is not None:
        reset_package_row(aws, package_id, drop=drop_package, report=report)
    return report
