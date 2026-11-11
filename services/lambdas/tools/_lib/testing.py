"""Test helpers for the agent-plane Lambdas: a seeded moto world (tables, bucket, one package).

Users: User A (``SAMPLE_SUB``, group lending-analyst) owns ``pkg-lending-01``; User B
(``USER_B_SUB``, group markets-analyst) has no access to it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from evidence_core import s3keys
from evidence_core.bbox import BBox
from evidence_core.ddb import create_tables_for_tests, to_ddb
from evidence_core.enums import Engine, PiiFlag, UnitType
from evidence_core.env import Settings
from evidence_core.ids import chunk_label, chunk_local, unit_id
from evidence_core.models.api import DocAclItem, DocumentItem, Package
from evidence_core.models.status import iso
from evidence_core.models.units import Unit, UnitProvenance, UnitStatusItem
from evidence_core.testing import SAMPLE_PACKAGE, SAMPLE_SUB, make_ctx, make_w2_unit

from .stores import Stores

USER_B_SUB = "9b1d0c3e-0000-4000-8000-00000000000b"
BUCKET = "rp-data-test"
GROUP_A = "lending-analyst"
GROUP_B = "markets-analyst"


@dataclass
class World:
    stores: Stores
    w2: Unit
    chunk: Unit
    held_unit_id: str


def unit_status_for(unit: Unit, *, held: bool = False) -> UnitStatusItem:
    return UnitStatusItem(
        unit_id=unit.unit_id, doc_id=unit.doc_id, package_id=unit.package_id,
        pipeline_version=unit.pipeline_version, doc_lane=f"{unit.doc_id}#{unit.pipeline_version}",
        package_lane=f"{unit.package_id}#{unit.pipeline_version}", unit_type=unit.unit_type, label=unit.label,
        pii_flag=PiiFlag.HELD if held else unit.pii_flag, held=held,
        silver_key=s3keys.silver_units_key(unit.package_id, unit.doc_id, unit.pipeline_version),
        gold_key=None if held else s3keys.gold_unit_key(unit.package_id, unit.unit_id),
    )


def make_chunk(n: int = 3) -> Unit:
    ctx = make_ctx("1.0")
    return Unit(
        unit_id=unit_id(ctx.doc_id, "1.0", chunk_local(n)), doc_id=ctx.doc_id, package_id=ctx.package_id,
        pipeline_version="1.0", unit_type=UnitType.CHUNK, label=chunk_label(n), chunk_label=chunk_label(n),
        file_name=ctx.file_name, text="Wages tips other compensation 98,400.00 Social security wages", pages=[1],
        allowed_principals=ctx.allowed_principals,
        provenance=UnitProvenance(engine=Engine.TEXT_LAYER, source_key=ctx.bronze_key, source_sha=ctx.source_sha256,
                                  config_sha=ctx.config_sha),
    )


def seed_world(dynamodb: Any, s3: Any, settings: Settings) -> World:
    create_tables_for_tests(dynamodb)
    s3.create_bucket(Bucket=BUCKET)
    st = Stores(settings=settings, dynamodb=dynamodb, s3=s3)
    now = iso()
    pkg = Package(package_id=SAMPLE_PACKAGE, name="Lending package", owner_sub=SAMPLE_SUB, created_at=now,
                  allowed_principals=[f"user:{SAMPLE_SUB}", f"group:{GROUP_A}"])
    st.table("packages").put_item(Item=to_ddb(pkg.dump()))
    w2 = make_w2_unit()
    chunk = make_chunk()
    st.table("doc_acl").put_item(Item=to_ddb(DocAclItem(doc_id=w2.doc_id, package_id=SAMPLE_PACKAGE,
                                                        owner_sub=SAMPLE_SUB,
                                                        allowed_principals=pkg.allowed_principals).dump()))
    st.table("documents").put_item(Item=to_ddb(DocumentItem(
        package_id=SAMPLE_PACKAGE, doc_id=w2.doc_id, file_name=w2.file_name,
        bronze_key=s3keys.bronze_key(SAMPLE_SUB, SAMPLE_PACKAGE, w2.file_name), sha256="a" * 64, doc_type="doc:W2",
        uploaded_by=SAMPLE_SUB, upload_state="IN_BRONZE", upload_url_issued_at=now).dump()))
    for u in (w2, chunk):
        s3.put_object(Bucket=BUCKET, Key=s3keys.gold_unit_key(SAMPLE_PACKAGE, u.unit_id),
                      Body=json.dumps(u.dump()).encode())
        st.table("unit_status").put_item(Item=to_ddb(unit_status_for(u).dump()))
    held = w2.model_copy(update={"unit_id": unit_id(w2.doc_id, "2.3.1", "eu-02"), "pii_flag": PiiFlag.HELD})
    st.table("unit_status").put_item(Item=to_ddb(unit_status_for(held, held=True).dump()))
    held_box = BBox(page=1, x0=40, y0=60, x1=300, y1=90)
    s3.put_object(Bucket=BUCKET, Key=s3keys.held_regions_key(SAMPLE_PACKAGE, w2.doc_id, "2.3.1"),
                  Body=json.dumps({"pages": {"1": [held_box.dump()]}}).encode())
    return World(stores=st, w2=w2, chunk=chunk, held_unit_id=held.unit_id)


def injected(pv: str = "2.3.1", *, sub: str = SAMPLE_SUB, groups: list[str] | None = None,
             package_id: str = SAMPLE_PACKAGE, run_id: str | None = "r-0123456789ab") -> dict[str, Any]:
    out: dict[str, Any] = {"callerSub": sub, "groups": [GROUP_A] if groups is None else groups,
                           "pipelineVersion": pv, "packageId": package_id}
    if run_id:
        out["runId"] = run_id
    return out
