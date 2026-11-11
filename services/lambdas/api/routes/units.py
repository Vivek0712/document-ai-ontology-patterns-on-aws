"""GET /units/{unitId}, GET /units/{unitId}/lineage?field="""

from __future__ import annotations

from evidence_core.ids import FIELD_ID_RE
from evidence_core.models.api import UnitView

from .. import store
from ..acl import require_doc
from ..deps import get_deps
from ..http import Reply, Request, bad_request, not_found
from ..lineage import build_lineage
from ..units import is_held, load_unit, pii_decision, redact, split_unit_id
from . import UNIT, UNIT_LINEAGE, router


def _resolve(req: Request):  # noqa: ANN202 - (deps, package_id, LoadedUnit)
    deps = get_deps()
    unit_id = req.param("unitId")
    doc_id, _, _ = split_unit_id(unit_id)
    access = require_doc(deps, req.caller, doc_id)
    loaded = load_unit(deps, access.package_id, unit_id)
    if loaded is None:
        raise not_found(f"Unit {unit_id} not found.")
    return deps, access.package_id, loaded


@router.route("GET", UNIT)
def get_unit(req: Request) -> Reply:
    deps, package_id, loaded = _resolve(req)
    decision = pii_decision(deps, package_id, loaded.unit.unit_id)
    held = is_held(loaded.unit, decision)
    unit = redact(loaded.unit) if held else loaded.unit
    status = store.get_unit_status(deps, unit.unit_id)
    return Reply(UnitView(unit=unit, status=status, pii=decision, held=held))


@router.route("GET", UNIT_LINEAGE)
def get_lineage(req: Request) -> Reply:
    field_id = req.query.get("field") or None
    if field_id and not FIELD_ID_RE.match(field_id):
        raise bad_request(f"invalid field id {field_id!r}")
    deps, package_id, loaded = _resolve(req)
    return Reply(build_lineage(deps, package_id, loaded, field_id))
