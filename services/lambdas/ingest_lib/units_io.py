"""Unit JSONL IO plus the UnitStatus and ReviewQueue table writes shared by the unit stages."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from boto3.dynamodb.conditions import Key
from evidence_core.ddb import from_ddb, to_ddb
from evidence_core.models.status import iso
from evidence_core.models.units import ReviewItem, Unit, UnitStatusItem

from .store import DataStore


def read_units(store: DataStore, key: str) -> list[Unit]:
    return [Unit.load(row) for row in store.get_jsonl(key)]


def doc_lane(doc_id: str, pv: str) -> str:
    return f"{doc_id}#{pv}"


def package_lane(package_id: str, pv: str) -> str:
    return f"{package_id}#{pv}"


def put_unit_statuses(table: Any, units: Iterable[Unit], *, silver_key: str,
                      review_counts: dict[str, int] | None = None,
                      previous: Iterable[UnitStatusItem] = ()) -> int:
    """Write (replace) one UnitStatus row per unit as produced by the units stage.

    ``aossId``/``indexedAt`` of a previous run are carried over: ``embed_index`` deletes exactly
    those OpenSearch documents before it re-indexes the lane."""
    n = 0
    now = iso()
    carried = {p.unit_id: p for p in previous if p.aoss_id}
    with table.batch_writer(overwrite_by_pkeys=["unitId"]) as batch:
        for u in units:
            item = UnitStatusItem(
                unit_id=u.unit_id, doc_id=u.doc_id, package_id=u.package_id, pipeline_version=u.pipeline_version,
                doc_lane=doc_lane(u.doc_id, u.pipeline_version),
                package_lane=package_lane(u.package_id, u.pipeline_version), unit_type=u.unit_type, label=u.label,
                pii_flag=u.pii_flag, held=False, silver_key=silver_key,
                review_items=(review_counts or {}).get(u.unit_id, 0), updated_at=now,
                aoss_id=carried[u.unit_id].aoss_id if u.unit_id in carried else None,
                indexed_at=carried[u.unit_id].indexed_at if u.unit_id in carried else None,
            )
            batch.put_item(Item=to_ddb(item.dump()))
            n += 1
    return n


def unit_statuses_for_lane(table: Any, doc_id: str, pv: str) -> list[UnitStatusItem]:
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"IndexName": "byDocLane",
                              "KeyConditionExpression": Key("docLane").eq(doc_lane(doc_id, pv))}
    while True:
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return [UnitStatusItem.load(from_ddb(i)) for i in items]


def update_unit_status(table: Any, unit_id: str, *, remove: list[str] | None = None, **fields: Any) -> None:
    fields["updatedAt"] = iso()
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    sets: list[str] = []
    for i, (k, v) in enumerate(fields.items()):
        if v is None:
            continue
        names[f"#f{i}"] = k
        values[f":v{i}"] = to_ddb(v)
        sets.append(f"#f{i} = :v{i}")
    expr = "SET " + ", ".join(sets)
    if remove:
        for j, k in enumerate(remove):
            names[f"#r{j}"] = k
        expr += " REMOVE " + ", ".join(f"#r{j}" for j in range(len(remove)))
    table.update_item(Key={"unitId": unit_id}, UpdateExpression=expr, ExpressionAttributeNames=names,
                      ExpressionAttributeValues=values)


def delete_unit_statuses(table: Any, unit_ids: Iterable[str]) -> int:
    n = 0
    with table.batch_writer() as batch:
        for uid in unit_ids:
            batch.delete_item(Key={"unitId": uid})
            n += 1
    return n


def replace_review_items(table: Any, package_id: str, doc_id: str, pv: str, items: list[ReviewItem]) -> None:
    """Replace this doc lane's OPEN review items (a rerun must not leave stale ones)."""
    prefix = f"{doc_id}#{pv}#"
    resp = table.query(KeyConditionExpression=Key("packageId").eq(package_id) & Key("reviewKey").begins_with(prefix))
    with table.batch_writer(overwrite_by_pkeys=["packageId", "reviewKey"]) as batch:
        for old in resp.get("Items", []):
            if old.get("state", "OPEN") == "OPEN":
                batch.delete_item(Key={"packageId": package_id, "reviewKey": old["reviewKey"]})
        for it in items:
            batch.put_item(Item=to_ddb(it.dump()))
