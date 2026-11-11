"""eval/golden.jsonl: shape, counts, ids, and that every gold region holds its answer."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from evidence_core.ids import doc_id_for
from evidence_core.models.golden import load_golden, match_golden, normalise_amount

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "data" / "samples"
GOLDEN = REPO / "eval" / "golden.jsonl"


@pytest.fixture(scope="module")
def entries():
    return load_golden(GOLDEN)


def test_counts_by_type(entries):
    assert len(entries) == 50
    assert Counter(e.type for e in entries) == {"table": 15, "prose": 12, "figure": 10, "footnote": 13}
    assert len({e.qid for e in entries}) == 50


def test_headline_lending_question(entries):
    e = next(x for x in entries if x.qid == "q-lend-01")
    assert e.question == "What is the total income across this package?"
    assert e.answer == "169,650.00" and e.headline and e.package_id == "pkg-lending-01"
    assert normalise_amount(e.answer) == "169650.00"
    for text in ("169,650.00", "$169,650", "169650.00", "The total is 169,650.00 USD."):
        assert re.search(e.answer_pattern, text), text
    for wrong in ("180,000.00", "169,650.50", "1,169,650.00", "169,6500"):
        assert not re.search(e.answer_pattern, wrong), wrong
    assert match_golden("what is the total income across this package", entries, "pkg-lending-01") is e
    assert match_golden("What is the total income across this package?", entries, "pkg-other") is None


def test_ids_and_answers_self_consistent(entries):
    for e in entries:
        assert e.doc_id == doc_id_for(e.package_id, e.file_name)
        assert re.search(e.answer_pattern, e.answer, flags=re.IGNORECASE), e.qid
        assert e.gold_bbox is not None and e.page == e.gold_bbox.page


def test_gold_regions_hold_the_answer_text(entries):
    """For digital documents, the text inside each question's gold bbox matches its answer pattern."""
    key_for = {"pkg-lending-01": "lending", "pkg-lending-02": "lending-02", "pkg-quarterly-01": "quarterly",
               "pkg-tradeconf-01": "tradeconf"}
    checked = 0
    for e in entries:
        if e.headline or e.file_name.endswith("_scan.pdf"):
            continue
        pdf = pdfium.PdfDocument(str(SAMPLES / key_for[e.package_id] / e.file_name))
        page = pdf[e.page - 1]
        h = page.get_height()
        b = e.gold_bbox
        text = page.get_textpage().get_text_bounded(left=b.x0 - 1, bottom=h - b.y1 - 1, right=b.x1 + 1,
                                                    top=h - b.y0 + 1)
        pdf.close()
        text = " ".join(text.replace("\x02", "-").split())  # pdfium marks a line-final hyphen as \x02
        assert re.search(e.answer_pattern, text, flags=re.IGNORECASE), (e.qid, text)
        checked += 1
    assert checked >= 40
