from __future__ import annotations

from evidence_core.config import LocalConfigSource, load_pipeline
from evidence_core.enums import LaneState, StageId, StageState
from evidence_core.models.status import LaneRecord, StageResult, iso
from evidence_core.models.tools import DocStatusResult, RegionResult
from evidence_core.stages import resolve_stages
from evidence_core.status import StatusWriter
from evidence_core.testing import SAMPLE_PACKAGE
from lambdas.tools._lib.testing import USER_B_SUB, injected
from lambdas.tools.doc_status import handler as doc_status
from lambdas.tools.get_region import handler as get_region
from lambdas.tools.tests.conftest import REPO


def test_get_region_field_bbox(world):
    res = RegionResult.load(get_region.handler({"unitId": world.w2.unit_id, "fieldId": "f-box1", **injected()}))
    assert res.page == 1 and res.bbox.x0 == 50 and res.bbox.y1 == 284
    assert res.field_bbox == res.bbox and res.unit_bboxes[0].x1 == 562
    assert res.value == "98,400.00" and res.concept == "fin:W2WagesBox1"
    assert res.page_image_key.endswith(f"{world.w2.doc_id}/2.3.1/pages/p001.png")
    assert len(res.held_regions) == 1 and res.url is None
    assert res.engine == "docling"


def test_get_region_unit_bbox_without_field(world):
    res = RegionResult.load(get_region.handler({"unitId": world.w2.unit_id, **injected()}))
    assert res.field_id is None and res.bbox.x1 == 562


def test_get_region_refusals(world):
    b = injected(sub=USER_B_SUB, groups=["markets-analyst"])
    assert get_region.handler({"unitId": world.w2.unit_id, **b})["error"]["code"] == "forbidden"
    # unit from another version than the injected one
    assert get_region.handler({"unitId": world.w2.unit_id, **injected("1.0")})["error"]["code"] == "forbidden"
    assert get_region.handler({"unitId": world.held_unit_id, **injected()})["error"]["code"] == "not_found"
    assert get_region.handler({"unitId": world.chunk.unit_id, **injected("1.0")})["error"]["code"] == "not_found"
    out = get_region.handler({"unitId": world.w2.unit_id, "fieldId": "f-nope", **injected()})
    assert out["error"]["code"] == "not_found"
    assert get_region.handler({"unitId": "bad id", **injected()})["error"]["code"] == "bad_request"


def _seed_status(world):
    table = world.stores.table("doc_status")
    lp = load_pipeline("2.3.1", LocalConfigSource(REPO))
    w = StatusWriter(table, world.w2.doc_id, SAMPLE_PACKAGE, "2.3.1", StageId.REGISTER)
    w.init_lane(resolve_stages(lp.config), LaneRecord(doc_id=world.w2.doc_id, package_id=SAMPLE_PACKAGE,
                                                      pipeline_version="2.3.1", state=LaneState.RUNNING,
                                                      current_stage=StageId.REGISTER, started_at=iso()))
    w.start()
    w.succeed(StageResult(stage_id=StageId.REGISTER, state=StageState.SUCCEEDED))
    w.lane_succeeded(units=4, held=1, indexed=3)


def test_doc_status(world):
    _seed_status(world)
    res = DocStatusResult.load(doc_status.handler({**injected()}))
    assert res.package_id == SAMPLE_PACKAGE and res.pipeline_version == "2.3.1" and res.ready
    (doc,) = res.docs
    assert doc.doc_id == world.w2.doc_id and doc.file_name == world.w2.file_name and doc.doc_type == "doc:W2"
    assert doc.lane_state is LaneState.SUCCEEDED and doc.units == 4 and doc.held == 1
    assert doc.stages["register"] is StageState.SUCCEEDED and doc.stages["extract"] is StageState.PENDING
    empty = DocStatusResult.load(doc_status.handler({**injected("1.0")}))
    assert empty.docs == [] and not empty.ready
    assert doc_status.handler({"docId": "nope-000000", **injected()})["error"]["code"] == "not_found"
    b = injected(sub=USER_B_SUB, groups=["markets-analyst"])
    assert doc_status.handler({**b})["error"]["code"] == "forbidden"
