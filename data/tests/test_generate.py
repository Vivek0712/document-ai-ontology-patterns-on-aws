"""Generator: determinism, checked-in samples up to date, document content and gold geometry."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from docgen.common import FOOTER_TEXT, sha256_file
from docgen.packages import luhn_valid
from docgen.raster import rotate_point
from evidence_core.ids import doc_id_for
from pypdf import PdfReader

EXPECTED_PACKAGES = {"lending": "pkg-lending-01", "lending-02": "pkg-lending-02", "quarterly": "pkg-quarterly-01",
                     "tradeconf": "pkg-tradeconf-01"}
LENDING_FILES = {
    "w2_2025_maya_fictus.pdf", "w2_2025_arjun_fictus.pdf", "payslip_2026-09_maya_fictus.pdf",
    "payslip_2026-09_arjun_fictus_scan.pdf", "bank_statement_2026-09.pdf",
}


def _docs(golds):
    for key, gold in golds.items():
        for d in gold["documents"]:
            yield key, gold, d


def test_packages_and_lending_files(golds):
    assert {k: g["packageId"] for k, g in golds.items()} == EXPECTED_PACKAGES
    assert {d["fileName"] for d in golds["lending"]["documents"]} == LENDING_FILES
    for _, gold, d in _docs(golds):
        assert d["docId"] == doc_id_for(gold["packageId"], d["fileName"])


def test_regeneration_is_byte_identical_to_checked_in_samples(regenerated, samples_dir):
    for key in EXPECTED_PACKAGES:
        fresh = sorted((regenerated / key).glob("*"))
        assert fresh, key
        for path in fresh:
            checked_in = samples_dir / key / path.name
            assert checked_in.exists(), f"{checked_in} missing: run make data"
            assert sha256_file(path) == sha256_file(checked_in), f"{key}/{path.name} drifted: run make data"
    golden_repo = samples_dir.parents[1] / "eval" / "golden.jsonl"
    assert (regenerated / "golden.jsonl").read_text() == golden_repo.read_text(), "eval/golden.jsonl drifted"


def test_gold_sha_matches_files(golds, samples_dir):
    for key, _, d in _docs(golds):
        assert sha256_file(samples_dir / key / d["fileName"]) == d["sha256"]


# --------------------------------------------------------------------------- W-2


@pytest.mark.parametrize("file_name,box1,box3,box12a", [
    ("w2_2025_maya_fictus.pdf", "98400.00", "105000.00", "6600.00"),
    ("w2_2025_arjun_fictus.pdf", "71250.00", "75000.00", "3750.00"),
])
def test_w2_values_and_arithmetic(golds, file_name, box1, box3, box12a):
    d = next(x for x in golds["lending"]["documents"] if x["fileName"] == file_name)
    boxes = {k: Decimal(v) for k, v in d["attributes"]["boxes"].items()}
    assert boxes["1"] == Decimal(box1) and boxes["3"] == Decimal(box3) and boxes["12a"] == Decimal(box12a)
    assert boxes["1"] == boxes["3"] - boxes["12a"]  # 401(k) deferral: Box 1 != Box 3/5
    assert boxes["5"] == boxes["3"]
    assert boxes["4"] == (boxes["3"] * Decimal("0.062")).quantize(Decimal("0.01"))
    assert boxes["6"] == (boxes["5"] * Decimal("0.0145")).quantize(Decimal("0.01"))
    assert d["attributes"]["overlayOrder"] == ["12a", "3", "5", "1", "2", "4", "6"]


def test_lending_qualifying_income_is_169650(golds):
    gold = golds["lending"]
    assert gold["expected"]["qualifyingIncome"] == "169650.00"
    box1 = [Decimal(d["attributes"]["boxes"]["1"]) for d in gold["documents"] if d["docType"] == "doc:W2"]
    assert sum(box1) == Decimal("169650.00")


def test_w2_text_layer_detaches_values_from_box_labels(samples_dir):
    """pypdf default extract_text (the v1.0 lane) yields the label block, then a run of values."""
    text = PdfReader(samples_dir / "lending" / "w2_2025_maya_fictus.pdf").pages[0].extract_text()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    i_label = lines.index("1 Wages, tips, other compensation")
    values = [ln for ln in lines if re.fullmatch(r"[\d,]+\.\d{2}", ln)]
    assert values[:7] == ["6,600.00", "105,000.00", "105,000.00", "98,400.00", "13,776.00", "6,510.00", "1,522.50"]
    assert lines[i_label + 1] != "98,400.00"
    first_value_line = lines.index("6,600.00")
    assert first_value_line > lines.index("17 State income tax"), "values must follow the whole label block"
    # Positionally, the first value after the label block is Box 12a's, not Box 1's.
    assert values[0] != "98,400.00"


def test_scanned_payslip_has_no_text_layer(samples_dir):
    for key in ("lending", "lending-02"):
        for path in (samples_dir / key).glob("*_scan.pdf"):
            assert PdfReader(path).pages[0].extract_text().strip() == ""


# --------------------------------------------------------------------------- footer, PII


def _page_texts(path: Path) -> list[str]:
    pdf = pdfium.PdfDocument(str(path))
    try:
        return [pdf[i].get_textpage().get_text_range() for i in range(len(pdf))]
    finally:
        pdf.close()


def test_every_page_carries_the_fictitious_footer(golds, samples_dir):
    for key, _, d in _docs(golds):
        footers = {r["page"] for r in d["regions"] if r["kind"] == "footer"}
        assert footers == set(range(1, d["pageCount"] + 1)), d["fileName"]
        if d["route"] == "digital":
            for text in _page_texts(samples_dir / key / d["fileName"]):
                assert FOOTER_TEXT in " ".join(text.split())


def _aba_valid(nine: str) -> bool:
    w = [3, 7, 1] * 3
    return sum(int(c) * k for c, k in zip(nine, w, strict=True)) % 10 == 0


def test_identifier_hygiene(golds, samples_dir):
    """Identifier check: no full SSN outside the 900 series, invalid EIN prefix, account numbers
    that cannot pass as card numbers, no routing-shaped numbers."""
    for key, _, d in _docs(golds):
        if d["route"] != "digital":
            continue
        text = " ".join(_page_texts(samples_dir / key / d["fileName"]))
        for ssn in re.findall(r"\b\d{3}-\d{2}-\d{4}\b", text):
            assert ssn.startswith("9"), f"full SSN outside 900 series in {d['fileName']}"
        for ein in re.findall(r"\b\d{2}-\d{7}\b", text):
            assert ein.startswith("00-"), f"EIN with an issued prefix in {d['fileName']}"
        for nine in re.findall(r"(?<!\d)\d{9}(?!\d)", text):
            assert not _aba_valid(nine), f"routing-shaped number {nine} in {d['fileName']}"
        for acct in re.findall(r"(?<!\d)\d{14}(?!\d)", text):
            assert not luhn_valid(acct)
    for key in ("lending", "lending-02"):
        bank = next(d for d in golds[key]["documents"] if d["docType"] == "doc:BankStatement")
        header = next(r for r in bank["regions"] if r["regionId"] == "bs-account-header")
        assert header["pii"] == "held"
        acct = next(r for r in bank["regions"] if r["regionId"] == "bs-account-number")
        assert re.fullmatch(r"\d{14}", acct["value"]) and acct["pii"] == "held"
        w2s = [d for d in golds[key]["documents"] if d["docType"] == "doc:W2"]
        for w in w2s:
            ssn = next(r for r in w["regions"] if r["regionId"] == "w2-ssn")
            assert re.fullmatch(r"XXX-XX-\d{4}", ssn["value"]) and ssn["pii"] == "masked"


# --------------------------------------------------------------------------- gold geometry


def _text_in(page, b: dict, margin: float = 1.0) -> str:
    h = page.get_height()
    tp = page.get_textpage()
    return tp.get_text_bounded(left=b["x0"] - margin, bottom=h - b["y1"] - margin, right=b["x1"] + margin,
                               top=h - b["y0"] + margin)


def test_digital_gold_value_boxes_hold_their_printed_values(golds, samples_dir):
    """Canonical frame check: the text pdfium finds inside each value box (PDF points, top-left origin,
    converted to bottom-left only here) is the value the generator says it printed there."""
    checked = 0
    for key, _, d in _docs(golds):
        if d["route"] != "digital":
            continue
        pdf = pdfium.PdfDocument(str(samples_dir / key / d["fileName"]))
        try:
            for r in d["regions"]:
                if r["kind"] != "field" or "valueBbox" not in r or not r.get("value"):
                    continue
                found = " ".join(_text_in(pdf[r["page"] - 1], r["valueBbox"]).split())
                assert r["value"] in found, f"{d['fileName']} {r['regionId']}: {r['value']!r} not in {found!r}"
                checked += 1
        finally:
            pdf.close()
    assert checked > 60


def test_scan_gold_boxes_follow_the_skew(golds, samples_dir):
    """Rotated gold boxes of the scanned payslip sit on ink: each value box is markedly darker than the page."""
    for key in ("lending", "lending-02"):
        d = next(x for x in golds[key]["documents"] if x["route"] == "scan")
        pdf = pdfium.PdfDocument(str(samples_dir / key / d["fileName"]))
        img = pdf[0].render(scale=150 / 72).to_pil().convert("L")
        pdf.close()
        s = 150 / 72

        def ink(b):
            crop = img.crop((round(b["x0"] * s), round(b["y0"] * s), round(b["x1"] * s), round(b["y1"] * s)))
            px = crop.tobytes()
            return sum(1 for v in px if v < 110) / max(1, len(px))

        blank = {"x0": 100, "y0": 600, "x1": 500, "y1": 700, "page": 1}
        assert ink(blank) < 0.005
        for r in d["regions"]:
            if r["kind"] == "field" and "valueBbox" in r:
                assert ink(r["valueBbox"]) > 0.05, f"{d['fileName']} {r['regionId']} box is not on ink"
        assert d["attributes"]["skewDegrees"] == 0.8


def test_rotate_point_is_identity_at_zero_and_keeps_centre():
    assert rotate_point(10.0, 20.0, 0.0) == pytest.approx((10.0, 20.0))
    assert rotate_point(306.0, 396.0, 0.8) == pytest.approx((306.0, 396.0))


def test_quarterly_layout_matches_plan(golds):
    q = next(d for d in golds["quarterly"]["documents"] if d["fileName"] == "quarterly_report_q3_fy2025.pdf")
    assert q["pageCount"] == 16
    regions = {r["regionId"]: r for r in q["regions"]}
    assert regions["qr-table3"]["page"] == 14
    assert regions["qr-table3-caption"]["label"] == "Table 3 · Total revenue · USD millions"
    assert [regions[f"qr-table3-q{i}"]["value"] for i in range(1, 5)] == ["4,120", "4,455", "4,890", "5,010"]
    assert regions["qr-table3-footnote"]["value"] == "Q4 figures are preliminary."
    assert regions["qr-table4-part1"]["page"] == 15 and regions["qr-table4-part2"]["page"] == 16
    assert regions["qr-figure2"]["page"] == 12
    assert sum(int(regions[f"qr-fig2-{s}"]["value"].replace(",", "")) for s in ("payments", "lending", "markets")) \
        == 4890


def test_trade_confirmation_step_up_footnote_on_page_2(golds):
    d = next(x for x in golds["tradeconf"]["documents"] if x["fileName"] == "trade_confirmation_xs2031.pdf")
    regions = {r["regionId"]: r for r in d["regions"]}
    assert regions["tc-coupon"]["value"] == "4.25%" and regions["tc-coupon"]["page"] == 1
    fn = regions["tc-footnote3"]
    assert fn["page"] == 2 and fn["value"] == "6.00% from year 3 if rating < BBB-"
    assert fn["extra"]["ontologyClass"] == "fin:RatingTriggeredStepEvent"


def test_gold_jsonl_mirrors_gold_json(golds, samples_dir):
    for key, gold in golds.items():
        lines = [json.loads(x) for x in (samples_dir / key / "gold.jsonl").read_text().splitlines() if x]
        assert len(lines) == sum(len(d["regions"]) for d in gold["documents"])
        assert all(ln["packageId"] == gold["packageId"] for ln in lines)
