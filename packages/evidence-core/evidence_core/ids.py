"""Identifier schemes and principal strings.

- packageId: ``pkg-<slug>`` (seeded demo package: ``pkg-lending-01``). Max 48 chars.
- docId: ``<slug(file stem)[:40]>-<sha256(packageId + "/" + fileName)[:6]>``. Deterministic
  per (package, file name); re-uploading the same name re-ingests the same docId.
  Globally unique in practice, so DocStatus/DocAcl are keyed by docId alone.
- unitId: ``<docId>:<pipelineVersion>:<local>``; local is ``eu-01`` (v2.3.1 evidence units)
  or ``c-01`` (v1.0 chunks). Never reused across versions.
- fieldId: ``f-<slug>`` unique within the unit (``f-box1``, ``f-ytd-gross``).
- runId: ``r-<12 hex>`` (UI shows the first 8 chars, e.g. ``r-7f3a21``).
- Principals: ``user:<cognito sub>``, ``group:<cognito group>``.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from collections.abc import Iterable

_SLUG_RE = re.compile(r"[^a-z0-9]+")
PACKAGE_ID_RE = re.compile(r"^pkg-[a-z0-9][a-z0-9-]{0,43}$")
DOC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}-[0-9a-f]{6}$|^[a-z0-9][a-z0-9-]{1,47}$")
PIPELINE_VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?$")
UNIT_ID_RE = re.compile(r"^(?P<doc>[a-z0-9-]+):(?P<pv>\d+\.\d+(?:\.\d+)?):(?P<local>(?:eu|c)-\d{2,4})$")
FIELD_ID_RE = re.compile(r"^f-[a-z0-9-]{1,40}$")
RUN_ID_RE = re.compile(r"^r-[0-9a-f]{6,32}$")


def slugify(text: str, max_len: int = 40) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return (slug[:max_len].rstrip("-")) or "x"


def package_id_from_name(name: str) -> str:
    """``pkg-<slug>-<4 hex>`` for user-created packages."""
    return f"pkg-{slugify(name, 36)}-{secrets.token_hex(2)}"


def doc_id_for(package_id: str, file_name: str) -> str:
    stem = file_name.rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    digest = hashlib.sha256(f"{package_id}/{file_name}".encode()).hexdigest()[:6]
    return f"{slugify(stem, 40)}-{digest}"


def unit_id(doc_id: str, pipeline_version: str, local: str) -> str:
    uid = f"{doc_id}:{pipeline_version}:{local}"
    if not UNIT_ID_RE.match(uid):
        raise ValueError(f"invalid unitId {uid!r}")
    return uid


def eu_local(n: int) -> str:
    """Local id for the n-th (1-based) evidence unit of a document lane."""
    return f"eu-{n:02d}"


def chunk_local(n: int) -> str:
    """Local id for the n-th (1-based) v1.0 chunk of a document lane."""
    return f"c-{n:02d}"


def chunk_label(n: int) -> str:
    """Label shown on a v1.0 `no region` chip, e.g. ``chunk c-03``."""
    return f"chunk {chunk_local(n)}"


def parse_unit_id(uid: str) -> tuple[str, str, str]:
    """Return (docId, pipelineVersion, local)."""
    m = UNIT_ID_RE.match(uid)
    if not m:
        raise ValueError(f"invalid unitId {uid!r}")
    return m["doc"], m["pv"], m["local"]


def field_id(label: str) -> str:
    return f"f-{slugify(label, 40)}"


def new_run_id() -> str:
    return f"r-{secrets.token_hex(6)}"


def index_name(pipeline_version: str) -> str:
    """OpenSearch Serverless index per pipeline version: ``units-v1-0``, ``units-v2-3-1``."""
    if not PIPELINE_VERSION_RE.match(pipeline_version):
        raise ValueError(f"invalid pipelineVersion {pipeline_version!r}")
    return "units-v" + pipeline_version.replace(".", "-")


def pv_slug(pipeline_version: str) -> str:
    """``2.3.1`` -> ``2-3-1`` (for names that cannot hold dots)."""
    return pipeline_version.replace(".", "-")


# --- principals -------------------------------------------------------------

def user_principal(sub: str) -> str:
    return f"user:{sub}"


def group_principal(group: str) -> str:
    return f"group:{group}"


def principals_for(sub: str, groups: Iterable[str]) -> list[str]:
    """Caller's principal list: ``["user:<sub>", "group:<g>", ...]``."""
    return [user_principal(sub)] + [group_principal(g) for g in sorted(set(groups)) if g]


def parse_groups_claim(value: object) -> list[str]:
    """``cognito:groups`` arrives as a list (JWT) or a string (API Gateway claims map:
    ``"[a, b]"`` or ``"a,b"`` or ``"a b"``)."""
    if value is None:
        return []
    if isinstance(value, list | tuple):
        return [str(v) for v in value if str(v)]
    text = str(value).strip().strip("[]")
    return [p for p in re.split(r"[,\s]+", text) if p]


def is_allowed(caller_principals: Iterable[str], allowed_principals: Iterable[str]) -> bool:
    return bool(set(caller_principals) & set(allowed_principals))


def short(value: str, n: int = 6) -> str:
    """Truncate IDs for display (Cognito sub, execution ids): first n chars."""
    return value[:n]
