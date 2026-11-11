"""Lineage: a cited figure back through field -> unit -> engine blocks -> page region ->
page image -> Bronze object, plus the index and graph side records. Every step names the stage run
that produced it and the artifacts (S3 keys) it can be checked against."""

from __future__ import annotations

from typing import Any

from evidence_core import iris, s3keys
from evidence_core.enums import ArtifactKind, Engine, StageId, StageState
from evidence_core.ids import index_name, short
from evidence_core.models.api import DocumentItem, Lineage, LineageStep, StageRef
from evidence_core.models.status import ArtifactRef, StageRecord
from evidence_core.models.units import BlockRef, FieldValue, Member, Unit, UnitStatusItem

from . import store
from .deps import Deps
from .http import not_found
from .units import LoadedUnit, is_held, pii_decision, redact

_BLOCK_KIND: dict[Engine, ArtifactKind] = {
    Engine.DOCLING: ArtifactKind.DOCLING_JSON,
    Engine.TEXTRACT: ArtifactKind.TEXTRACT_JSON,
    Engine.TEXT_LAYER: ArtifactKind.TEXT_LAYER,
}


def stage_ref(rec: StageRecord | None) -> StageRef | None:
    if rec is None:
        return None
    return StageRef(stage_id=rec.stage_id, state=rec.state, started_at=rec.started_at, ended_at=rec.ended_at,
                    duration_ms=rec.duration_ms, attempt=rec.attempt, execution_arn=rec.execution_arn)


def _artifact(rec: StageRecord | None, key: str, kind: ArtifactKind, **kw: Any) -> ArtifactRef:
    """Prefer the ArtifactRef the stage recorded (it carries sha/size/version); else a bare ref."""
    if rec is not None:
        for a in rec.artifacts:
            if a.key == key:
                return a
    return ArtifactRef(key=key, kind=kind, **kw)


def _field_step(unit: Unit, fld: FieldValue, gold: ArtifactRef | None, units_rec: StageRecord | None) -> LineageStep:
    return LineageStep(
        kind="field",
        title=f"Field {fld.field_id}",
        summary=f"{fld.label} = {fld.value}" + (f" ({fld.concept})" if fld.concept else ""),
        data={"fieldId": fld.field_id, "label": fld.label, "value": fld.value, "valueNumber": fld.value_number,
              "concept": fld.concept, "period": fld.period, "source": fld.source, "confidence": fld.confidence},
        bbox=fld.bbox,
        artifacts=[gold] if gold else [],
        stage=stage_ref(units_rec),
    )


def _unit_step(unit: Unit, artifacts: list[ArtifactRef], units_rec: StageRecord | None) -> LineageStep:
    kind = "Evidence unit" if unit.chunk_label is None else "Chunk"
    local = unit.unit_id.rsplit(":", 1)[-1]
    return LineageStep(
        kind="unit",
        title=f"{kind} {local}",
        summary=f"{unit.label} · {unit.unit_type.value} · p.{', '.join(str(p) for p in unit.pages) or '-'}",
        data={"unitId": unit.unit_id, "unitType": unit.unit_type.value, "label": unit.label,
              "breadcrumb": unit.breadcrumb, "pages": unit.pages, "piiFlag": unit.pii_flag.value,
              "memberCount": len(unit.members)},
        bbox=unit.bboxes[0] if unit.bboxes else None,
        artifacts=artifacts,
        stage=stage_ref(units_rec),
    )


def _block_refs(unit: Unit, fld: FieldValue | None) -> list[tuple[BlockRef, Member | FieldValue]]:
    """(blockRef, member-or-field) pairs: the field's own refs when a field is cited, else the members'."""
    if fld is not None and fld.block_refs:
        return [(ref, fld) for ref in fld.block_refs]
    return [(m.block_ref, m) for m in unit.members if m.block_ref is not None]


