"""Reads that also return a receipt (``SourceRef``): the key, the S3 version read and the sha256 of its bytes.

The v2 inspection routes put these in ``sources`` so the UI's receipts drawer can name exactly what a payload was
built from."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from botocore.exceptions import ClientError
from evidence_core.models.inspect import SourceRef

from .deps import Deps
from .store import _missing


def read_bytes_ref(deps: Deps, key: str) -> tuple[bytes, SourceRef] | None:
    try:
        obj = deps.s3.get_object(Bucket=deps.bucket, Key=key)
    except ClientError as exc:
        if _missing(exc):
            return None
        raise
    body = obj["Body"].read()
    modified = obj.get("LastModified")
    return body, SourceRef(kind="s3", key=key, version_id=obj.get("VersionId"),
                           sha256=hashlib.sha256(body).hexdigest(),
                           at=modified.isoformat() if hasattr(modified, "isoformat") else None)


def read_json_ref(deps: Deps, key: str) -> tuple[Any, SourceRef] | None:
    got = read_bytes_ref(deps, key)
    return (json.loads(got[0]), got[1]) if got else None


def read_jsonl_ref(deps: Deps, key: str) -> tuple[list[dict[str, Any]], SourceRef] | None:
    got = read_bytes_ref(deps, key)
    if got is None:
        return None
    return [json.loads(line) for line in got[0].decode("utf-8").splitlines() if line.strip()], got[1]
