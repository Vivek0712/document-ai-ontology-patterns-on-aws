from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_core.config import PiiGateConfig
from evidence_core.enums import CanonicalRole, PiiFlag
from evidence_core.testing import make_ctx, make_w2_unit
from lambdas.ingest_lib.chunker import split_pages
from lambdas.ingest_lib.classifier import classify
from lambdas.ingest_lib.docling_layout import docling_to_layout
from lambdas.ingest_lib.embedder import HashEmbedder, cosine
from lambdas.ingest_lib.periods import detect_party, detect_period, period_bounds, period_display
from lambdas.ingest_lib.pii import decide
from lambdas.ingest_lib.rdf import build_abox, delete_doc_update, insert_batches
from lambdas.ingest_lib.rollup import compute_rollup
from lambdas.ingest_lib.testing import FakeComprehend, FakeConverse, scanned_payslip_response
from lambdas.ingest_lib.textract_layout import textract_to_layout

FIX = Path(__file__).parent / "fixtures"


# --- chunker (v1.0) ------------------------------------------------------------------------------

def test_chunker_respects_size_overlap_and_pages() -> None:
    words = [f"w{i}" for i in range(700)]
    pages = [(1, " ".join(words[:400])), (2, ""), (3, " ".join(words[400:]))]
    chunks = split_pages(pages, size=300, overlap=60)
    assert all(c.tokens <= 300 for c in chunks)
    assert chunks[0].pages == [1]
    assert any(3 in c.pages for c in chunks)
    assert all(2 not in c.pages for c in chunks)  # empty (scanned) page yields nothing
    # every token appears in some chunk
    seen = set(" ".join(c.text for c in chunks).split())
    assert set(words) <= seen


def test_chunker_carries_overlap_between_pieces() -> None:
    lines = "\n".join(" ".join(f"l{i}w{j}" for j in range(20)) for i in range(40))
    chunks = split_pages([(1, lines)], size=100, overlap=20)
    assert len(chunks) > 1
    first_tail = chunks[0].text.splitlines()[-1]
    assert chunks[1].text.splitlines()[0] == first_tail


def test_chunker_fills_chunks_and_overlaps_across_short_pages() -> None:
    """The naive lane's chunks: 300 tokens with 20 % overlap, also when every page is shorter than a chunk
    (a page that fits is still split on its lines, so chunks are not page-aligned)."""
    def page(n: int, lines: int) -> tuple[int, str]:
        return n, "\n".join(" ".join(f"p{n}l{i}w{j}" for j in range(10)) for i in range(lines))

    pages = [page(1, 25), page(2, 20), page(3, 25), page(4, 20)]  # 250 + 200 + 250 + 200 tokens
    chunks = split_pages(pages, size=300, overlap=60)
    assert len(chunks) >= 3
    assert all(c.tokens <= 300 for c in chunks)
    assert all(c.tokens >= 290 for c in chunks[:-1])  # filled to ~300, not cut at page ends
    assert chunks[0].pages == [1, 2]
    for a, b in zip(chunks, chunks[1:], strict=False):
        shared = a.text.split()[-60:]
        assert b.text.split()[:60] == shared  # exactly the trailing 60 tokens carry over


def test_chunker_overlap_from_long_paragraphs_uses_word_suffix() -> None:
    paras = "\n\n".join(" ".join(f"a{i}w{j}" for j in range(120)) for i in range(5))  # 120-token pieces
    chunks = split_pages([(1, paras)], size=300, overlap=60)
    assert len(chunks) >= 2
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert b.text.split()[:60] == a.text.split()[-60:]


def test_chunker_rejects_bad_overlap() -> None:
    with pytest.raises(ValueError):
        split_pages([(1, "a b c")], size=10, overlap=10)


# --- layouts ---------------------------------------------------------------------------------------

def test_docling_layout_converts_bottomleft_and_maps_roles(vocab) -> None:  # noqa: ANN001
    doc = json.loads((FIX / "w2_2025_maya_fictus.docling.json").read_text())
    layout = docling_to_layout(doc, artifact_key="silver/k/docling.json", vocab=vocab)
    roles = [b.role for b in layout.blocks]
    assert CanonicalRole.TABLE in roles and CanonicalRole.SECTION_HEADER in roles
    title = next(b for b in layout.blocks if b.role is CanonicalRole.SECTION_HEADER)
    assert title.bbox is not None and title.bbox.y0 < 100  # top of the page in the top-left frame
    footer = next(b for b in layout.blocks if b.role is CanonicalRole.PAGE_FOOTER)
    assert footer.excluded and footer.bbox.y0 > 700
    table = next(b for b in layout.blocks if b.table is not None)
    assert table.table.num_cols == 4
    assert all(c.bbox and c.bbox.y0 > 200 for c in table.table.cells)  # TOPLEFT cells unchanged


