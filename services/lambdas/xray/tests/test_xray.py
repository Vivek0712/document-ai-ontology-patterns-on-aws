"""X-ray core and handler on the real lending sample (data/samples/lending, generator seed 305).

What SC-1 relies on: alignment coverage ≥ 0.99, the 98,400.00 line lies in the gold Box 1 region, no held token
leaves the Lambda (one test per lane), and the positional pairing is a derived hypothesis that only `answerNumbers` can
confirm (docs/decisions/0005-honest-replays.md), plus the split facts.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

import pytest

from lambdas.tools._lib.errors import Forbidden
from lambdas.xray import handler as xh
from lambdas.xray.core import (
    PageChar,
    PdfPage,
    Token,
    XrayInput,
    align_page,
    build_xray,
    canonical_money,
    evidence_from_units,
    locate_chunks,
    merge_rows,
    read_pdf,
    resplit,
    tokenize,
)

REPO = Path(__file__).resolve().parents[4]
LENDING = REPO / "data" / "samples" / "lending"
E2E = REPO / "build" / "e2e"
MAYA = ("w2-2025-maya-fictus-827d37", "w2_2025_maya_fictus.pdf")
BANK = ("bank-statement-2026-09-7c2e0d", "bank_statement_2026-09.pdf")
GOLD_BOX1 = (309.0, 100.33, 433.0, 125.57)  # data/samples/lending/gold.json, w2-box1 [generator]
TEXT_LAYER_DOCS = ["w2_2025_maya_fictus.pdf", "w2_2025_arjun_fictus.pdf", "payslip_2026-09_maya_fictus.pdf",
                   "bank_statement_2026-09.pdf"]


def _units(lane: str, doc_id: str) -> list[dict[str, Any]]:
    path = E2E / lane / f"{doc_id}.units.jsonl"
    if not path.exists():
        pytest.skip("build/e2e missing: run `make e2e-local`")
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _xray(file_name: str, *, size: int = 300, doc_id: str = "doc", **kw: Any) -> dict[str, Any]:
    pages = read_pdf((LENDING / file_name).read_bytes())
    control = resplit(pages, 300, 20)
    kw.setdefault("chunk_ids", [f"{doc_id}:1.0:c-{n:02d}" for n in range(1, len(control) + 1)])
    return build_xray(XrayInput(doc_id=doc_id, page=1, pdf_pages=pages, chunk_texts=control, size_tokens=size,
                                control_size=300, overlap_pct=20, **kw))


def _line(x: dict[str, Any], text: str) -> dict[str, Any]:
    return next(ln for ln in x["lines"] if ln["text"] == text)


def _box(r: dict[str, float]) -> tuple[float, float, float, float]:
    return r["x0"], r["y0"], r["x1"], r["y1"]


# ------------------------------------------------------------------------------------------------- geometry


@pytest.mark.parametrize("file_name", TEXT_LAYER_DOCS)
def test_alignment_coverage_on_text_layer_pdfs(file_name: str) -> None:  # coverage
    x = _xray(file_name)
    assert x["coverage"] >= 0.99 and "warning" not in x
    assert all(c["coverage"] >= 0.99 for c in x["chunks"])


@pytest.mark.parametrize("file_name", TEXT_LAYER_DOCS)
def test_every_token_box_holds_that_token_on_the_page(file_name: str) -> None:
    """Per-token geometry: pdfium's own text inside each token rect contains the token (no spurious unions)."""
    import pypdfium2 as pdfium

    pdf = (LENDING / file_name).read_bytes()
    pages = read_pdf(pdf)
    tokens, _ = tokenize(pages)
    doc = pdfium.PdfDocument(pdf)
    try:
        for p in pages:
            align_page(tokens, p)
            page = doc[p.page - 1]
            tp, ph = page.get_textpage(), page.get_size()[1]
            for t in (t for t in tokens if t.page == p.page and t.box is not None):
                x0, y0, x1, y1 = t.box
                got = tp.get_text_bounded(left=x0 - 0.5, bottom=ph - y1 - 0.5, right=x1 + 0.5, top=ph - y0 + 0.5)
                assert t.text in re.sub(r"\s", "", got.replace("\x02", "-")), (t.text, got)
                assert y1 - y0 < 30, t.text  # one row
            tp.close()
            page.close()
    finally:
        doc.close()


