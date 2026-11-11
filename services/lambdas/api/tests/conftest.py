"""Fixtures for the REST API tests: moto tables + versioned bucket, a seeded package with one W-2
document processed by both lanes, and an API Gateway proxy event builder."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from lambdas.api.tests.support import BUCKET, REPO_ROOT, SUB_A, make_event


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("RP_ENV", "test")
    monkeypatch.setenv("RP_CONFIG_SOURCE", "local")
    monkeypatch.setenv("RP_REPO_ROOT", str(REPO_ROOT))
    monkeypatch.setenv("RP_DATA_BUCKET", BUCKET)
    monkeypatch.setenv("RP_CORS_ORIGINS", "https://web.example.com")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("RP_AGENT_RUNTIME_ARN", raising=False)
    monkeypatch.delenv("RP_STATE_MACHINE_ARN", raising=False)


@dataclass
class Env:
    deps: Any
    ddb: Any
    s3: Any


@pytest.fixture
def env() -> Iterator[Env]:
    from moto import mock_aws

    with mock_aws():
        import boto3
        from evidence_core.config import LocalConfigSource
        from evidence_core.ddb import create_tables_for_tests
        from evidence_core.env import Settings

        from lambdas.api.deps import Deps, set_deps

        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        create_tables_for_tests(ddb)
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        deps = Deps(
            settings=Settings.from_env(),
            ddb=ddb,
            s3=s3,
            sfn=boto3.client("stepfunctions", region_name="us-east-1"),
            lambda_client=None,
            ssm=boto3.client("ssm", region_name="us-east-1"),
            config_source=LocalConfigSource(root=Path(os.environ["RP_REPO_ROOT"])),
        )
        set_deps(deps)
        try:
            yield Env(deps=deps, ddb=ddb, s3=s3)
        finally:
            set_deps(None)


Call = Callable[..., tuple[int, Any]]


@pytest.fixture
def call(env: Env) -> Call:
    from lambdas.api.handler import handler

    def _call(method: str, resource: str, **kw: Any) -> tuple[int, Any]:
        resp = handler(make_event(method, resource, **kw), None)
        body = resp["body"]
        return resp["statusCode"], json.loads(body) if body else None

    return _call


# --- seeded package ----------------------------------------------------------------------------------


@dataclass
class Seed:
    package_id: str
    doc_id: str
    file_name: str
    bronze_key: str
    unit_id: str  # 2.3.1 evidence unit
    chunk_id: str  # 1.0 chunk
    held_unit_id: str


def _put_json(s3: Any, key: str, data: Any) -> None:
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(data).encode(), ContentType="application/json")


@pytest.fixture
def seeded(env: Env) -> Seed:
    """pkg-lending-01 owned by user A (shared with lending-analyst), one W-2 through both lanes."""
    from evidence_core import s3keys
    from evidence_core.bbox import BBox
    from evidence_core.ddb import to_ddb
    from evidence_core.enums import (
        ArtifactKind,
        Engine,
        LaneState,
        PiiFlag,
        StageId,
        UnitType,
    )
    from evidence_core.ids import chunk_label, chunk_local, eu_local, unit_id
    from evidence_core.models.api import DocAclItem, DocumentItem, Package
    from evidence_core.models.status import ArtifactRef, LaneRecord, StageResult, StageVersions, iso
    from evidence_core.models.units import (
        BlockRef,
        PiiDecision,
        PiiDecisions,
        Unit,
        UnitProvenance,
        UnitStatusItem,
    )
    from evidence_core.stages import resolve_stages
    from evidence_core.status import StatusWriter
    from evidence_core.testing import make_ctx, make_w2_unit

    deps = env.deps
    ctx = make_ctx("2.3.1", bucket=BUCKET)
    pkg_id, doc_id = ctx.package_id, ctx.doc_id
    principals = [f"user:{SUB_A}", "group:lending-analyst"]
    now = iso()
    pkg = Package(package_id=pkg_id, name="Lending 01", owner_sub=SUB_A, created_at=now,
                  allowed_principals=principals, doc_count=1)
    deps.table("packages").put_item(Item=to_ddb(pkg.dump()))
    doc = DocumentItem(package_id=pkg_id, doc_id=doc_id, file_name=ctx.file_name, bronze_key=ctx.bronze_key,
                       size_bytes=12345, sha256="ab" * 32, source_version_id="v1", page_count=1, doc_type="doc:W2",
                       uploaded_by=SUB_A, upload_state="IN_BRONZE", upload_url_issued_at=now, in_bronze_at=now)
    deps.table("documents").put_item(Item=to_ddb(doc.dump()))
    deps.table("doc_acl").put_item(Item=to_ddb(DocAclItem(doc_id=doc_id, package_id=pkg_id, owner_sub=SUB_A,
                                                          allowed_principals=principals).dump()))
    env.s3.put_object(Bucket=BUCKET, Key=ctx.bronze_key, Body=b"%PDF-1.7 fake 123456789", ContentType="application/pdf")

    tbl = deps.table("doc_status")
    # --- 2.3.1 lane: every stage succeeded ------------------------------------------------------------
    pv = "2.3.1"
    w = StatusWriter(tbl, doc_id, pkg_id, pv, StageId.REGISTER)
    stages = resolve_stages(deps.pipeline(pv).config)
    w.init_lane(stages, LaneRecord(doc_id=doc_id, package_id=pkg_id, pipeline_version=pv, state=LaneState.RUNNING,
                                   execution_arn=ctx.execution_arn, started_at=now, config_sha=ctx.config_sha))
    docling_key = s3keys.docling_key(pkg_id, doc_id, pv)
    for st in stages:
        if not st.enabled:
            continue
        sw = w.for_stage(st.id)
        sw.start(engine=Engine.DOCLING if st.id is StageId.EXTRACT else None,
                 versions=StageVersions(pipeline_version=pv, config_sha=ctx.config_sha,
                                        engine_version="docling-serve-cpu 1.38.2"
                                        if st.id is StageId.EXTRACT else None))
        sw.heartbeat(force=True, message="working")
        arts = []
        if st.id is StageId.EXTRACT:
            arts = [ArtifactRef(key=docling_key, kind=ArtifactKind.DOCLING_JSON, sha256="cd" * 32, count=12)]
        sw.succeed(StageResult(stage_id=st.id, state="SUCCEEDED", headline=f"{st.id.value} ok", artifacts=arts))
    w.lane_succeeded(units=2, held=1, indexed=1, triples=40)

    unit = make_w2_unit(ctx)
    ref = BlockRef(engine=Engine.DOCLING, artifact_key=docling_key, pointer="#/tables/0/data/table_cells/3",
                   engine_label="table")
    unit.fields[0].block_refs = [ref]
    unit.members[0].block_ref = BlockRef(engine=Engine.DOCLING, artifact_key=docling_key, pointer="#/tables/0",
                                         engine_label="table")
    held = unit.model_copy(update={
        "unit_id": unit_id(doc_id, pv, eu_local(2)), "label": "W-2 · employee SSN", "pii_flag": PiiFlag.HELD,
        "text": "a Employee's social security number 900-12-3456", "fields": [], "members": [],
        "bboxes": [BBox(page=1, x0=50, y0=60, x1=300, y1=90)], "unit_type": UnitType.FIELD_GROUP,
    })
    silver_units = s3keys.silver_units_key(pkg_id, doc_id, pv)
    env.s3.put_object(Bucket=BUCKET, Key=silver_units,
                      Body=("\n".join(u.dump_json() for u in (unit, held)) + "\n").encode())
    _put_json(env.s3, s3keys.gold_unit_key(pkg_id, unit.unit_id), unit.dump())
    _put_json(env.s3, docling_key, {"schema_name": "DoclingDocument", "texts": [], "tables": [{"label": "table"}]})
    _put_json(env.s3, s3keys.held_regions_key(pkg_id, doc_id, pv),
              {"pages": {"1": [held.bboxes[0].dump()]}})
    _put_json(env.s3, s3keys.pii_decisions_key(pkg_id, doc_id, pv), PiiDecisions(
        doc_id=doc_id, pipeline_version=pv, rule="rp-pii-v1",
        decisions=[PiiDecision(unit_id=unit.unit_id, decision=PiiFlag.NONE, rule="rp-pii-v1"),
                   PiiDecision(unit_id=held.unit_id, decision=PiiFlag.HELD, rule="rp-pii-v1",
                               held_bboxes=held.bboxes)]).dump())
    env.s3.put_object(Bucket=BUCKET, Key=s3keys.page_image_key(pkg_id, doc_id, pv, 1), Body=b"\x89PNG fake",
                      ContentType="image/png")
    for u, flag in ((unit, PiiFlag.NONE), (held, PiiFlag.HELD)):
        is_held = flag is PiiFlag.HELD
        deps.table("unit_status").put_item(Item=to_ddb(UnitStatusItem(
            unit_id=u.unit_id, doc_id=doc_id, package_id=pkg_id, pipeline_version=pv, doc_lane=f"{doc_id}#{pv}",
            package_lane=f"{pkg_id}#{pv}", unit_type=u.unit_type, label=u.label, pii_flag=flag, held=is_held,
            silver_key=silver_units, gold_key=None if is_held else s3keys.gold_unit_key(pkg_id, u.unit_id),
            aoss_id=None if is_held else "aoss-1", indexed_at=None if is_held else now).dump()))

    # --- 1.0 lane: running at the units stage ----------------------------------------------------------
    pv1 = "1.0"
    w1 = StatusWriter(tbl, doc_id, pkg_id, pv1, StageId.REGISTER)
    w1.init_lane(resolve_stages(deps.pipeline(pv1).config),
                 LaneRecord(doc_id=doc_id, package_id=pkg_id, pipeline_version=pv1, state=LaneState.RUNNING,
                            started_at=now))
    w1.start()
    w1.succeed(StageResult(stage_id=StageId.REGISTER, state="SUCCEEDED", headline="1 page"))
    w1.for_stage(StageId.EXTRACT).start(engine=Engine.TEXT_LAYER)
    chunk = Unit(
        unit_id=unit_id(doc_id, pv1, chunk_local(1)), doc_id=doc_id, package_id=pkg_id, pipeline_version=pv1,
        unit_type=UnitType.CHUNK, label=chunk_label(1), chunk_label=chunk_label(1), file_name=ctx.file_name,
        text="Wages tips other compensation 98,400.00", pages=[1], allowed_principals=principals, chars=40,
        provenance=UnitProvenance(engine=Engine.TEXT_LAYER, source_key=ctx.bronze_key, source_sha="ab" * 32,
                                  config_sha="x" * 64),
    )
    _put_json(env.s3, s3keys.gold_unit_key(pkg_id, chunk.unit_id), chunk.dump())
    return Seed(package_id=pkg_id, doc_id=doc_id, file_name=ctx.file_name, bronze_key=ctx.bronze_key,
                unit_id=unit.unit_id, chunk_id=chunk.unit_id, held_unit_id=held.unit_id)
