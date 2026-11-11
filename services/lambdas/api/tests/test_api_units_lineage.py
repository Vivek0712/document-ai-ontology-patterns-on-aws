from __future__ import annotations

from urllib.parse import quote

from lambdas.api.routes import UNIT, UNIT_LINEAGE
from lambdas.api.tests.support import SUB_B


def test_unit_view(call, seeded) -> None:
    status, v = call("GET", UNIT, path_params={"unitId": quote(seeded.unit_id, safe="")})
    assert status == 200
    assert v["held"] is False and v["unit"]["fields"][0]["value"] == "98,400.00"
    assert v["status"]["aossId"] == "aoss-1" and v["pii"]["decision"] == "none"


def test_held_unit_is_redacted(call, seeded) -> None:
    status, v = call("GET", UNIT, path_params={"unitId": seeded.held_unit_id})
    assert status == 200 and v["held"] is True
    assert v["unit"]["text"] == "[held: PII]" and "900-12" not in str(v)


def test_unit_acl_and_bad_id(call, seeded) -> None:
    status, _ = call("GET", UNIT, path_params={"unitId": seeded.unit_id}, sub=SUB_B, groups=["markets-analyst"])
    assert status == 403
    status, _ = call("GET", UNIT, path_params={"unitId": "not-a-unit"})
    assert status == 400


def test_lineage_from_field_to_source(call, seeded) -> None:
    status, lin = call("GET", UNIT_LINEAGE, path_params={"unitId": seeded.unit_id}, query={"field": "f-box1"})
    assert status == 200
    kinds = [s["kind"] for s in lin["steps"]]
    assert kinds == ["field", "unit", "blocks", "region", "page_image", "source", "index", "graph"]
    steps = {s["kind"]: s for s in lin["steps"]}
    assert steps["field"]["data"]["value"] == "98,400.00" and steps["field"]["data"]["concept"] == "fin:W2WagesBox1"
    assert steps["field"]["bbox"] == {"page": 1, "x0": 50.0, "y0": 216.0, "x1": 306.0, "y1": 284.0}
    assert steps["blocks"]["data"]["items"][0]["pointer"] == "#/tables/0/data/table_cells/3"
    blocks_art = steps["blocks"]["artifacts"][0]
    assert blocks_art["kind"] == "docling_json" and blocks_art["sha256"] == "cd" * 32  # stage-recorded ref
    assert steps["blocks"]["stage"]["stageId"] == "extract" and steps["blocks"]["stage"]["state"] == "SUCCEEDED"
    assert steps["region"]["bbox"]["page"] == 1 and steps["region"]["data"]["frame"] == "pdf-points-top-left"
    assert len(steps["page_image"]["data"]["heldRegions"]) == 1
    assert steps["source"]["data"]["bronzeKey"] == seeded.bronze_key
    assert steps["source"]["data"]["versionId"] == "v1" and len(steps["source"]["data"]["uploadedBy"]) == 6
    assert steps["index"]["data"] == {"index": "units-v2-3-1", "aossId": "aoss-1",
                                      "indexedAt": steps["index"]["data"]["indexedAt"]}
    assert steps["graph"]["data"]["fieldIri"] == f"urn:rp:field:{seeded.unit_id}/f-box1"
    assert lin["versions"]["engine"] == "docling" and lin["versions"]["engineVersion"] == "docling-serve-cpu 1.38.2"


def test_lineage_unit_only_uses_member_blocks(call, seeded) -> None:
    status, lin = call("GET", UNIT_LINEAGE, path_params={"unitId": seeded.unit_id})
    assert status == 200 and lin["steps"][0]["kind"] == "unit"
    blocks = next(s for s in lin["steps"] if s["kind"] == "blocks")
    assert blocks["data"]["items"][0]["pointer"] == "#/tables/0" and blocks["data"]["items"][0]["role"] == "table"


def test_lineage_v1_chunk_chain(call, seeded) -> None:
    status, lin = call("GET", UNIT_LINEAGE, path_params={"unitId": seeded.chunk_id})
    assert status == 200
    assert [s["kind"] for s in lin["steps"]] == ["unit", "blocks", "source"]
    assert lin["steps"][1]["title"] == "Page text layer"
    assert lin["steps"][1]["artifacts"][0]["key"].endswith("/1.0/extract/text_layer.json")


def test_lineage_unknown_field_404_and_bad_field_400(call, seeded) -> None:
    status, _ = call("GET", UNIT_LINEAGE, path_params={"unitId": seeded.unit_id}, query={"field": "f-nope"})
    assert status == 404
    status, _ = call("GET", UNIT_LINEAGE, path_params={"unitId": seeded.unit_id}, query={"field": "BOX 1"})
    assert status == 400


def test_lineage_of_held_unit_hides_text(call, seeded) -> None:
    status, lin = call("GET", UNIT_LINEAGE, path_params={"unitId": seeded.held_unit_id})
    assert status == 200
    assert "900-12" not in str(lin)
    assert "graph" not in [s["kind"] for s in lin["steps"]]