def test_a_lone_matched_digit_never_stretches_a_token_box() -> None:
    """A one-character match that is not a whole token is ignored; a token spread over rows gets no box."""
    chars = [PageChar(c, (10.0 * i, 10.0, 10.0 * i + 8, 20.0)) for i, c in enumerate("ab")]
    chars.append(PageChar("7", (40.0, 300.0, 48.0, 310.0)))  # a "7" far down the page
    page = PdfPage(page=1, width=600, height=800, text="ab 17", chars=chars)
    tokens = [Token(index=0, page=1, line=1, text="ab"), Token(index=1, page=1, line=1, text="17")]
    coverage = align_page(tokens, page)
    assert tokens[0].box == (0.0, 10.0, 18.0, 20.0)
    assert tokens[1].box is None and tokens[1].aligned == 0
    assert coverage == pytest.approx(2 / 4)


def test_box1_value_lies_in_gold_region() -> None:  # Box 1 region
    x0, y0, x1, y1 = _box(_line(_xray(MAYA[1]), "98,400.00")["bbox"])
    gx0, gy0, gx1, gy1 = GOLD_BOX1
    assert gx0 <= x0 and gy0 <= y0 and x1 <= gx1 and y1 <= gy1


def test_payload_follows_the_spec_contract() -> None:
    x = _xray(MAYA[1], doc_id=MAYA[0])
    assert x["frame"] == "pdf-points-top-left" and x["lane"] == "1.0" and x["overlapPct"] == 20
    assert x["chunks"][0]["label"] == "chunk 827d37:c-01" and x["chunks"][0]["ordinal"] == 1
    assert {"i", "text", "rect", "chunkOrdinals", "line"} <= set(x["tokens"][0])
    assert {"ordinal", "kind", "text", "rects", "tokenStart", "tokenEnd", "chunkOrdinals"} <= set(x["lines"][0])
    assert {ln["kind"] for ln in x["lines"]} <= {"label", "value", "other"}


# ------------------------------------------------------------------------------------------------- chunks


def test_control_is_one_chunk_and_matches_the_splitter() -> None:
    x = _xray(MAYA[1])
    assert x["whatIf"] is False and x["splitterMatchesControl"] is True
    assert [c["chunkId"] for c in x["chunks"]] == ["c-01"]
    assert x["chunks"][0]["tokens"] == 179  # [local run], CAP F-1


def test_what_if_128_cuts_the_label_from_its_value() -> None:
    x = _xray(MAYA[1], size=128)
    assert x["whatIf"] is True and x["sizeTokens"] == 128
    assert [c["chunkId"] for c in x["chunks"]] == ["w-01", "w-02"]  # never a stored-looking id
    assert [c["label"] for c in x["chunks"]] == ["what-if #1", "what-if #2"]
    assert all(c["unitId"] is None for c in x["chunks"])
    assert _line(x, "1 Wages, tips, other compensation")["chunkOrdinals"] == [1]
    assert _line(x, "98,400.00")["chunkOrdinals"] == [2]


def test_stored_v1_chunks_match_the_splitter() -> None:
    stored = [u["text"] for u in _units("v1.0", MAYA[0])]
    pages = read_pdf((LENDING / MAYA[1]).read_bytes())
    assert resplit(pages, 300, 20) == stored


def test_a_chunk_that_is_not_in_the_stream_is_reported_not_raised() -> None:
    pages = read_pdf((LENDING / MAYA[1]).read_bytes())
    tokens, _ = tokenize(pages)
    spans, missing = locate_chunks(tokens, ["not on this page at all"])
    assert spans == [] and missing == [1]
    x = build_xray(XrayInput(doc_id="d", page=1, pdf_pages=pages, chunk_texts=["not on this page"], size_tokens=300,
                             control_size=300, overlap_pct=20))
    assert "not found" in x["warning"]


def test_merge_rows_joins_neighbours_on_one_row_only() -> None:
    rows = merge_rows([(0, 0, 10, 8), (12, 0, 20, 8), (0, 20, 10, 28)], gap=5, pad=0)
    assert rows == [(0, 0, 20, 8), (0, 20, 10, 28)]


