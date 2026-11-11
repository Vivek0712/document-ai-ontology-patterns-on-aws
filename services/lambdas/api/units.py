"""Unit loading (Gold first, Silver fallback for held units) and held-unit redaction."""

from __future__ import annotations

from dataclasses import dataclass

from evidence_core import s3keys
from evidence_core.enums import PiiFlag
from evidence_core.ids import parse_unit_id
from evidence_core.models.units import PiiDecision, PiiDecisions, Unit

from . import store
from .deps import Deps
from .http import bad_request

HELD_TEXT = "[held: PII]"


@dataclass(frozen=True)
class LoadedUnit:
    unit: Unit
    source_key: str  # gold unit json or silver units.jsonl
    tier: str  # "gold" | "silver"


def split_unit_id(unit_id: str) -> tuple[str, str, str]:
    try:
        return parse_unit_id(unit_id)
    except ValueError as exc:
        raise bad_request(f"invalid unitId {unit_id!r}") from exc


def load_unit(deps: Deps, package_id: str, unit_id: str) -> LoadedUnit | None:
    doc_id, pv, _ = split_unit_id(unit_id)
    gold_key = s3keys.gold_unit_key(package_id, unit_id)
    data = store.read_json(deps, gold_key)
    if data is not None:
        return LoadedUnit(Unit.load(data), gold_key, "gold")
    silver_key = s3keys.silver_units_key(package_id, doc_id, pv)
    for u in store.load_units_jsonl(deps, silver_key):
        if u.unit_id == unit_id:
            return LoadedUnit(u, silver_key, "silver")
    return None


def pii_decision(deps: Deps, package_id: str, unit_id: str) -> PiiDecision | None:
    doc_id, pv, _ = split_unit_id(unit_id)
    data = store.read_json(deps, s3keys.pii_decisions_key(package_id, doc_id, pv))
    if data is None:
        return None
    return next((d for d in PiiDecisions.load(data).decisions if d.unit_id == unit_id), None)


def is_held(unit: Unit, decision: PiiDecision | None = None) -> bool:
    return unit.pii_flag is PiiFlag.HELD or (decision is not None and decision.decision is PiiFlag.HELD)


def redact(unit: Unit) -> Unit:
    """Held units never show their text: text, member text and field values replaced."""
    return unit.model_copy(update={
        "text": HELD_TEXT,
        "members": [m.model_copy(update={"text": HELD_TEXT if m.text else None}) for m in unit.members],
        "fields": [f.model_copy(update={"value": HELD_TEXT, "value_number": None}) for f in unit.fields],
        "pii_flag": PiiFlag.HELD,
    })
