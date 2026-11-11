"""SKOS vocabulary snapshot lookups (config/ontology/{ver}/vocab.json).

- engine label -> canonical role (``roles[].engineLabels``; case-insensitive)
- printed field label -> field concept (``fieldConcepts[].altLabels`` + ``prefLabel``; SKOS altLabel
  matching with whitespace/case/punctuation normalised)
- document type indicators for ``classify``

Adding a parser or a label variant is a vocabulary change (a new ontologyVersion), never code.
"""

from __future__ import annotations

import functools
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from evidence_core import s3keys
from evidence_core.config import ConfigSource, default_source
from evidence_core.enums import CanonicalRole
from evidence_core.models.units import VocabDocType, VocabFieldConcept, VocabSnapshot

_WS = re.compile(r"\s+")
_PUNCT_EDGE = re.compile(r"^[\s:;,.\-–—]+|[\s:;,.\-–—]+$")
_NOTATION = re.compile(r"^(?:box\s*)?(?P<n>\d{1,2}[a-z]?)\b[\s.:)\-–]*", re.IGNORECASE)


def normalise_label(text: str) -> str:
    """Case-fold, unify dashes/quotes, collapse whitespace, trim edge punctuation."""
    t = unicodedata.normalize("NFKC", text).casefold()
    t = t.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    t = _WS.sub(" ", t)
    return _PUNCT_EDGE.sub("", t)


def split_notation(label: str) -> tuple[str | None, str]:
    """``"1 Wages, tips"`` -> (``"1"``, ``"Wages, tips"``); ``"Box 12a"`` -> (``"12a"``, ``""``)."""
    m = _NOTATION.match(label.strip())
    if not m:
        return None, label.strip()
    return m.group("n").lower(), label.strip()[m.end():].strip()


@dataclass
class VocabIndex:
    snapshot: VocabSnapshot
    _roles: dict[tuple[str, str], CanonicalRole] = field(default_factory=dict)
    _concepts_exact: dict[str, list[VocabFieldConcept]] = field(default_factory=dict)
    _concepts_pref_tail: dict[str, list[VocabFieldConcept]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for role in self.snapshot.roles:
            for engine, labels in role.engine_labels.items():
                for label in labels:
                    self._roles[(engine.lower(), label.lower())] = role.role
        for fc in self.snapshot.field_concepts:
            for lab in [fc.pref_label, *fc.alt_labels]:
                self._concepts_exact.setdefault(normalise_label(lab), []).append(fc)
            self._concepts_pref_tail.setdefault(normalise_label(fc.pref_label), []).append(fc)

    @property
    def ontology_version(self) -> str:
        return self.snapshot.ontology_version

    # --- roles -------------------------------------------------------------------------------
    def role_for(self, engine: str, label: str) -> CanonicalRole | None:
        return self._roles.get((engine.lower(), label.lower()))

    # --- doc types -----------------------------------------------------------------------------
    @property
    def doc_types(self) -> list[VocabDocType]:
        return self.snapshot.doc_types

    def doc_type(self, concept: str | None) -> VocabDocType | None:
        if not concept:
            return None
        return next((d for d in self.snapshot.doc_types if d.concept == concept), None)

    # --- field concepts --------------------------------------------------------------------------
    def concept_for_label(self, label: str, doc_type: str | None = None) -> VocabFieldConcept | None:
        """Match a printed label to a field concept, preferring the document's type.

        Order: exact altLabel/prefLabel; then the label without its leading box notation
        against the labels; then a box notation unique within the document type (``"12a ..."`` on
        a W-2 -> Box 12a).
        """
        norm = normalise_label(label)
        if not norm:
            return None
        hit = self._pick(self._concepts_exact.get(norm, []), doc_type)
        if hit:
            return hit
        notation, tail = split_notation(label)
        if tail:
            hit = self._pick(self._concepts_exact.get(normalise_label(tail), []), doc_type, notation)
            if hit:
                return hit
        if doc_type:  # a text-layer run can merge two printed labels: "e Employee's name ... f Employee's address"
            prefixed = [(len(alt), fc) for fc in self.snapshot.field_concepts if fc.doc_type == doc_type
                        for alt in map(normalise_label, [fc.pref_label, *fc.alt_labels])
                        if len(alt) >= 12 and norm.startswith(alt + " ")]
            if prefixed:
                return max(prefixed, key=lambda x: x[0])[1]
        if notation and doc_type:
            cands = [fc for fc in self.snapshot.field_concepts
                     if fc.doc_type == doc_type and (fc.notation or "").lower() == notation]
            if len(cands) == 1:
                return cands[0]
        return None

    @staticmethod
    def _pick(cands: list[VocabFieldConcept], doc_type: str | None,
              notation: str | None = None) -> VocabFieldConcept | None:
        if not cands:
            return None
        if notation:
            with_notation = [c for c in cands if (c.notation or "").lower() == notation]
            cands = with_notation or cands
        if doc_type:
            same = [c for c in cands if c.doc_type == doc_type]
            if same:
                return same[0]
            # a concept bound to another document type must not be applied here
            return None
        return cands[0] if len(cands) == 1 else None

    def concept(self, concept: str) -> VocabFieldConcept | None:
        return next((c for c in self.snapshot.field_concepts if c.concept == concept), None)


def parse_vocab(text: str) -> VocabIndex:
    return VocabIndex(VocabSnapshot.model_validate(json.loads(text)))


@functools.lru_cache(maxsize=4)
def _load_cached(ontology_version: str) -> VocabIndex:
    text, _ = default_source().read_text(s3keys.vocab_key(ontology_version))
    return parse_vocab(text)


def load_vocab(ontology_version: str, source: ConfigSource | None = None, *, path: Path | None = None) -> VocabIndex:
    """Vocab snapshot for an ontology version (S3 in AWS, ontology/dist locally, or an explicit file).

    ``RP_VOCAB_PATH`` (dev/test only) points at a vocab.json to use instead of the config source.
    """
    override = os.environ.get("RP_VOCAB_PATH")
    if path is None and source is None and override:
        path = Path(override)
    if path is not None:
        return parse_vocab(path.read_text(encoding="utf-8"))
    if source is not None:
        text, _ = source.read_text(s3keys.vocab_key(ontology_version))
        return parse_vocab(text)
    return _load_cached(ontology_version)