def test_docling_page_map_renumbers_subset_pages(vocab) -> None:  # noqa: ANN001
    doc = json.loads((FIX / "payslip_2026-09_maya_fictus.docling.json").read_text())
    layout = docling_to_layout(doc, artifact_key="k", vocab=vocab, page_map={1: 3})
    assert {b.page for b in layout.blocks} == {3}
    assert 3 in layout.pages


def test_textract_layout_parses_layout_and_key_values(vocab) -> None:  # noqa: ANN001
    layout = textract_to_layout(scanned_payslip_response(), page=2, page_width=612, page_height=792,
                                artifact_key="silver/k/textract/p002.json", vocab=vocab)
    kv = next(b for b in layout.blocks if b.kvs)
    assert [k.key for k in kv.kvs] == ["Gross pay", "401(k) deferral", "YTD gross"]
    assert kv.kvs[0].value == "6,500.00" and kv.kvs[0].confidence == pytest.approx(0.96)
    kb = kv.kvs[0].key_bbox
    assert kb.page == 2
    assert (kb.x0, kb.y0, kb.x1, kb.y1) == pytest.approx((0.098 * 612, 0.192 * 792, 0.218 * 612, 0.207 * 792))
    assert any(b.excluded for b in layout.blocks)  # LAYOUT_FOOTER


# --- PII hold rule -----------------------------------------------------------------------------------

def _decide(text: str) -> PiiFlag:
    unit = make_w2_unit().model_copy(update={"text": text})
    ents = FakeComprehend().detect_pii_entities(Text=text)["Entities"]
    return decide(unit, text, ents, PiiGateConfig()).decision


def test_pii_truncated_ssn_passes_masked() -> None:
    assert _decide("Employee: Maya Fictus SSN XXX-XX-0417") is PiiFlag.MASKED


def test_pii_full_account_number_is_held_and_bboxes_recorded() -> None:
    unit = make_w2_unit()
    text = "Account number 40917305583216"
    ents = FakeComprehend().detect_pii_entities(Text=text)["Entities"]
    d = decide(unit, text, ents, PiiGateConfig())
    assert d.decision is PiiFlag.HELD
    assert d.held_bboxes == unit.bboxes
    assert d.entities[0].digits == 14 and not d.entities[0].masked
    assert "40917305583216" not in d.dump_json()  # values never written


def test_pii_low_score_and_other_types_ignored() -> None:
    unit = make_w2_unit()
    ents = [{"Type": "SSN", "Score": 0.5, "BeginOffset": 0, "EndOffset": 11},
            {"Type": "NAME", "Score": 0.99, "BeginOffset": 0, "EndOffset": 4}]
    assert decide(unit, "900-12-3456", ents, PiiGateConfig()).decision is PiiFlag.NONE


def test_pii_clean_text() -> None:
    assert _decide("1 Wages, tips, other compensation 98,400.00") is PiiFlag.NONE


# --- classify -----------------------------------------------------------------------------------------

def test_classify_by_indicators(vocab) -> None:  # noqa: ANN001
    r = classify("Form W-2 Wage and Tax Statement 2025 Social security wages Medicare wages and tips",
                 vocab.doc_types, min_margin=0.15, client=None, model_id=None)
    assert r.doc_type == "doc:W2" and not r.tie_break_used and r.margin > 0.15


def test_classify_tie_break_uses_compact_model(vocab) -> None:  # noqa: ANN001
    client = FakeConverse("doc:BankStatement")
    r = classify("nothing recognisable here", vocab.doc_types, min_margin=0.15, client=client,
                 model_id="us.amazon.nova-2-lite-v1:0")
    assert r.tie_break_used and r.doc_type == "doc:BankStatement" and client.calls == 1


def test_classify_ignores_model_reply_outside_candidates(vocab) -> None:  # noqa: ANN001
    r = classify("payslip", vocab.doc_types, min_margin=0.99, client=FakeConverse("doc:Invented"),
                 model_id="m")
    assert r.doc_type == "doc:Payslip"


# --- vocab / periods ---------------------------------------------------------------------------------

def test_vocab_label_matching(vocab) -> None:  # noqa: ANN001
    assert vocab.concept_for_label("1  Wages, tips, other compensation", "doc:W2").concept == "fin:W2WagesBox1"
    assert vocab.concept_for_label("12a See instructions for box 12 D", "doc:W2").concept == "fin:W2Box12aAmount"
    assert vocab.concept_for_label("Gross pay", "doc:Payslip").concept == "fin:PayslipGrossPay"
    assert vocab.concept_for_label("Gross pay", "doc:W2") is None  # bound to another doc type
    assert vocab.role_for("textract", "layout_table") is CanonicalRole.TABLE