# ----------------------------------------------------------------------------------- pairs, the positional hypothesis


def _maya_with_evidence(**kw: Any) -> dict[str, Any]:
    return _xray(MAYA[1], doc_id=MAYA[0], evidence=evidence_from_units(_units("v2.3.1", MAYA[0])), **kw)


def test_reading_order_lists_labels_before_values_and_pairs_by_position() -> None:
    x = _maya_with_evidence()
    kinds = [ln["kind"] for ln in x["lines"] if ln["kind"] != "other"]
    assert "label" not in kinds[kinds.index("value"):]  # constructed W-2: all box labels first, then the amounts
    box1 = next(p for p in x["pairs"] if p["concept"] == "fin:W2WagesBox1")
    assert box1["positionalProvenance"] == "derived"
    assert box1["positionalValue"]["text"] == "6,600.00"  # the first amount after the label block (12a)
    assert box1["geometricValue"]["text"] == "98,400.00"
    assert box1["sameChunk"] is True and box1["tokenDistance"] > 0  # one chunk at the 300-token control
    unit = next(u for u in x["units"] if u["unitId"] == box1["unitId"])
    assert unit["members"] and all(m["bbox"] for m in unit["members"])
    assert "answerNumbers" not in x  # no logged run: the pairing stays a hypothesis


def test_positional_comparison_is_by_value_as_well_as_by_line() -> None:
    """Box 3 by position lands on Box 5's line, which prints the same figure: same value, different line."""
    x = _maya_with_evidence()
    box3 = next(p for p in x["pairs"] if p["label"]["text"].startswith("3 "))
    assert box3["positionalValue"]["text"] == box3["geometricValue"]["text"] == "105,000.00"
    assert box3["positionalSameValue"] is True and box3["positionalSameLine"] is False
    box1 = next(p for p in x["pairs"] if p["concept"] == "fin:W2WagesBox1")
    assert box1["positionalSameValue"] is False and box1["positionalSameLine"] is False


def test_answer_numbers_confirm_the_positional_hypothesis() -> None:  # positional hypothesis, branch 1
    x = _maya_with_evidence(answer=("r-before", ["Total income is 10,350.00 = 6,600.00 + 3,750.00."]))
    used = {a["value"]: a for a in x["answerNumbers"]}
    assert used["6,600.00"]["matchedLines"] and used["6,600.00"]["isPositionalValueOf"] == "f-box1"
    assert used["10,350.00"]["matchedLines"] == []  # not on this page
    assert all(a["runId"] == "r-before" for a in x["answerNumbers"])


def test_answer_numbers_refute_the_positional_hypothesis() -> None:  # positional hypothesis, branch 2
    x = _maya_with_evidence(answer=("r-before", ["180000.00 = 105000.00 + 75000.00"]))
    used = {a["value"]: a for a in x["answerNumbers"]}
    assert "isPositionalValueOf" not in used["105,000.00"] or used["105,000.00"]["isPositionalValueOf"] != "f-box1"
    assert not any(a.get("isPositionalValueOf") == "f-box1" for a in x["answerNumbers"])
    assert used["105,000.00"]["matchedLines"]  # it does match lines on the page (Boxes 3 and 5)


def test_canonical_money() -> None:
    assert canonical_money("98400.00") == canonical_money("$98,400.00") == "98,400.00"
    assert canonical_money("n/a") is None


# ------------------------------------------------------------------------------------------------- held PII


def test_held_regions_drop_their_tokens() -> None:  # 2.3.1 lane regions
    pages = read_pdf((LENDING / BANK[1]).read_bytes())
    tokens, _ = tokenize(pages)
    acct = next(t for t in tokens if re.fullmatch(r"\d{14}", t.text))
    align_page(tokens, pages[acct.page - 1])
    x = _xray(BANK[1], held=[acct.box])
    assert x["heldTokensDropped"] >= 1
    assert not re.search(r"\d{14}", json.dumps(x))


def test_a_held_naive_chunk_is_dropped_whole() -> None:  # 1.0 lane: /units redacts the whole chunk
    x = _xray(BANK[1], doc_id=BANK[0], held_chunk_ids=[f"{BANK[0]}:1.0:c-01"])
    assert x["lines"] == [] and all(t["text"] == "[held]" for t in x["tokens"])
    assert x["heldTokensDropped"] == len(x["tokens"]) > 0


