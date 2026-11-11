"""GET /artifacts?key= : metadata, a bounded text preview and (where allowed) a presigned URL.

Rules: package keys need the package ACL; ``config/`` and ``eval/`` are readable by any
signed-in user; ``runs/{runId}.json`` needs the run's package ACL. Previews are <= 64 KB, and Bronze,
Silver and quarantine previews mask long digit runs. Silver previews also mask every span the PII gate held
for that document (any lane), whatever its shape: a held span is not always a long digit run (e.g. OCR'd table
cells that Comprehend scored as an SSN). URLs are issued for gold/config/runs/eval and for Bronze to its owner
only; never for Silver or quarantine."""

from __future__ import annotations

import json
from typing import Any

from botocore.exceptions import ClientError
from evidence_core import s3keys
from evidence_core.enums import PiiFlag
from evidence_core.ids import RUN_ID_RE
from evidence_core.masking import mask_long_digits
from evidence_core.models.api import ARTIFACT_PREVIEW_MAX_BYTES, REGION_URL_TTL_SECONDS, ArtifactView
from evidence_core.models.units import PiiDecisions

from .. import store
from ..acl import require_package
from ..deps import Deps, get_deps
from ..http import Reply, Request, bad_request, forbidden, not_found
from . import ARTIFACTS, router

TEXT_TYPES = ("application/json", "application/x-ndjson", "application/jsonl", "application/n-quads",
              "application/yaml", "application/x-yaml", "text/")
TEXT_SUFFIXES = (".json", ".jsonl", ".yaml", ".yml", ".txt", ".nq", ".rq", ".md", ".csv")
URL_TIERS = {s3keys.GOLD, s3keys.CONFIG, s3keys.RUNS, s3keys.EVAL}
MASKED_TIERS = {s3keys.BRONZE, s3keys.SILVER, s3keys.QUARANTINE}
HELD_MASK = "[held: PII]"


def _is_text(key: str, content_type: str) -> bool:
    return content_type.startswith(TEXT_TYPES) or key.endswith(TEXT_SUFFIXES)


def _content_type(key: str, reported: str | None) -> str:
    if reported and reported not in ("binary/octet-stream", "application/octet-stream"):
        return reported
    for suffix, ct in ((".jsonl", "application/x-ndjson"), (".json", "application/json"), (".png", "image/png"),
                       (".pdf", "application/pdf"), (".yaml", "application/yaml"), (".nq", "application/n-quads"),
                       (".txt", "text/plain"), (".rq", "application/sparql-query")):
        if key.endswith(suffix):
            return ct
    return reported or "application/octet-stream"


def _validate_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/") or "//" in key:
        raise bad_request("invalid artifact key")
    try:
        return s3keys.tier_of(key)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc


def authorize(deps: Deps, req: Request, key: str, tier: str) -> bool:
    """Raise unless the caller may read ``key``; return True when the caller owns the package."""
    if tier in (s3keys.CONFIG, s3keys.EVAL):
        return False
    if tier == s3keys.RUNS:
        run_id = key.removeprefix("runs/").removesuffix(".json")
        if not RUN_ID_RE.match(run_id):
            raise forbidden("You don't have access to this artifact.")
        row = deps.table("runs").get_item(Key={"runId": run_id}).get("Item")
        if not row:
            raise not_found("Run not found.")
        require_package(deps, req.caller, str(row["packageId"]))
        return False
    package_id = s3keys.package_of(key)
    if not package_id:
        raise bad_request("artifact key has no package")
    pkg = require_package(deps, req.caller, package_id)
    if tier == s3keys.BRONZE and s3keys.parse_bronze_key(key).owner_sub != pkg.owner_sub:
        raise forbidden("You don't have access to this artifact.")
    return pkg.owner_sub == req.caller.sub


def held_spans(deps: Deps, key: str) -> list[str]:
    """The text of every unmasked span the PII gate held for the document a Silver key belongs to, in any lane
    (read from each lane's ``pii_gate/decisions.json`` offsets into its Silver ``units.jsonl``)."""
    parts = key.split("/")
    if len(parts) < 4 or parts[0] != s3keys.SILVER:
        return []
    package_id, doc_id = parts[1], parts[2]
    lanes = deps.s3.list_objects_v2(Bucket=deps.bucket, Prefix=f"{s3keys.SILVER}/{package_id}/{doc_id}/",
                                    Delimiter="/").get("CommonPrefixes", [])
    spans: list[str] = []
    for lane in lanes:
        pv = lane["Prefix"].rstrip("/").rsplit("/", 1)[-1]
        raw = store.read_json(deps, s3keys.pii_decisions_key(package_id, doc_id, pv))
        if not raw:
            continue
        held = [d for d in PiiDecisions.load(raw).decisions
                if d.decision is PiiFlag.HELD and any(not e.masked for e in d.entities)]
        if not held:
            continue
        units = store.load_units_jsonl(deps, s3keys.silver_units_key(package_id, doc_id, pv))
        texts = {u.unit_id: u.text for u in units}
        for d in held:
            text = texts.get(d.unit_id) or ""
            spans += [text[e.begin:e.end] for e in d.entities if not e.masked and text[e.begin:e.end].strip()]
    return spans


def mask_held(text: str, spans: list[str]) -> str:
    """Replace each held span as written, whitespace-collapsed and JSON-escaped (previews are often JSON)."""
    for span in sorted(set(spans), key=len, reverse=True):
        for form in {span, " ".join(span.split()), json.dumps(span, ensure_ascii=False)[1:-1]}:
            text = text.replace(form, HELD_MASK)
    return text


def build_view(deps: Deps, key: str, tier: str, is_owner: bool) -> ArtifactView:
    meta = store.head(deps, key)
    if meta is None:
        raise not_found(f"Artifact {key} not found.")
    size = int(meta.get("ContentLength", 0))
    ctype = _content_type(key, meta.get("ContentType"))
    view: dict[str, Any] = {
        "key": key, "tier": tier, "content_type": ctype, "size_bytes": size,
        "etag": (meta.get("ETag") or "").strip('"') or None, "version_id": meta.get("VersionId"),
        "last_modified": meta["LastModified"].isoformat() if meta.get("LastModified") else None,
    }
    if _is_text(key, ctype) and size > 0:
        try:
            obj = deps.s3.get_object(Bucket=deps.bucket, Key=key, Range=f"bytes=0-{ARTIFACT_PREVIEW_MAX_BYTES - 1}")
        except ClientError as exc:
            raise not_found(f"Artifact {key} not readable.") from exc
        text = obj["Body"].read().decode("utf-8", errors="replace")
        masked = tier in MASKED_TIERS
        if tier == s3keys.SILVER:
            text = mask_held(text, held_spans(deps, key))
        view.update(preview=mask_long_digits(text) if masked else text,
                    preview_truncated=size > ARTIFACT_PREVIEW_MAX_BYTES, preview_masked=masked)
    if tier in URL_TIERS or (tier == s3keys.BRONZE and is_owner):
        view.update(url=store.presign_get(deps, key, REGION_URL_TTL_SECONDS), expires_in=REGION_URL_TTL_SECONDS)
    return ArtifactView(**view)


@router.route("GET", ARTIFACTS)
def get_artifact(req: Request) -> Reply:
    deps = get_deps()
    key = req.query.get("key", "")
    tier = _validate_key(key)
    is_owner = authorize(deps, req, key, tier)
    return Reply(build_view(deps, key, tier, is_owner))
