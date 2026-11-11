from __future__ import annotations

import copy

import pytest
from agent_fixtures import CHUNK, GOOD_ANSWER, LEO, MAYA, chunk_unit, graph_payload, retrieved, search_payload
from rp_agent.citations import AnswerParseError, parse_answer_json, retry_prompt, validate
from rp_agent.ledger import ToolLedger
from rp_agent.numbers import evaluate_arithmetic, figures, norm


def test_figures_ignore_ids_years_and_box_numbers():
    text = "W-2 Box 1 for TY2025 (2025) on p.1, unit eu-01: $98,400.00 and 71250 and 3.5% and 12"
    assert figures(text) == ["98,400.00", "71250", "3.5"]
    assert norm("$169,650") == "169650.00" and norm("abc") is None


def test_arithmetic():
    assert evaluate_arithmetic("98,400.00 + 71,250.00 = 169,650.00") == (["98400.00", "71250.00"], "169650.00", True)
    assert evaluate_arithmetic("98,400.00 + 71,250.00 = 170,000.00")[2] is False
    assert evaluate_arithmetic("no maths here") is None


def ledger_v2() -> ToolLedger:
    led = ToolLedger()
    led.record("search_units", search_payload([retrieved(MAYA, "Maya Fictus", "98,400.00", 1),
                                               retrieved(LEO, "Leo Example", "71,250.00", 2)]))
    return led


def test_valid_claims_get_geometry_from_tools_not_model():
    raw = copy.deepcopy(GOOD_ANSWER)
    raw["claims"][0]["page"] = 9  # model-supplied geometry is ignored
    raw["claims"][0]["bbox"] = {"page": 9, "x0": 0, "y0": 0, "x1": 1, "y1": 1}
    v = validate(raw, ledger_v2())
    assert v.uncited == [] and not v.dropped
    c = v.payload.claims[0]
    assert c.page == 1 and c.bbox.x0 == 50 and c.bbox.x1 == 306 and c.unit_bbox.x1 == 562
    assert c.resolvable and c.label == "W-2 · Maya Fictus · Boxes 1–6 · Box 1 · p.1"
    assert c.doc_id == MAYA.split(":")[0]


def test_unknown_unit_wrong_value_and_wrong_field_are_dropped():
    raw = copy.deepcopy(GOOD_ANSWER)
    raw["claims"] = [
        {"text": "98,400.00", "unitId": "made-up-aaaaaa:2.3.1:eu-09", "fieldId": "f-box1"},
        {"text": "71,250.00", "value": "71250.00", "unitId": MAYA, "fieldId": "f-box1"},
        {"text": "98,400.00", "unitId": MAYA, "fieldId": "f-box9"},
    ]
    v = validate(raw, ledger_v2())
    assert v.payload.claims == []
    assert [d.reason.split(" ")[0] for d in v.dropped] == ["unit", "value", "field"]
    assert v.uncited == ["169,650.00", "98,400.00", "71,250.00"]
    assert "169,650.00" in retry_prompt(v)


def test_total_needs_cited_operands_or_graph_total():
    raw = copy.deepcopy(GOOD_ANSWER)
    raw["arithmetic"] = None
    raw["answer"] = "Total qualifying income is 169,650.00 USD."
    v = validate(raw, ledger_v2())
    assert v.uncited == ["169,650.00"]  # nothing derives it
    led = ledger_v2()
    led.record("graph_hop", graph_payload())
    assert validate(raw, led).uncited == []  # graph total over the two cited fields


def test_wrong_arithmetic_is_not_accepted():
    raw = copy.deepcopy(GOOD_ANSWER)
    raw["arithmetic"] = "98,400.00 + 71,250.00 = 170,000.00"
    raw["total"] = "170,000.00"
    v = validate(raw, ledger_v2())
    assert "170,000.00" in v.uncited


def test_chunk_claims_are_not_resolvable():
    led = ToolLedger()
    led.record("search_units", search_payload([chunk_unit()], "1.0"))
    raw = {"answer": "Wages were 98,400.00.", "claims": [{"text": "98,400.00", "unitId": CHUNK}]}
    v = validate(raw, led)
    (c,) = v.payload.claims
    assert not c.resolvable and c.chunk_label == "chunk c-03" and c.reason == "chunk has no page region"
    assert c.bbox is None and v.uncited == []
    raw = {"answer": "Wages were 12,345.00.", "claims": [{"text": "12,345.00", "unitId": CHUNK}]}
    assert validate(raw, led).payload.claims == []  # value not in the chunk text


def test_graph_only_claim_needs_region():
    led = ToolLedger()
    led.record("graph_hop", graph_payload())
    v = validate(GOOD_ANSWER, led)
    assert len(v.payload.claims) == 2 and all(c.bbox is None for c in v.payload.claims)
    assert len(v.needs_regions) == 2 and v.payload.claims[0].reason == "region not retrieved in session"


def test_parse_answer_json():
    assert parse_answer_json('```json\n{"answer": "x"}\n```') == {"answer": "x"}
    assert parse_answer_json('Here you go: {"answer": "y", "claims": []}') == {"answer": "y", "claims": []}
    with pytest.raises(AnswerParseError):
        parse_answer_json("no json")
    with pytest.raises(AnswerParseError):
        parse_answer_json('{"total": 1}')