def test_an_unaligned_token_on_a_page_with_held_regions_is_dropped() -> None:  # fail closed
    pages = read_pdf((LENDING / BANK[1]).read_bytes())
    tokens, _ = tokenize(pages)
    acct = next(t for t in tokens if re.fullmatch(r"\d{14}", t.text))
    # the account number's glyphs vanish from the char stream: the aligner cannot place the token
    page = pages[acct.page - 1]
    stripped = PdfPage(page=page.page, width=page.width, height=page.height, text=page.text,
                       chars=[c for c in page.chars if c.char not in "0123456789"])
    pages[acct.page - 1] = stripped
    x = build_xray(XrayInput(doc_id="d", page=acct.page, pdf_pages=pages, chunk_texts=resplit(pages, 300, 20),
                             size_tokens=300, control_size=300, overlap_pct=20, held=[(0.0, 0.0, 1.0, 1.0)]))
    assert not re.search(r"\d{14}", json.dumps(x))
    assert x["heldTokensDropped"] >= 1


def test_scanned_page_has_no_text_layer() -> None:
    x = _xray("payslip_2026-09_arjun_fictus_scan.pdf")
    assert x["chunks"] == [] and x["lines"] == [] and x["tokens"] == []
    assert x["textLayer"] is False and x["coverage"] is None  # nothing aligned is not "100 % aligned"
    assert "warning" not in x


# ------------------------------------------------------------------------------------------------- handler


def test_whatif_sizes_come_from_the_environment() -> None:
    assert xh.whatif_sizes("") == xh.DEFAULT_WHATIF_SIZES
    assert xh.whatif_sizes("300, 96") == (300, 96)
    assert _xray(MAYA[1], what_if_sizes=(300, 96))["whatIfSizes"] == [300, 96]


def test_parse_request_validates_lane_and_size() -> None:
    ev = {"pathParameters": {"docId": MAYA[0], "pv": "1.0"},
          "queryStringParameters": {"page": "1", "size": "128", "runId": "r-1"}}
    assert xh.parse_request(ev) == (MAYA[0], 1, 128, "r-1")
    with pytest.raises(xh.BadRequest):
        xh.parse_request({**ev, "queryStringParameters": {"size": "100"}})
    with pytest.raises(xh.BadRequest):
        xh.parse_request({**ev, "pathParameters": {"docId": MAYA[0], "pv": "2.3.1"}})


class _Acl:
    package_id = "pkg-lending-01"


class _Doc:
    doc_id, file_name, bronze_key, sha256, upload_state = MAYA[0], MAYA[1], "bronze/o/pkg/w2.pdf", "6b8b", "IN_BRONZE"
    source_version_id = "v-bronze"


class _S3:
    def __init__(self) -> None:
        self.puts: dict[str, bytes] = {}
        self.versions: dict[str, str] = {}

    def get_object(self, Bucket: str, Key: str, VersionId: str | None = None) -> dict[str, Any]:  # noqa: N803
        return {"Body": io.BytesIO((LENDING / MAYA[1]).read_bytes()), "VersionId": VersionId}

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        return {"VersionId": self.versions.get(Key, "v-units")}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:  # noqa: N803
        self.puts[Key] = Body


class _Stores:
    bucket = "rp-data"

    def __init__(self, allowed: bool, run: dict[str, Any] | None = None,
                 held: dict[str, list[dict[str, float]]] | None = None) -> None:
        self.allowed, self.s3, self.run, self.held = allowed, _S3(), run, held or {}

    def require_doc_acl(self, doc_id: str, principals: list[str], message: str) -> _Acl:
        if not self.allowed:
            raise Forbidden(message)
        return _Acl()

    def read_json(self, key: str) -> Any:
        if key.endswith("held_regions.json"):
            lane = key.split("/")[3]
            return {"pages": {"1": self.held[lane]}} if lane in self.held else None
        return self.run if key.startswith("runs/") else None

    def read_text(self, key: str) -> str | None:
        lane = "v1.0" if "/1.0/" in key else "v2.3.1"
        return "\n".join(json.dumps(u) for u in _units(lane, MAYA[0]))

    def list_documents(self, package_id: str) -> list[_Doc]:
        return [_Doc()]

    def presign_get(self, key: str, expires_in: int) -> str:
        return f"https://signed/{key}?ttl={expires_in}"


