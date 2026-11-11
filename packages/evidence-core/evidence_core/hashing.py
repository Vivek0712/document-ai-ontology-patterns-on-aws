"""Canonical hashing used for configSha, blockShas, promptSha and corpusHash."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def corpus_hash(doc_shas: Iterable[str]) -> str:
    """Hash of a package's corpus: sha256 over the sorted source-object sha256 list."""
    return sha256_text("\n".join(sorted(doc_shas)))


def short_sha(sha: str, n: int = 4) -> str:
    """Display form, e.g. ``1c9e…``."""
    return f"{sha[:n]}…"
