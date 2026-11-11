"""Read/write helpers over the contract tables and the data bucket."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any

from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError
from evidence_core.ddb import from_ddb
from evidence_core.models.api import DocAclItem, DocumentItem, Package
from evidence_core.models.status import LaneRecord, StageEvent, StageRecord
from evidence_core.models.units import Unit, UnitStatusItem

from .deps import Deps
from .http import bad_request

# --- generic DynamoDB ------------------------------------------------------------------------


def query_all(table: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
    while True:
        page = table.query(**kwargs)
        for item in page.get("Items", []):
            yield from_ddb(item)
        lek = page.get("LastEvaluatedKey")
        if not lek:
            return
        kwargs["ExclusiveStartKey"] = lek


def scan_all(table: Any, **kwargs: Any) -> Iterator[dict[str, Any]]:
    while True:
        page = table.scan(**kwargs)
        for item in page.get("Items", []):
            yield from_ddb(item)
        lek = page.get("LastEvaluatedKey")
        if not lek:
            return
        kwargs["ExclusiveStartKey"] = lek


def encode_cursor(lek: dict[str, Any] | None) -> str | None:
    if not lek:
        return None
    raw = json.dumps(from_ddb(lek), separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        pad = "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(cursor + pad))
    except (ValueError, json.JSONDecodeError) as exc:
        raise bad_request("invalid cursor") from exc
    if not isinstance(data, dict) or not all(isinstance(v, str | int) for v in data.values()):
        raise bad_request("invalid cursor")
    return data


# --- packages / documents / ACL --------------------------------------------------------------


def get_package(deps: Deps, package_id: str) -> Package | None:
    item = deps.table("packages").get_item(Key={"packageId": package_id}).get("Item")
    return Package.load(from_ddb(item)) if item else None


def list_packages_for(deps: Deps, principals: list[str]) -> list[Package]:
    if not principals:
        return []
    cond = Attr("allowedPrincipals").contains(principals[0])
    for p in principals[1:]:
        cond = cond | Attr("allowedPrincipals").contains(p)
    items = [Package.load(i) for i in scan_all(deps.table("packages"), FilterExpression=cond)]
    return sorted(items, key=lambda p: p.created_at, reverse=True)


def query_documents(deps: Deps, package_id: str) -> list[DocumentItem]:
    rows = query_all(deps.table("documents"), KeyConditionExpression=Key("packageId").eq(package_id))
    return sorted((DocumentItem.load(r) for r in rows), key=lambda d: d.file_name)


def get_document(deps: Deps, package_id: str, doc_id: str) -> DocumentItem | None:
    item = deps.table("documents").get_item(Key={"packageId": package_id, "docId": doc_id}).get("Item")
    return DocumentItem.load(from_ddb(item)) if item else None


def find_document(deps: Deps, doc_id: str) -> DocumentItem | None:
    rows = list(query_all(deps.table("documents"), IndexName="byDocId",
                          KeyConditionExpression=Key("docId").eq(doc_id)))
    return DocumentItem.load(rows[0]) if rows else None


def get_doc_acl(deps: Deps, doc_id: str) -> DocAclItem | None:
    item = deps.table("doc_acl").get_item(Key={"docId": doc_id}).get("Item")
    return DocAclItem.load(from_ddb(item)) if item else None


# --- DocStatus ----------------------------------------------------------------------------------


def package_status_records(deps: Deps, package_id: str) -> list[dict[str, Any]]:
    """Every STAGE and LANE record of a package (GSI byPackage)."""
    return list(query_all(deps.table("doc_status"), IndexName="byPackage",
                          KeyConditionExpression=Key("gsi1pk").eq(package_id)))


def doc_records(deps: Deps, doc_id: str, prefix: str) -> list[dict[str, Any]]:
    return list(query_all(deps.table("doc_status"),
                          KeyConditionExpression=Key("pk").eq(doc_id) & Key("sk").begins_with(prefix)))


def lane_record(deps: Deps, doc_id: str, pv: str) -> LaneRecord | None:
    item = deps.table("doc_status").get_item(Key={"pk": doc_id, "sk": f"LANE#{pv}"}).get("Item")
    return LaneRecord.load(from_ddb(item)) if item else None


def stage_history(deps: Deps, doc_id: str, pv: str, stage_id: str, cap: int = 10) -> list[StageRecord]:
    """This stage in earlier executions (archived by StatusWriter.init_lane), newest first."""
    rows = [r for r in doc_records(deps, doc_id, f"HIST#{pv}#")
            if r.get("recordType") == "STAGE_HIST" and r.get("stageId") == stage_id]
    rows.sort(key=lambda r: r["sk"], reverse=True)
    return [StageRecord.load(r) for r in rows[:cap]]


def stage_records(deps: Deps, doc_id: str, pv: str) -> list[StageRecord]:
    recs = [StageRecord.load(r) for r in doc_records(deps, doc_id, f"STAGE#{pv}#")]
    return sorted(recs, key=lambda r: r.order)


def stage_events(deps: Deps, doc_id: str, pv: str, *, limit: int, cursor: dict[str, Any] | None,
                 stage_id: str | None = None) -> tuple[list[StageEvent], dict[str, Any] | None]:
    """Events oldest first. With ``stage_id`` the page is filtered (DynamoDB filter after the limit)."""
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("pk").eq(doc_id) & Key("sk").begins_with(f"EVENT#{pv}#"),
        "ScanIndexForward": True,
        "Limit": limit,
    }
    if stage_id:
        kwargs["FilterExpression"] = Attr("stageId").eq(stage_id)
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    page = deps.table("doc_status").query(**kwargs)
    events = [StageEvent.load(from_ddb(i)) for i in page.get("Items", [])]
    return events, page.get("LastEvaluatedKey")


def all_stage_events(deps: Deps, doc_id: str, pv: str, stage_id: str, cap: int = 500,
                     execution_arn: str | None = None) -> list[StageEvent]:
    """Events of one stage, oldest first. With ``execution_arn`` only that run's events (events written
    before runs were stamped have no executionArn and are kept)."""
    out: list[StageEvent] = []
    for row in query_all(deps.table("doc_status"),
                         KeyConditionExpression=Key("pk").eq(doc_id) & Key("sk").begins_with(f"EVENT#{pv}#"),
                         FilterExpression=Attr("stageId").eq(stage_id)):
        ev = StageEvent.load(row)
        if execution_arn and ev.execution_arn and ev.execution_arn != execution_arn:
            continue
        out.append(ev)
        if len(out) >= cap:
            break
    return out


# --- UnitStatus ------------------------------------------------------------------------------------


def unit_status_for_lane(deps: Deps, doc_id: str, pv: str) -> list[UnitStatusItem]:
    rows = query_all(deps.table("unit_status"), IndexName="byDocLane",
                     KeyConditionExpression=Key("docLane").eq(f"{doc_id}#{pv}"))
    return [UnitStatusItem.load(r) for r in rows]


def unit_status_for_package_lane(deps: Deps, package_id: str, pv: str) -> list[UnitStatusItem]:
    rows = query_all(deps.table("unit_status"), IndexName="byPackageLane",
                     KeyConditionExpression=Key("packageLane").eq(f"{package_id}#{pv}"))
    return [UnitStatusItem.load(r) for r in rows]


def get_unit_status(deps: Deps, unit_id: str) -> UnitStatusItem | None:
    item = deps.table("unit_status").get_item(Key={"unitId": unit_id}).get("Item")
    return UnitStatusItem.load(from_ddb(item)) if item else None


# --- S3 ---------------------------------------------------------------------------------------------


def _missing(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound", "NoSuchVersion")


def read_bytes(deps: Deps, key: str) -> bytes | None:
    try:
        return deps.s3.get_object(Bucket=deps.bucket, Key=key)["Body"].read()
    except ClientError as exc:
        if _missing(exc):
            return None
        raise


def read_json(deps: Deps, key: str) -> Any | None:
    data = read_bytes(deps, key)
    return json.loads(data) if data is not None else None


def read_jsonl(deps: Deps, key: str) -> list[dict[str, Any]] | None:
    data = read_bytes(deps, key)
    if data is None:
        return None
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def head(deps: Deps, key: str) -> dict[str, Any] | None:
    try:
        return deps.s3.head_object(Bucket=deps.bucket, Key=key)
    except ClientError as exc:
        if _missing(exc):
            return None
        raise


def presign_get(deps: Deps, key: str, expires: int) -> str:
    return deps.s3.generate_presigned_url("get_object", Params={"Bucket": deps.bucket, "Key": key},
                                          ExpiresIn=expires)


def load_units_jsonl(deps: Deps, key: str) -> list[Unit]:
    rows = read_jsonl(deps, key) or []
    return [Unit.load(r) for r in rows]
