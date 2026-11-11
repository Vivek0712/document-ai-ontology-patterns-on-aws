"""Factories for builders' tests: a valid DocCtx / StageEnvelope / Unit without AWS."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bbox import BBox
from .config import LocalConfigSource, load_pipeline
from .enums import Engine, UnitType
from .ids import doc_id_for, eu_local, unit_id
from .models.status import DocCtx, StageEnvelope, iso
from .models.units import FieldValue, Member, Unit, UnitProvenance
from .s3keys import bronze_key

SAMPLE_SUB = "7c41e2aa-0000-4000-8000-00000000000a"
SAMPLE_PACKAGE = "pkg-lending-01"


def make_ctx(
    pipeline_version: str = "2.3.1",
    file_name: str = "w2_2025_maya_fictus.pdf",
    *,
    repo_root: Path | None = None,
    **overrides: Any,
) -> DocCtx:
    root = repo_root or Path(__file__).resolve().parents[3]
    lp = load_pipeline(pipeline_version, LocalConfigSource(root))
    doc_id = doc_id_for(SAMPLE_PACKAGE, file_name)
    data: dict[str, Any] = {
        "package_id": SAMPLE_PACKAGE,
        "doc_id": doc_id,
        "pipeline_version": pipeline_version,
        "mode": lp.config.mode,
        "owner_sub": SAMPLE_SUB,
        "file_name": file_name,
        "bucket": "rp-data-test",
        "bronze_key": bronze_key(SAMPLE_SUB, SAMPLE_PACKAGE, file_name),
        "source_sha256": "0" * 64,
        "size_bytes": 12345,
        "page_count": 1,
        "config_key": lp.source_key,
        "config_sha": lp.config_sha,
        "prompt_sha": lp.prompt_sha,
        "ontology_version": lp.config.ontology_version,
        "route_enabled": lp.config.route_enabled,
        "classify_enabled": lp.config.classify_enabled,
        "graph_enabled": lp.config.graph_enabled,
        "allowed_principals": [f"user:{SAMPLE_SUB}", "group:lending-analyst"],
        "execution_arn": "arn:aws:states:us-east-1:000000000000:execution:rp-ingest:test-exec",
        "lane_started_at": iso(),
    }
    data.update(overrides)
    return DocCtx(**data)


def make_envelope(ctx: DocCtx | None = None, **overrides: Any) -> StageEnvelope:
    env = StageEnvelope(ctx=ctx or make_ctx(), execution_arn="arn:aws:states:us-east-1:000000000000:execution:x:y",
                        state_name="BuildUnits")
    return env.model_copy(update=overrides)


def make_w2_unit(ctx: DocCtx | None = None) -> Unit:
    c = ctx or make_ctx()
    box1 = BBox(page=1, x0=50, y0=216, x1=306, y1=284)
    return Unit(
        unit_id=unit_id(c.doc_id, c.pipeline_version, eu_local(1)),
        doc_id=c.doc_id,
        package_id=c.package_id,
        pipeline_version=c.pipeline_version,
        unit_type=UnitType.TABLE_PANEL,
        label="W-2 · Maya Fictus · Boxes 1–6",
        file_name=c.file_name,
        doc_type="doc:W2",
        breadcrumb=["W-2 2025", "Maya Fictus", "Wages (Boxes 1–6)"],
        text="1 Wages, tips, other compensation │ 98,400.00 │ 3 Social security wages │ 105,000.00",
        pages=[1],
        page_width=612,
        page_height=792,
        bboxes=[BBox(page=1, x0=50, y0=216, x1=562, y1=422)],
        members=[Member(member_id="m-01", role="table", bbox=BBox(page=1, x0=50, y0=216, x1=562, y1=422),
                        phase="A")],
        fields=[
            FieldValue(field_id="f-box1", label="1 Wages, tips, other compensation", value="98,400.00",
                       value_number="98400.00", currency="USD", concept="fin:W2WagesBox1", period="TY2025",
                       bbox=box1, source="docling table cell (row 0, col 0)"),
        ],
        field_concepts=["fin:W2WagesBox1"],
        period="TY2025",
        allowed_principals=c.allowed_principals,
        chars=90,
        provenance=UnitProvenance(engine=Engine.DOCLING, source_key=c.bronze_key, source_sha=c.source_sha256,
                                  config_sha=c.config_sha, ontology_version=c.ontology_version),
    )