def test_periods_and_party() -> None:
    assert detect_period("tax_year", "Form W-2 Wage and Tax Statement 2025", "") == "TY2025"
    assert detect_period("pay_period", "", "Employee: X\nPay period 01–30 Sep 2026") == "2026-09"
    assert detect_period("quarter", "Q3 FY2025 report", "") == "FY2025-Q3"
    assert period_display("2026-09") == "Sep 2026" and period_display("TY2025") == "2025"
    assert period_bounds("2026-09") == ("2026-09-01", "2026-09-30")
    assert detect_party("Employee: Maya Fictus Pay period 01-30 Sep 2026") == "Maya Fictus"
    assert detect_party("Employee: Maya Fictus   SSN XXX-XX-0417") == "Maya Fictus"


def test_hash_embedder_is_deterministic_and_normalised() -> None:
    e = HashEmbedder()
    a, b, c = e.embed(["Gross pay 9,000.00", "Gross pay 9,000.00", "Medicare tax withheld"])
    assert a == b and cosine(a, b) == pytest.approx(1.0) and cosine(a, c) < 0.9


# --- RDF -----------------------------------------------------------------------------------------------

def test_abox_has_typed_facts_with_lineage() -> None:
    ctx = make_ctx()
    unit = make_w2_unit(ctx)
    quads, facts = build_abox([unit], package_id=ctx.package_id, pipeline_version="2.3.1", doc_id=ctx.doc_id,
                              file_name=ctx.file_name, source_sha="ab" * 32, doc_type="doc:W2",
                              stage_activity=f"urn:rp:activity:{ctx.doc_id}:2.3.1:graph")
    text = quads.nquads()
    assert facts == 1
    assert "<urn:rp:ont:fin:concept> <urn:rp:ont:fin:W2WagesBox1>" in text
    assert '"98400.00"^^<http://www.w3.org/2001/XMLSchema#decimal>' in text
    assert '"2025"^^<http://www.w3.org/2001/XMLSchema#gYear>' in text
    assert f"<urn:rp:field:{unit.unit_id}/f-box1> <http://www.w3.org/ns/prov#wasDerivedFrom> " \
           f"<urn:rp:unit:{unit.unit_id}>" in text
    assert f"<urn:rp:region:{unit.unit_id}/f-box1/p1>" in text
    assert "<urn:rp:ont:fin:borrower> <urn:rp:party:maya-fictus>" in text
    assert all(line.endswith(f"<urn:rp:graph:abox:{ctx.package_id}:2.3.1> .") for line in quads.lines)
    batches = insert_batches(quads, batch_size=10)
    assert batches[0].startswith("INSERT DATA { GRAPH <urn:rp:graph:abox:")
    assert "DELETE" in delete_doc_update(quads.graph, ctx.doc_id)


# --- rollup --------------------------------------------------------------------------------------------

def _lane(doc: str, pv: str, state: str) -> dict:
    return {"recordType": "LANE", "docId": doc, "pipelineVersion": pv, "state": state}


def _stage(doc: str, pv: str, stage: str, output: dict) -> dict:
    return {"recordType": "STAGE", "docId": doc, "pipelineVersion": pv, "stageId": stage, "state": "SUCCEEDED",
            "output": output}


def test_rollup_ready_only_when_all_lanes_succeed_and_counts_match() -> None:
    items = [_lane("a", "2.3.1", "SUCCEEDED"), _stage("a", "2.3.1", "units", {"units": 4}),
             _stage("a", "2.3.1", "pii_gate", {"held": 1, "passed": 3}), _stage("a", "2.3.1", "index", {"indexed": 3}),
             _stage("a", "2.3.1", "graph", {"triples": 40}),
             _lane("b", "2.3.1", "RUNNING"), _stage("b", "2.3.1", "units", {"units": 2})]
    lanes, counters = compute_rollup(items, ["1.0", "2.3.1"])
    assert lanes["1.0"].state == "EMPTY" and not lanes["1.0"].ready
    assert lanes["2.3.1"].state == "RUNNING" and not lanes["2.3.1"].ready
    assert counters["2.3.1"].units == 6 and counters["2.3.1"].held == 1 and counters["2.3.1"].triples == 40
    items[-2] = _lane("b", "2.3.1", "SUCCEEDED")
    items += [_stage("b", "2.3.1", "pii_gate", {"held": 0, "passed": 2}), _stage("b", "2.3.1", "index", {"indexed": 2})]
    lanes, _ = compute_rollup(items, ["2.3.1"])
    assert lanes["2.3.1"].state == "SUCCEEDED" and lanes["2.3.1"].ready
    assert lanes["2.3.1"].indexed == lanes["2.3.1"].expected == 5


def test_rollup_partial_failure() -> None:
    lanes, _ = compute_rollup([_lane("a", "1.0", "FAILED"), _lane("b", "1.0", "SUCCEEDED")], ["1.0"])
    assert lanes["1.0"].state == "PARTIAL" and not lanes["1.0"].ready
