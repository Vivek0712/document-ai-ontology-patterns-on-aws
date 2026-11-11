from __future__ import annotations

import pytest
from evidence_core import bbox, ids, iris, s3keys
from evidence_core.bbox import BBox


def test_textract_ratio_to_points() -> None:
    b = bbox.from_textract({"Left": 0.1, "Top": 0.25, "Width": 0.5, "Height": 0.1}, page=1, page_width=612,
                           page_height=792)
    assert b.rounded() == BBox(page=1, x0=61.2, y0=198.0, x1=367.2, y1=277.2)


def test_docling_bottomleft_flips_to_topleft() -> None:
    # gold box drawn by the generator at y0=216..y1=284 (top-left frame) on a 792pt page
    gold = BBox(page=1, x0=50, y0=216, x1=306, y1=284)
    docling = {"l": 50, "t": 792 - 216, "r": 306, "b": 792 - 284, "coord_origin": "BOTTOMLEFT"}
    assert bbox.from_docling(docling, page=1, page_height=792) == gold
    topleft = {"l": 50, "t": 216, "r": 306, "b": 284, "coord_origin": "TOPLEFT"}
    assert bbox.from_docling(topleft, page=1, page_height=792) == gold


def test_union_coverage_iou() -> None:
    a = BBox(page=1, x0=0, y0=0, x1=10, y1=10)
    b = BBox(page=1, x0=5, y0=5, x1=15, y1=15)
    assert bbox.union([a, b]) == BBox(page=1, x0=0, y0=0, x1=15, y1=15)
    assert bbox.coverage(a, b) == pytest.approx(0.25)
    assert bbox.iou(a, b) == pytest.approx(25 / 175)
    assert bbox.intersection(a, BBox(page=2, x0=0, y0=0, x1=1, y1=1)) is None
    with pytest.raises(ValueError):
        bbox.union([a, BBox(page=2, x0=0, y0=0, x1=1, y1=1)])
    assert [u.page for u in bbox.union_by_page([a, BBox(page=2, x0=0, y0=0, x1=1, y1=1)])] == [1, 2]


def test_bbox_rejects_inverted_and_scales_to_pixels() -> None:
    with pytest.raises(ValueError):
        BBox(page=1, x0=10, y0=0, x1=0, y1=5)
    assert BBox(page=1, x0=50, y0=216, x1=306, y1=284).to_pixels(612, 1275) == (104, 450, 638, 592)


def test_ids() -> None:
    d1 = ids.doc_id_for("pkg-lending-01", "w2_2025_maya_fictus.pdf")
    assert d1 == ids.doc_id_for("pkg-lending-01", "w2_2025_maya_fictus.pdf")
    assert d1.startswith("w2-2025-maya-fictus-") and len(d1.split("-")[-1]) == 6
    assert d1 != ids.doc_id_for("pkg-other", "w2_2025_maya_fictus.pdf")
    uid = ids.unit_id(d1, "2.3.1", ids.eu_local(1))
    assert ids.parse_unit_id(uid) == (d1, "2.3.1", "eu-01")
    assert ids.unit_id(d1, "1.0", ids.chunk_local(3)).endswith(":1.0:c-03")
    assert ids.chunk_label(3) == "chunk c-03"
    with pytest.raises(ValueError):
        ids.unit_id("Bad Doc", "2.3.1", "eu-01")
    assert ids.index_name("1.0") == "units-v1-0"
    assert ids.index_name("2.3.1") == "units-v2-3-1"
    assert ids.new_run_id().startswith("r-")
    assert ids.PACKAGE_ID_RE.match(ids.package_id_from_name("Lending package"))


def test_principals() -> None:
    assert ids.principals_for("abc", ["lending-analyst"]) == ["user:abc", "group:lending-analyst"]
    assert ids.parse_groups_claim("[lending-analyst, admins]") == ["lending-analyst", "admins"]
    assert ids.parse_groups_claim(["a"]) == ["a"]
    assert ids.parse_groups_claim(None) == []
    assert ids.is_allowed(["user:x", "group:g"], ["group:g"])
    assert not ids.is_allowed(["user:x", "group:h"], ["group:g", "user:y"])


def test_s3_keys() -> None:
    k = s3keys.bronze_key("sub123", "pkg-lending-01", "w2.pdf")
    assert k == "bronze/sub123/pkg-lending-01/w2.pdf"
    parsed = s3keys.parse_bronze_key(k)
    assert (parsed.owner_sub, parsed.package_id, parsed.file_name) == ("sub123", "pkg-lending-01", "w2.pdf")
    with pytest.raises(ValueError):
        s3keys.bronze_key("s", "p", "../x.pdf")
    assert s3keys.textract_page_key("p", "d", "2.3.1", 1) == "silver/p/d/2.3.1/extract/textract/p001.json"
    assert s3keys.page_image_key("p", "d", "2.3.1", 2) == "gold/p/d/2.3.1/pages/p002.png"
    assert s3keys.gold_unit_key("p", "d-abc123:2.3.1:eu-01") == "gold/p/d-abc123/2.3.1/units/eu-01.json"
    assert s3keys.pipeline_config_key("2.3.1") == "config/pipelines/v2.3.1.yaml"
    assert s3keys.prompt_key("answer_v1") == "config/prompts/answer_v1.txt"
    assert s3keys.package_of(k) == "pkg-lending-01"
    assert s3keys.package_of("gold/p/d/2.3.1/units.jsonl") == "p"
    assert s3keys.package_of("config/models.yaml") is None
    assert s3keys.tier_of("silver/p/x") == "silver"


def test_iris() -> None:
    assert iris.abox_graph("pkg-lending-01", "2.3.1") == "urn:rp:graph:abox:pkg-lending-01:2.3.1"
    fi = iris.field_iri("d-abc123:2.3.1:eu-01", "f-box1")
    assert iris.field_ref_from_iri(fi) == ("d-abc123:2.3.1:eu-01", "f-box1")
    assert iris.unit_id_from_iri(iris.unit_iri("d:2.3.1:eu-01")) == "d:2.3.1:eu-01"
    assert iris.expand("fin:W2WagesBox1") == "urn:rp:ont:fin:W2WagesBox1"
    assert "PREFIX prov:" in iris.sparql_prefix_block(["prov", "fin"])
