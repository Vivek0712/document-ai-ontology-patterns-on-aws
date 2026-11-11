"""Golden set lines (eval/golden.jsonl, owned by A) and eval results (eval/, owned by F)."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..base import RpModel
from ..bbox import BBox

QuestionType = Literal["table", "prose", "figure", "footnote"]


class GoldenEntry(RpModel):
    qid: str  # "q-lend-01"
    type: QuestionType
    question: str
    answer: str  # display form "169,650.00"
    answer_pattern: str  # regex matched against normalised unit text / answer
    package_id: str | None = None  # "pkg-lending-01"
    # hashing.corpus_hash of the package's source PDFs (data/generate.py). A package a user creates in the
    # UI gets a new packageId, but uploading the same sample PDFs gives the same corpus hash, so the
    # golden-set check also runs there.
    corpus_hash: str | None = None
    doc_id: str | None = None  # docId as computed by ids.doc_id_for(packageId, fileName)
    file_name: str | None = None
    page: int | None = None
    gold_bbox: BBox | None = None
    headline: bool = False  # end-to-end answer check (agent check event) when True
    aliases: list[str] = Field(default_factory=list)  # other phrasings that map to this qid


_WS = re.compile(r"\s+")


def normalise_question(text: str) -> str:
    return _WS.sub(" ", text.strip().lower().rstrip("?").strip())


def normalise_amount(text: str) -> str | None:
    """``$169,650.00`` / ``169650`` -> ``169650.00``; None if not a number."""
    cleaned = re.sub(r"[^\d.\-]", "", text)
    if not cleaned:
        return None
    try:
        return f"{Decimal(cleaned):.2f}"
    except InvalidOperation:
        return None


def load_golden(path: Path) -> list[GoldenEntry]:
    out: list[GoldenEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(GoldenEntry.load(json.loads(line)))
    return out


def applies_to(entry: GoldenEntry, package_id: str | None, corpus_hash: str | None) -> bool:
    """An entry applies to its own package id, or to any package holding exactly the same source PDFs."""
    if not package_id or not entry.package_id or entry.package_id == package_id:
        return True
    return bool(corpus_hash and entry.corpus_hash and entry.corpus_hash == corpus_hash)


def match_golden(question: str, entries: list[GoldenEntry], package_id: str | None = None,
                 corpus_hash: str | None = None) -> GoldenEntry | None:
    """Exact match after normalisation on question or aliases (agent golden-set check)."""
    q = normalise_question(question)
    for e in entries:
        if not applies_to(e, package_id, corpus_hash):
            continue
        if q == normalise_question(e.question) or q in (normalise_question(a) for a in e.aliases):
            return e
    return None
