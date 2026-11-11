"""rp-status-stream: DynamoDB stream on rp-doc-status -> package rollup. For every package touched by
a STAGE or LANE record, query ``byPackage`` once and rewrite ``Package.lanes`` / ``counters`` /
``docCount``."""

from __future__ import annotations

from typing import Any

from boto3.dynamodb.conditions import Key
from evidence_core.ddb import from_ddb, to_ddb
from evidence_core.models.status import iso

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.rollup import compute_rollup


def packages_in(event: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for rec in event.get("Records", []):
        image = (rec.get("dynamodb") or {}).get("NewImage") or (rec.get("dynamodb") or {}).get("OldImage") or {}
        rt = image.get("recordType", {}).get("S")
        pkg = image.get("gsi1pk", {}).get("S") or image.get("packageId", {}).get("S")
        if rt in ("STAGE", "LANE") and pkg:
            out.add(pkg)
    return out


def _query_all(table: Any, **kwargs: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def rollup_package(package_id: str) -> dict[str, Any] | None:
    svc = services()
    pkg_tbl = svc.table("packages")
    pkg = pkg_tbl.get_item(Key={"packageId": package_id}).get("Item")
    if not pkg:
        return None  # deleted package: nothing to roll up
    items = [from_ddb(i) for i in _query_all(svc.table("doc_status"), IndexName="byPackage",
                                              KeyConditionExpression=Key("gsi1pk").eq(package_id))]
    pvs = list(pkg.get("pipelineVersions") or ["1.0", "2.3.1"])
    lanes, counters = compute_rollup(items, pvs)
    docs = _query_all(svc.table("documents"), KeyConditionExpression=Key("packageId").eq(package_id),
                      ProjectionExpression="docId")
    update = {"lanes": {pv: r.dump() for pv, r in lanes.items()},
              "counters": {pv: c.dump() for pv, c in counters.items()},
              "docCount": len(docs), "updatedAt": iso()}
    pkg_tbl.update_item(
        Key={"packageId": package_id},
        UpdateExpression="SET #l = :l, #c = :c, #d = :d, #u = :u",
        ConditionExpression="attribute_exists(packageId)",
        ExpressionAttributeNames={"#l": "lanes", "#c": "counters", "#d": "docCount", "#u": "updatedAt"},
        ExpressionAttributeValues={":l": to_ddb(update["lanes"]), ":c": to_ddb(update["counters"]),
                                   ":d": update["docCount"], ":u": update["updatedAt"]},
    )
    return update


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    done = {}
    for pkg in sorted(packages_in(event)):
        try:
            done[pkg] = rollup_package(pkg) is not None
        except services().table("packages").meta.client.exceptions.ConditionalCheckFailedException:
            done[pkg] = False
    return {"packages": done}