def test_compute_reads_bronze_caches_in_gold_inspect_and_presigns_the_image() -> None:
    st = _Stores(allowed=True)
    x = xh.compute(st, MAYA[0], 1, 300, ["group:lending-analyst"])
    assert x["chunks"][0]["unitId"] == f"{MAYA[0]}:1.0:c-01"
    assert x["sources"]["bronze"] == {"key": "bronze/o/pkg/w2.pdf", "versionId": "v-bronze", "sha256": "6b8b"}
    assert x["sources"]["units"]["versionId"] == "v-units" and x["sources"]["mode"] == "live"
    [key] = [k for k in st.s3.puts if k.endswith(".json")]
    assert key.startswith(f"gold/pkg-lending-01/{MAYA[0]}/1.0/inspect/xray-p001-s300-")
    assert not json.loads(st.s3.puts[key])["image"].get("url")  # presigned URLs are never cached
    assert x["image"]["url"].startswith(f"https://signed/gold/pkg-lending-01/{MAYA[0]}/1.0/inspect/page-p001-")
    assert x["image"]["zoomUrl"].endswith("@2x.png?ttl=300")


def test_the_served_image_masks_both_lanes_held_regions() -> None:
    """The X-ray image is never the 1.0 Gold render alone: a region only the 2.3.1 gate held is filled too."""
    from PIL import Image

    naive_only = {"x0": 40.0, "y0": 40.0, "x1": 120.0, "y1": 60.0}
    evidence_only = {"x0": 300.0, "y0": 500.0, "x1": 420.0, "y1": 540.0}
    st = _Stores(allowed=True, held={"1.0": [naive_only], "2.3.1": [evidence_only]})
    x = xh.compute(st, MAYA[0], 1, 300, ["group:lending-analyst"])
    for key, scale in ((x["image"]["key"], 150 / 72), (x["image"]["key"].replace(".png", "@2x.png"), 300 / 72)):
        img = Image.open(io.BytesIO(st.s3.puts[key])).convert("RGB")
        for r in (naive_only, evidence_only):
            cx, cy = (r["x0"] + r["x1"]) / 2 * scale, (r["y0"] + r["y1"]) / 2 * scale
            assert img.getpixel((int(cx), int(cy))) == (0, 0, 0), (key, r)
    assert len(x["heldRegions"]) == 2


def test_cache_key_changes_when_a_pii_input_changes() -> None:
    st = _Stores(allowed=True)
    xh.compute(st, MAYA[0], 1, 300, ["group:lending-analyst"])
    before = {k for k in st.s3.puts if k.endswith(".json")}
    st.s3.versions["gold/pkg-lending-01/" + MAYA[0] + "/2.3.1/pages/held_regions.json"] = "v-regated"
    xh.compute(st, MAYA[0], 1, 300, ["group:lending-analyst"])
    after = {k for k in st.s3.puts if k.endswith(".json")}
    assert len(after - before) == 1  # a new key: the old payload is not served


def test_compute_with_a_logged_run_adds_answer_numbers() -> None:
    run = {"packageId": "pkg-lending-01", "pipelineVersion": "1.0",
           "answer": {"answer": "The total is 10,350.00.", "claims": [{"text": "6,600.00"}]}}
    x = xh.compute(_Stores(allowed=True, run=run), MAYA[0], 1, 300, ["group:lending-analyst"], run_id="r-1")
    assert any(a.get("isPositionalValueOf") for a in x["answerNumbers"])


def test_handler_denies_without_acl_and_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    ev = {"pathParameters": {"docId": MAYA[0], "pv": "1.0"}, "queryStringParameters": {},
          "requestContext": {"authorizer": {"claims": {"sub": "u-b", "cognito:groups": "markets-analyst"}}}}
    monkeypatch.setattr(xh, "stores", lambda: _Stores(allowed=False))
    assert xh.handler(ev)["statusCode"] == 403
    assert xh.handler({**ev, "requestContext": {}})["statusCode"] == 401
