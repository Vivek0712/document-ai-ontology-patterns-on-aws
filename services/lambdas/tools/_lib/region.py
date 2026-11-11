"""``resolve_region()``: shared by the get_region tool and GET /region/{unitId} (get_region_http).

Order of checks (nothing is read from Gold before the ACL passes):
1. unitId well formed; optional scope check (tool: unit must be in the injected package and version);
2. DocAcl for the unit's document against the caller's principals -> 403;
3. UnitStatus: held units never left Silver -> 404;
4. Gold unit record -> page, bbox (field bbox if present, else first unit bbox), page size;
5. held regions on that page (black boxes on the page image) for the viewer.
"""

from __future__ import annotations

from evidence_core import s3keys
from evidence_core.bbox import BBox
from evidence_core.enums import PiiFlag
from evidence_core.ids import FIELD_ID_RE, parse_unit_id
from evidence_core.models.tools import RegionResult
from evidence_core.models.units import Unit

from .errors import REGION_FORBIDDEN_MESSAGE, BadRequest, Forbidden, NotFound
from .stores import Stores

NO_REGION_MESSAGE = "chunk has no page region"


def resolve_region(
    stores: Stores,
    unit_id: str,
    field_id: str | None,
    principals: list[str],
    *,
    package_id: str | None = None,
    pipeline_version: str | None = None,
) -> RegionResult:
    try:
        doc_id, pv, _local = parse_unit_id(unit_id)
    except ValueError as e:
        raise BadRequest(f"invalid unitId {unit_id!r}") from e
    if field_id is not None and not FIELD_ID_RE.match(field_id):
        raise BadRequest(f"invalid fieldId {field_id!r}")
    if pipeline_version is not None and pv != pipeline_version:
        raise Forbidden(f"unit {unit_id} is not in pipeline version {pipeline_version}")

    acl = stores.require_doc_acl(doc_id, principals, REGION_FORBIDDEN_MESSAGE)
    if package_id is not None and acl.package_id != package_id:
        raise Forbidden(REGION_FORBIDDEN_MESSAGE)

    status = stores.get_unit_status(unit_id)
    if status is not None and (status.held or status.pii_flag is PiiFlag.HELD):
        raise NotFound("This unit is held by the PII gate and has no region to show.")

    raw = stores.read_json(s3keys.gold_unit_key(acl.package_id, unit_id))
    if raw is None:
        raise NotFound(f"Unit {unit_id} is not in Gold.")
    unit = Unit.load(raw)
    if unit.pii_flag is PiiFlag.HELD:
        raise NotFound("This unit is held by the PII gate and has no region to show.")
    if not unit.bboxes:
        raise NotFound(f"{unit.chunk_label or unit.unit_id}: {NO_REGION_MESSAGE}")

    field = unit.field(field_id) if field_id else None
    if field_id and field is None:
        raise NotFound(f"Unit {unit_id} has no field {field_id}.")
    field_bbox = field.bbox if field else None
    bbox: BBox = field_bbox or unit.bboxes[0]
    if unit.page_width is None or unit.page_height is None:
        raise NotFound(f"Unit {unit_id} has no page size.")

    held = _held_regions(stores, acl.package_id, doc_id, pv, bbox.page)
    return RegionResult(
        unit_id=unit.unit_id,
        field_id=field.field_id if field else None,
        doc_id=unit.doc_id,
        file_name=unit.file_name,
        label=unit.label,
        page=bbox.page,
        bbox=bbox,
        field_bbox=field_bbox,
        unit_bboxes=unit.bboxes,
        page_width=unit.page_width,
        page_height=unit.page_height,
        page_image_key=s3keys.page_image_key(acl.package_id, doc_id, pv, bbox.page),
        held_regions=held,
        value=field.value if field else None,
        field_label=field.label if field else None,
        concept=field.concept if field else None,
        unit_type=unit.unit_type,
        breadcrumb=unit.breadcrumb_text(),
        engine=unit.provenance.engine.value,
        pii_flag=unit.pii_flag,
        pipeline_version=unit.pipeline_version,
        ontology_version=unit.provenance.ontology_version,
    )


def _held_regions(stores: Stores, package_id: str, doc_id: str, pv: str, page: int) -> list[BBox]:
    raw = stores.read_json(s3keys.held_regions_key(package_id, doc_id, pv)) or {}
    return [BBox.load(b) for b in (raw.get("pages") or {}).get(str(page), [])]
