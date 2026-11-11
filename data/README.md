# data

A deterministic generator for fictitious PDFs, and the packages it produced. The documents are built to make the
failure modes of naive RAG visible, and every region the generator draws is recorded, so answers can be checked
against an exact box on a page.

> **All documents are fictitious.** "NewAge Global Fintech" and people with the surname "Fictus" do not exist.
> Every page carries the footer `Fictitious document · NewAge Global Fintech · for demonstration only`. SSNs are
> printed truncated or in unissued ranges, EINs use the unissued `00-` prefix, and no routing number is printed.

```bash
make data                                                            # regenerate data/samples/ and eval/golden.jsonl
.venv/bin/python data/preview.py data/samples/lending --out build/preview     # page images with the gold boxes drawn
```

The generator uses fixed seeds and reportlab's invariant mode, so a rerun is byte-identical. A test fails when the
checked-in samples drift from the generator. Note that `make data` overwrites `eval/golden.jsonl`.

## Packages

| Folder | Package | Documents |
|---|---|---|
| `samples/lending/` | `pkg-lending-01` | Two 2025 W-2s, two September 2026 payslips (one a simulated 150 dpi scan with 0.8° skew, noise and no text layer), a two-page bank statement |
| `samples/lending-02/` | `pkg-lending-02` | A seeded variant; the bank statement has a titled balance chart with data labels |
| `samples/quarterly/` | `pkg-quarterly-01` | Three quarterly reports (Q3 FY2023 to FY2025) |
| `samples/tradeconf/` | `pkg-tradeconf-01` | Three bond trade confirmations (XS-2031 to XS-2033) |

## Built on purpose

- **W-2 drawing order.** Labels are drawn first and values afterwards, in the order 12a, 3, 5, 1, 2, 4, 6. A plain
  `extract_text()` returns the labels followed by a run of values, so values arrive detached from their boxes.
  Geometry-aware extraction keeps each value in its box.
- **401(k) deferral.** Box 1 = Box 3 − Box 12a (code D), so Box 1 differs from Boxes 3 and 5. Qualifying income for
  `pkg-lending-01` is 98,400.00 + 71,250.00 = **169,650.00**.
- **A scanned payslip.** No text layer: the naive lane has nothing to chunk; the evidence-unit lane routes it to OCR.
- **One full identifier.** The bank statement header prints a 14-digit account number, which the PII gate holds.
- **Quarterly reports.** Table 3 (total revenue) with footnote 1 "Q4 figures are preliminary."; Figure 2 (revenue
  by segment, printed labels); Table 4 split across pages 15 and 16 with its footnote on page 16.
- **Trade confirmations.** `Coupon 4.25%` with a raised marker 3 on page 1; footnote 3 on page 2:
  "6.00% from year 3 if rating < BBB-".

## Gold regions

`gold.json` in each package holds the package id, name and allowed groups, the documents, and every region the
generator drew, in one frame (PDF points, top-left origin, 1-based pages): fields (label, value and cell boxes,
concept, field id, period, borrower), units, tables, captions, footnotes, markers, figures and footers.
`gold.jsonl` has the same regions one per line. Document ids are `doc_id_for(packageId, fileName)` from
`evidence_core.ids`.

`eval/golden.jsonl` holds the 50 golden questions; each `goldBbox` is a generator region.

## Tests

```bash
.venv/bin/python -m pytest data
```