def _blocks_step(deps: Deps, unit: Unit, fld: FieldValue | None, package_id: str, pv: str,
                 extract_rec: StageRecord | None) -> LineageStep | None:
    pairs = _block_refs(unit, fld)
    if pairs:
        engines = sorted({ref.engine.value for ref, _ in pairs})
        items = []
        for ref, owner in pairs:
            is_member = isinstance(owner, Member)
            items.append({
                "pointer": ref.pointer,
                "engineLabel": ref.engine_label,
                "role": owner.role.value if is_member else None,
                "text": owner.text if is_member else owner.value,
                "bbox": owner.bbox.dump() if owner.bbox else None,
                "artifactKey": ref.artifact_key,
            })
        engine_by_key: dict[str, Engine] = {}
        for ref, _ in pairs:
            engine_by_key.setdefault(ref.artifact_key, ref.engine)
        artifacts = [_artifact(extract_rec, k, _BLOCK_KIND.get(e, ArtifactKind.DOCLING_JSON))
                     for k, e in engine_by_key.items()]
        engine_title = " + ".join(e.capitalize() if e != "text-layer" else "Text layer" for e in engines)
        return LineageStep(
            kind="blocks",
            title=f"{engine_title} blocks ({len(items)})",
            summary=", ".join(i["pointer"] for i in items[:6]) + (" …" if len(items) > 6 else ""),
            data={"engine": engines[0] if len(engines) == 1 else engines, "items": items},
            artifacts=artifacts,
            stage=stage_ref(extract_rec),
        )
    if unit.provenance.engine is Engine.TEXT_LAYER or unit.chunk_label is not None:
        # v1.0: the chunk was cut from the page text layer; there are no layout blocks
        key = s3keys.text_layer_key(package_id, unit.doc_id, pv)
        return LineageStep(
            kind="blocks",
            title="Page text layer",
            summary=f"pypdf text of page {', '.join(str(p) for p in unit.pages) or '-'}; no layout blocks, no bbox",
            data={"engine": Engine.TEXT_LAYER.value,
                  "items": [{"pointer": f"page {p}", "engineLabel": None, "role": None, "text": None, "bbox": None,
                             "artifactKey": key} for p in unit.pages]},
            artifacts=[_artifact(extract_rec, key, ArtifactKind.TEXT_LAYER)],
            stage=stage_ref(extract_rec),
        )
    return None


def _source_step(doc: DocumentItem | None, unit: Unit, register_rec: StageRecord | None) -> LineageStep:
    key = doc.bronze_key if doc else unit.provenance.source_key
    version_id = (doc.source_version_id if doc else None) or unit.provenance.source_version_id
    sha = (doc.sha256 if doc else None) or unit.provenance.source_sha
    return LineageStep(
        kind="source",
        title="Bronze object",
        summary=f"{unit.file_name} · sha256 {sha[:8]}…" if sha else unit.file_name,
        data={"fileName": unit.file_name, "bronzeKey": key, "versionId": version_id, "sha256": sha,
              "sizeBytes": doc.size_bytes if doc else None,
              "uploadedBy": short(doc.uploaded_by) if doc else None,
              "inBronzeAt": doc.in_bronze_at if doc else None},
        artifacts=[ArtifactRef(key=key, kind=ArtifactKind.SOURCE_PDF, content_type="application/pdf",
                               sha256=sha, version_id=version_id, size_bytes=doc.size_bytes if doc else None)],
        stage=stage_ref(register_rec),
    )


