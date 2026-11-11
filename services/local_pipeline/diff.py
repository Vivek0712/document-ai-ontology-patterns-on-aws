"""v1.0 chunks vs v2.3.1 evidence units, per document (``make units`` output).

For every field the evidence-unit builder found, the diff checks whether any v1.0 chunk keeps the
printed label next to its value (same line, at most 60 characters between them). A detached value is
the split-table signature this repository is about: the chunk holds the number, but not what it is.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def label_attached(chunks: list[dict[str, Any]], label: str, value: str) -> bool:
    lab = re.escape(" ".join(label.split()[:4]))
    val = re.escape(value)
    rx = re.compile(rf"{lab}[^\n]{{0,60}}?{val}|{val}[^\n]{{0,20}}?{lab}", re.IGNORECASE)
    return any(rx.search(" ".join(c["text"].split(" "))) for c in chunks)


def compare(file_name: str, doc_id: str, out: Path) -> dict[str, Any]:
    chunks = _load(out / "v1.0" / f"{doc_id}.units.jsonl")
    units = _load(out / "v2.3.1" / f"{doc_id}.units.jsonl")
    fields = []
    for u in units:
        for f in u.get("fields", []):
            fields.append({
                "unitId": u["unitId"], "fieldId": f["fieldId"], "label": f["label"], "value": f["value"],
                "concept": f.get("concept"), "page": (f.get("bbox") or {}).get("page"), "hasBbox": bool(f.get("bbox")),
                "valueInV1": any(f["value"] in c["text"] for c in chunks),
                "labelAttachedInV1": label_attached(chunks, f["label"], f["value"]),
            })
    return {
        "file": file_name, "docId": doc_id,
        "v1": {"chunks": len(chunks), "meanChars": round(sum(len(c["text"]) for c in chunks) / len(chunks), 1)
               if chunks else 0, "withRegion": sum(1 for c in chunks if c.get("bboxes"))},
        "v231": {"units": len(units), "types": _count(u["unitType"] for u in units),
                 "meanChars": round(sum(len(u["text"]) for u in units) / len(units), 1) if units else 0,
                 "withRegion": sum(1 for u in units if u.get("bboxes")), "fields": len(fields),
                 "fieldsWithConcept": sum(1 for f in fields if f["concept"]),
                 "breadcrumbs": sorted({" › ".join(u.get("breadcrumb", [])) for u in units if u.get("breadcrumb")})},
        "fields": fields,
        "detachedInV1": [f["fieldId"] for f in fields
                         if f["concept"] and f["valueInV1"] and not f["labelAttachedInV1"]],
        "missingInV1": [f["fieldId"] for f in fields if f["concept"] and not f["valueInV1"]],
    }


def _count(items: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def write_diff(out: Path, docs: list[tuple[str, str]], run_info: dict[str, Any]) -> str:
    rows = [compare(f, d, out) for f, d in docs]
    (out / "diff.json").write_text(json.dumps({"run": run_info, "documents": rows}, indent=1))
    lines = ["# v1.0 chunks vs v2.3.1 evidence units", "",
             f"Source: {run_info['source']}  ", f"Docling: {run_info['docling']}  ",
             f"Scanned pages: {run_info['scanPages']}  ", f"Phase B embeddings: {run_info['phaseBEmbeddings']}  ",
             f"Vocab: {run_info['vocab']}", "",
             "| document | v1.0 chunks | v2.3.1 units | fields (with concept) | units with region | "
             "concept fields detached in v1.0 | concept values missing in v1.0 |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        v1, v2 = r["v1"], r["v231"]
        lines.append(f"| {r['file']} | {v1['chunks']} | {v2['units']} {v2['types']} | {v2['fields']} "
                     f"({v2['fieldsWithConcept']}) | {v2['withRegion']}/{v2['units']} | "
                     f"{', '.join(r['detachedInV1']) or '-'} | {', '.join(r['missingInV1']) or '-'} |")
    lines += ["", "Detached = the value is in a v1.0 chunk but its printed label is not beside it on the same line.",
              "Missing = no v1.0 chunk contains the value (for example a scanned page with no text layer).", ""]
    for r in rows:
        lines.append(f"## {r['file']} ({r['docId']})")
        for f in r["fields"]:
            mark = "attached" if f["labelAttachedInV1"] else ("detached" if f["valueInV1"] else "missing")
            lines.append(f"- `{f['fieldId']}` {f['label']} = {f['value']} · {f['concept'] or 'no concept'} · "
                         f"p{f['page']} bbox {'yes' if f['hasBbox'] else 'no'} · v1.0: {mark}")
        lines.append("")
    text = "\n".join(lines)
    (out / "diff.md").write_text(text)
    return "\n".join(lines[:len(rows) + 11])
