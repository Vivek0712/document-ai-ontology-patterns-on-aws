"""Citation validator.

The model proposes claims ``{text, value, unitId, fieldId}``. A claim is kept only when its
unit (and field) came back from a tool in this session and its value matches what the tool
returned for that field (or appears in the unit text when the unit has no fields). Kept
claims get ``docId, fileName, page, bbox, unitBbox, label, resolvable`` from the ledger.

A figure in ``answer``/``arithmetic``/``total`` is accounted for when it is the value of a
kept claim, the result of an arithmetic line whose operands are all kept claims, or a
``graph_hop`` total whose operands are all kept claims. Anything else is uncited.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from evidence_core.models.answer import AnswerPayload, Claim

from .ledger import FieldInfo, ToolLedger, UnitInfo
from .numbers import evaluate_arithmetic, figures, norm

NO_REGION_CHUNK = "chunk has no page region"
NO_REGION_SESSION = "region not retrieved in session"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


class AnswerParseError(ValueError):
    pass


def parse_answer_json(text: str) -> dict[str, Any]:
    """The model's final message must be one JSON object (Output in prompts/answer_v1.txt); tolerate fences/prose."""
    cleaned = _FENCE.sub("", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise AnswerParseError("no JSON object in the model reply")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as e:
        raise AnswerParseError(f"model reply is not valid JSON: {e.msg}") from e
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        raise AnswerParseError("model reply has no 'answer' string")
    return data


@dataclass
class Dropped:
    claim: dict[str, Any]
    reason: str


@dataclass
class Validation:
    payload: AnswerPayload
    uncited: list[str]
    dropped: list[Dropped] = field(default_factory=list)

    @property
    def needs_regions(self) -> list[Claim]:
        """Kept, region-capable claims whose bbox no tool has returned yet."""
        return [c for c in self.payload.claims if c.bbox is None and c.chunk_label is None]


def _short_field_label(f: FieldInfo) -> str:
    label = (f.label or f.field_id).strip()
    m = re.match(r"^(\d{1,2}[a-z]?)\s+\S", label)
    return f"Box {m.group(1)}" if m else label


def _unit_figures(unit: UnitInfo) -> set[str]:
    return {n for n in (norm(x) for x in figures(unit.text)) if n}


def _check(raw: dict[str, Any], ledger: ToolLedger) -> tuple[Claim | None, str | None]:
    unit_id = raw.get("unitId")
    field_id = raw.get("fieldId") or None
    text = str(raw.get("text") or raw.get("value") or "").strip()
    if not isinstance(unit_id, str) or not text:
        return None, "claim has no unitId or text"
    unit = ledger.units.get(unit_id)
    if unit is None:
        return None, "unit not returned by a tool in this session"
    value = norm(str(raw.get("value") or "")) or norm(text)
    f: FieldInfo | None = None
    if field_id:
        f = unit.fields.get(field_id)
        if f is None:
            return None, "field not returned by a tool in this session"
        expected = f.value_number and norm(f.value_number) or norm(f.value)
        if expected is not None and value != expected:
            return None, f"value {value} does not match the field ({expected})"
    elif value is not None and value not in _unit_figures(unit):
        return None, f"value {value} does not appear in the unit text"

    claim = Claim(text=text, value=value, unit_id=unit_id, field_id=field_id, doc_id=unit.doc_id,
                  file_name=unit.file_name, resolvable=False)
    if unit.is_chunk:
        claim.chunk_label = unit.chunk_label
        claim.label = unit.chunk_label or unit.label
        claim.reason = NO_REGION_CHUNK
        return claim, None
    unit_bbox = unit.bboxes[0] if unit.bboxes else None
    bbox = (f.bbox if f else None) or unit_bbox
    claim.unit_bbox = unit_bbox
    claim.bbox = bbox
    claim.page = bbox.page if bbox else None
    claim.resolvable = bbox is not None
    parts = [p for p in (unit.label, _short_field_label(f) if f else None) if p]
    if bbox:
        parts.append(f"p.{bbox.page}")
    claim.label = " · ".join(parts) or unit_id
    if bbox is None:
        claim.reason = NO_REGION_SESSION
    return claim, None


def validate(raw: dict[str, Any], ledger: ToolLedger) -> Validation:
    claims: list[Claim] = []
    dropped: list[Dropped] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for item in raw.get("claims") or []:
        if not isinstance(item, dict):
            dropped.append(Dropped({"claim": item}, "claim is not an object"))
            continue
        claim, reason = _check(item, ledger)
        if claim is None:
            dropped.append(Dropped(item, reason or "invalid"))
            continue
        key = (claim.unit_id, claim.field_id, claim.value)
        if key not in seen:
            seen.add(key)
            claims.append(claim)

    cited = {c.value for c in claims if c.value}
    pairs = {(c.unit_id, c.field_id) for c in claims}
    arithmetic = raw.get("arithmetic") if isinstance(raw.get("arithmetic"), str) else None
    arith = evaluate_arithmetic(arithmetic)
    if arith and arith[2] and all(op in cited for op in arith[0]):
        cited.add(arith[1])
    for gt in ledger.graph_totals:
        if all(p in pairs for p in gt.operands) and (n := norm(gt.total)):
            cited.add(n)

    total = raw.get("total") if isinstance(raw.get("total"), str) else None
    uncited: list[str] = []
    for fig in figures(raw.get("answer")) + figures(arithmetic) + figures(total):
        n = norm(fig)
        if n and n not in cited and fig not in uncited:
            uncited.append(fig)

    caveats = [str(c) for c in raw.get("caveats") or [] if str(c).strip()]
    payload = AnswerPayload(
        answer=str(raw.get("answer", "")).strip(),
        total=total,
        currency=raw.get("currency") if isinstance(raw.get("currency"), str) else None,
        arithmetic=arithmetic,
        claims=claims,
        caveats=caveats,
        uncited_numbers=uncited,
    )
    return Validation(payload=payload, uncited=uncited, dropped=dropped)


def retry_prompt(v: Validation) -> str:
    lines = ["Your previous reply had figures without a valid citation:"]
    lines += [f"- {u}" for u in v.uncited]
    if v.dropped:
        lines.append("Citations that were rejected:")
        lines += [f"- {d.claim.get('unitId')} {d.claim.get('fieldId') or ''}: {d.reason}" for d in v.dropped[:6]]
    lines.append(
        "Reply again with one JSON object in the same format. Cite every figure with a unitId and fieldId exactly "
        "as a tool returned them, with the value the tool returned. If you cannot cite a figure, remove it."
    )
    return "\n".join(lines)
