"""Document typing (stage ``classify``): SKOS DocumentType indicator phrases from the vocab snapshot,
with an Amazon Nova 2 Lite tie-break (Bedrock Converse) when the top two scores are closer than
``minMargin``. The model only chooses among the candidate concepts it is shown."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from evidence_core.models.units import VocabDocType

from .vocab import normalise_label

EXCERPT_CHARS = 4000


@dataclass
class Classification:
    doc_type: str
    label: str
    scores: dict[str, float]
    margin: float
    tie_break_used: bool
    tie_break_model: str | None = None
    hits: dict[str, list[str]] | None = None
    tie_break_reply: str | None = None


def score(text: str, doc_types: list[VocabDocType]) -> tuple[dict[str, float], dict[str, list[str]]]:
    """Fraction of each type's indicator phrases (and labels) present in the normalised text."""
    norm = normalise_label(text)
    scores: dict[str, float] = {}
    hits: dict[str, list[str]] = {}
    for dt in doc_types:
        phrases = list(dict.fromkeys(dt.indicators))
        found = [p for p in phrases if normalise_label(p) and normalise_label(p) in norm]
        label_hit = any(normalise_label(lbl) in norm for lbl in [dt.pref_label, *dt.alt_labels] if lbl)
        s = len(found) / len(phrases) if phrases else 0.0
        if label_hit:
            s = min(1.0, s + 0.1)
        scores[dt.concept] = round(s, 4)
        hits[dt.concept] = found
    return scores, hits


def tie_break(client: Any, model_id: str, text: str, candidates: list[VocabDocType]) -> tuple[str | None, str]:
    """Ask the compact model to pick one candidate concept. Returns (concept or None, raw reply)."""
    lines = [f"- {c.concept}: {c.pref_label} (indicators: {', '.join(c.indicators[:8])})" for c in candidates]
    prompt = (
        "Classify the document excerpt as exactly one of these document types.\n"
        + "\n".join(lines)
        + "\nAnswer with the concept id only (for example doc:W2).\n\nExcerpt:\n"
        + text[:EXCERPT_CHARS]
    )
    resp = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"temperature": 0, "maxTokens": 20},
    )
    reply = "".join(p.get("text", "") for p in resp["output"]["message"]["content"]).strip()
    ids = {c.concept for c in candidates}
    m = re.search(r"[a-z]+:[A-Za-z0-9_]+", reply)
    return (m.group(0) if m and m.group(0) in ids else None), reply


def classify(text: str, doc_types: list[VocabDocType], *, min_margin: float, client: Any | None,
             model_id: str | None) -> Classification:
    if not doc_types:
        raise ValueError("vocab snapshot has no document types")
    scores, hits = score(text, doc_types)
    ranked = sorted(doc_types, key=lambda d: (-scores[d.concept], d.concept))
    top = ranked[0]
    second = scores[ranked[1].concept] if len(ranked) > 1 else 0.0
    margin = round(scores[top.concept] - second, 4)
    chosen, used, reply = top, False, None
    if margin < min_margin and client is not None and model_id:
        cands = [d for d in ranked if scores[d.concept] > 0][:3] or ranked[:3]
        picked, reply = tie_break(client, model_id, text, cands)
        used = True
        if picked:
            chosen = next(d for d in doc_types if d.concept == picked)
    return Classification(doc_type=chosen.concept, label=chosen.short_label, scores=scores, margin=margin,
                          tie_break_used=used, tie_break_model=model_id if used else None, hits=hits,
                          tie_break_reply=reply)