def build_lineage(deps: Deps, package_id: str, loaded: LoadedUnit, field_id: str | None) -> Lineage:
    unit = loaded.unit
    pv = unit.pipeline_version
    decision = pii_decision(deps, package_id, unit.unit_id)
    held = is_held(unit, decision)
    if held:
        unit = redact(unit)
    fld: FieldValue | None = None
    if field_id:
        fld = unit.field(field_id)
        if fld is None:
            raise not_found(f"Field {field_id} is not in unit {unit.unit_id}.")

    recs = {r.stage_id: r for r in store.stage_records(deps, unit.doc_id, pv)}
    units_rec, extract_rec = recs.get(StageId.UNITS), recs.get(StageId.EXTRACT)
    doc = store.get_document(deps, package_id, unit.doc_id)
    status: UnitStatusItem | None = store.get_unit_status(deps, unit.unit_id)

    silver = _artifact(units_rec, s3keys.silver_units_key(package_id, unit.doc_id, pv), ArtifactKind.UNITS_JSONL)
    gold = None if held else ArtifactRef(key=s3keys.gold_unit_key(package_id, unit.unit_id),
                                         kind=ArtifactKind.GOLD_UNIT)
    steps: list[LineageStep] = []
    if fld is not None:
        steps.append(_field_step(unit, fld, gold, units_rec))
    steps.append(_unit_step(unit, [silver] + ([gold] if gold else []), units_rec))
    blocks = _blocks_step(deps, unit, fld, package_id, pv, extract_rec)
    if blocks:
        steps.append(blocks)

    region_bbox = (fld.bbox if fld and fld.bbox else None) or (unit.bboxes[0] if unit.bboxes else None)
    if region_bbox is not None:
        steps.append(LineageStep(
            kind="region",
            title=f"Page {region_bbox.page} region",
            summary=f"({region_bbox.x0:.0f}, {region_bbox.y0:.0f}) – ({region_bbox.x1:.0f}, {region_bbox.y1:.0f}) pt",
            data={"page": region_bbox.page, "pageWidth": unit.page_width, "pageHeight": unit.page_height,
                  "frame": "pdf-points-top-left"},
            bbox=region_bbox,
            stage=stage_ref(units_rec),
        ))
        promote_rec = recs.get(StageId.PROMOTE)
        held_regions = store.read_json(deps, s3keys.held_regions_key(package_id, unit.doc_id, pv)) or {}
        page_held = (held_regions.get("pages") or {}).get(str(region_bbox.page), [])
        dpi = None
        if promote_rec and promote_rec.config:
            dpi = (promote_rec.config.values.get("preprocessing.render") or {}).get("dpi")
        img_key = s3keys.page_image_key(package_id, unit.doc_id, pv, region_bbox.page)
        steps.append(LineageStep(
            kind="page_image",
            title=f"Gold page image p.{region_bbox.page}",
            summary=f"{dpi or 150} dpi render; {len(page_held)} held region(s) filled black",
            data={"page": region_bbox.page, "dpi": dpi or 150, "heldRegions": page_held},
            artifacts=[_artifact(promote_rec, img_key, ArtifactKind.PAGE_IMAGE, content_type="image/png",
                                 page=region_bbox.page)],
            stage=stage_ref(promote_rec),
        ))

    steps.append(_source_step(doc, unit, recs.get(StageId.REGISTER)))

    index_rec = recs.get(StageId.INDEX)
    if status and (status.aoss_id or status.indexed_at) or (index_rec and index_rec.state is StageState.SUCCEEDED
                                                            and not held):
        idx = index_name(pv)
        steps.append(LineageStep(
            kind="index",
            title=f"Index {idx}",
            summary=f"_id {status.aoss_id}" if status and status.aoss_id else "indexed",
            data={"index": idx, "aossId": status.aoss_id if status else None,
                  "indexedAt": status.indexed_at if status else None},
            artifacts=[_artifact(index_rec, s3keys.index_receipt_key(package_id, unit.doc_id, pv),
                                 ArtifactKind.INDEX_RECEIPT)],
            stage=stage_ref(index_rec),
        ))

    graph_rec = recs.get(StageId.GRAPH)
    if graph_rec and graph_rec.state is StageState.SUCCEEDED and not held:
        graph = iris.abox_graph(package_id, pv)
        steps.append(LineageStep(
            kind="graph",
            title="Neptune named graph",
            summary=graph,
            data={"namedGraph": graph, "unitIri": iris.unit_iri(unit.unit_id),
                  "fieldIri": iris.field_iri(unit.unit_id, fld.field_id) if fld else None,
                  "factConcept": fld.concept if fld else None},
            artifacts=[_artifact(graph_rec, s3keys.abox_key(package_id, unit.doc_id, pv), ArtifactKind.ABOX_NQUADS,
                                 content_type="application/n-quads"),
                       _artifact(graph_rec, s3keys.graph_receipt_key(package_id, unit.doc_id, pv),
                                 ArtifactKind.GRAPH_RECEIPT)],
            stage=stage_ref(graph_rec),
        ))

    engine_version = extract_rec.versions.engine_version if extract_rec and extract_rec.versions else None
    return Lineage(
        unit_id=unit.unit_id,
        field_id=fld.field_id if fld else None,
        package_id=package_id,
        pipeline_version=pv,
        steps=steps,
        versions={
            "pipelineVersion": pv,
            "configSha": unit.provenance.config_sha,
            "ontologyVersion": unit.provenance.ontology_version,
            "engine": unit.provenance.engine.value,
            "engineVersion": unit.provenance.engine_version or engine_version,
        },
    )
