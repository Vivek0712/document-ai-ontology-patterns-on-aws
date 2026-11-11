"""PII gate rule ``rp-pii-v1`` over Amazon Comprehend ``DetectPiiEntities``.

A unit is HELD when Comprehend returns one of ``entityTypes`` with score >= ``minScore`` and the
matched span is unmasked: at least ``unmaskedMinDigits[type]`` digits (9 for SSN, 8 for an account
number, ...). Masked/truncated spans (``XXX-XX-0417``) pass with ``piiFlag: masked``.

Values never leave this module: decisions carry entity type, score, offsets and digit counts only,
and nothing here logs text.
"""

from __future__ import annotations

from typing import Any

from evidence_core.bbox import BBox
from evidence_core.config import PiiGateConfig
from evidence_core.enums import PiiFlag
from evidence_core.models.units import PiiDecision, PiiEntityHit, Unit

COMPREHEND_MAX_BYTES = 100_000  # DetectPiiEntities limit per request (UTF-8 bytes)


def _windows(text: str, max_bytes: int = COMPREHEND_MAX_BYTES) -> list[tuple[int, str]]:
    """Split text into (offset, window) pieces under the byte limit, on whitespace."""
    if len(text.encode("utf-8")) <= max_bytes:
        return [(0, text)]
    out: list[tuple[int, str]] = []
    start = 0
    step = max_bytes // 4  # worst case 4 bytes per char
    while start < len(text):
        end = min(len(text), start + step)
        if end < len(text):
            space = text.rfind(" ", start, end)
            end = space if space > start else end
        out.append((start, text[start:end]))
        start = end
    return out


def detect_entities(client: Any, text: str, language_code: str = "en") -> list[dict[str, Any]]:
    """Raw Comprehend entities with offsets relative to ``text``."""
    entities: list[dict[str, Any]] = []
    for offset, window in _windows(text):
        if not window.strip():
            continue
        resp = client.detect_pii_entities(Text=window, LanguageCode=language_code)
        for e in resp.get("Entities", []):
            entities.append({**e, "BeginOffset": e["BeginOffset"] + offset, "EndOffset": e["EndOffset"] + offset})
    return entities


def decide(unit: Unit, text: str, entities: list[dict[str, Any]], cfg: PiiGateConfig) -> PiiDecision:
    hits: list[PiiEntityHit] = []
    for e in entities:
        etype = str(e.get("Type"))
        score = float(e.get("Score", 0.0))
        if etype not in cfg.entity_types or score < cfg.min_score:
            continue
        begin, end = int(e["BeginOffset"]), int(e["EndOffset"])
        span = text[begin:end]
        digits = sum(ch.isdigit() for ch in span)
        threshold = cfg.unmasked_min_digits.get(etype, 8)
        hits.append(PiiEntityHit(type=etype, score=round(score, 4), begin=begin, end=end, digits=digits,
                                 masked=digits < threshold))
    if any(not h.masked for h in hits):
        decision = PiiFlag.HELD
    elif hits:
        decision = PiiFlag.MASKED
    else:
        decision = PiiFlag.NONE
    held_boxes: list[BBox] = list(unit.bboxes) if decision is PiiFlag.HELD else []
    return PiiDecision(unit_id=unit.unit_id, decision=decision, rule=cfg.rule, entities=hits,
                       held_bboxes=held_boxes)
