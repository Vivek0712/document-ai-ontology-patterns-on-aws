"""rp-xray · GET /documents/{docId}/lanes/{pv}/xray?page=&size=&runId= with pv = 1.0 (API Gateway REST, Cognito
authorizer; any other lane is a 400).

Returns the X-ray of one page (``core.build_xray``, contract ``XrayPage`` in contracts/api.ts):
where the v1.0 naive chunks' characters sit on the page, the text layer's reading order, positional (derived
hypothesis) vs geometric pairing, the v2.3.1 evidence units on the page, and, with ``runId``, the money figures of
that logged naive answer matched to the page (``answerNumbers``).

Honesty contract:
- An inspection artifact: cached under ``gold/{pkg}/{doc}/1.0/inspect/`` and never written into units, the index
  or citations, so v1.0 chunks stay unresolvable.
- ``size`` other than the control size is a what-if re-split with the real v1.0 splitter (``whatIf: true``).
- Tokens inside either lane's held regions, and every token of a held 1.0 chunk, are dropped before the payload
  leaves the Lambda (fail closed: on a page with held regions an unplaced token is dropped too); the evidence layer
  reads Gold units, where held units are absent.
- The page image is rendered from Bronze with the union of both lanes' held regions filled, never the 1.0 Gold
  render alone (that one masks only what the 1.0 gate held).
- The cache key folds in every input that changes what is shown: Bronze, both lanes' units, the 1.0 PII decisions,
  both lanes' held regions, the run and the code. Re-running the gate never serves a stale X-ray.

The DocAcl check runs before anything is read, exactly like ``get_region_http``.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote

from botocore.exceptions import ClientError
from evidence_core import s3keys
from evidence_core.bbox import BBox
from evidence_core.env import cors_headers
from evidence_core.ids import parse_groups_claim, principals_for, short
from evidence_core.logs import get_logger

from lambdas.ingest_lib.pdf import pypdf_version
from lambdas.ingest_lib.render import pdfium_version, render_pages
from lambdas.tools._lib.errors import BadRequest, NotFound, ToolError
from lambdas.tools._lib.stores import Stores, pipeline, stores
from lambdas.xray.core import XrayInput, build_xray, evidence_from_units, read_pdf

log = get_logger("rp.xray")

NAIVE, EVIDENCE = "1.0", "2.3.1"
DEFAULT_WHATIF_SIZES = (300, 200, 128, 64)  # the first is the control. Override: RP_XRAY_WHATIF_SIZES
IMAGE_URL_TTL_SECONDS = 300
FORBIDDEN = "You don't have access to this document."
SPLITTER = "lambdas.ingest_lib.chunker.split_pages (v1.0 recursive splitter, whitespace tokens)"
_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 500: "internal"}


def whatif_sizes(env: str | None = None) -> tuple[int, ...]:
    """Sizes the API serves, control first: ``RP_XRAY_WHATIF_SIZES="300,200,128,64"`` or the plan default."""
    raw = os.environ.get("RP_XRAY_WHATIF_SIZES", "") if env is None else env
    sizes = tuple(int(x) for x in raw.replace(" ", "").split(",") if x)
    return sizes or DEFAULT_WHATIF_SIZES


def xray_key(package_id: str, doc_id: str, page: int, size: int, version_key: str) -> str:
    """Cache key of the inspection artifact (Gold, lane 1.0). ``version_key`` folds in every input version (see
    ``compute``), so a new upload, a re-chunk, a new PII decision or new code never serves a stale X-ray."""
    return s3keys.gold_lane_prefix(package_id, doc_id, NAIVE) + f"inspect/xray-p{page:03d}-s{size}-{version_key}.json"


def page_image_keys(package_id: str, doc_id: str, page: int, version_key: str) -> tuple[str, str]:
    """The inspection page renders (1x, 2x), masked with both lanes' held regions."""
    base = s3keys.gold_lane_prefix(package_id, doc_id, NAIVE) + f"inspect/page-p{page:03d}-{version_key}"
    return base + ".png", base + "@2x.png"


def _jsonl(text: str | None) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (text or "").splitlines() if line.strip()]


def _held_regions(st: Stores, package_id: str, doc_id: str, page: int) -> list[BBox]:
    """The union of both lanes' held regions on ``page`` (promote's held_regions.json)."""
    out: list[BBox] = []
    for pv in (NAIVE, EVIDENCE):
        raw = st.read_json(s3keys.held_regions_key(package_id, doc_id, pv)) or {}
        for b in (raw.get("pages") or {}).get(str(page), []):
            out.append(BBox(page=page, x0=float(b["x0"]), y0=float(b["y0"]), x1=float(b["x1"]), y1=float(b["y1"])))
    return out


def _held_chunks(st: Stores, package_id: str, doc_id: str) -> list[str]:
    raw = st.read_json(s3keys.pii_decisions_key(package_id, doc_id, NAIVE)) or {}
    return [d["unitId"] for d in raw.get("decisions", []) if d.get("decision") == "held"]


def answer_texts(run: dict[str, Any]) -> list[str]:
    """Answer text, total, arithmetic and claim texts of a RunRecord: where its figures are written."""
    answer = run.get("answer") or {}
    texts = [answer.get("answer") or "", answer.get("total") or "", answer.get("arithmetic") or ""]
    return texts + [c.get("text") or "" for c in answer.get("claims") or []]


def _answer(st: Stores, run_id: str, package_id: str) -> tuple[str, list[str]]:
    """The logged naive answer's text and claim figures (the run must belong to the document's package)."""
    run = st.read_json(s3keys.run_key(run_id))
    if not run or run.get("packageId") != package_id:
        raise NotFound(f"Run {run_id} not found for this package.")
    if run.get("pipelineVersion") not in (None, NAIVE):
        raise BadRequest("answerNumbers come from a 1.0 (naive) run")
    return run_id, answer_texts(run)


def _version(st: Stores, key: str) -> str:
    """Object version (or ETag); ``absent`` when the object does not exist, so its later arrival changes the key."""
    try:
        head = st.s3.head_object(Bucket=st.bucket, Key=key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
            return "absent"
        raise
    return str(head.get("VersionId") or head.get("ETag") or "")


def compute(st: Stores, doc_id: str, page: int, size: int, principals: list[str], run_id: str | None = None,
            code_sha: str = "local") -> dict[str, Any]:
    acl = st.require_doc_acl(doc_id, principals, FORBIDDEN)
    pkg = acl.package_id
    doc = next((d for d in st.list_documents(pkg) if d.doc_id == doc_id), None)
    if doc is None or doc.upload_state != "IN_BRONZE":
        raise NotFound(f"Document {doc_id} is not in Bronze.")
    units_key = s3keys.silver_units_key(pkg, doc_id, NAIVE)
    bronze_version = doc.source_version_id or _version(st, doc.bronze_key)
    units_version = _version(st, units_key)
    # every input that changes what the payload or the image shows
    inputs = [s3keys.gold_units_key(pkg, doc_id, EVIDENCE), s3keys.pii_decisions_key(pkg, doc_id, NAIVE),
              *(s3keys.held_regions_key(pkg, doc_id, pv) for pv in (NAIVE, EVIDENCE))]
    fingerprint = "|".join([bronze_version, units_version, *(_version(st, k) for k in inputs), run_id or "", code_sha])
    version_key = hashlib.sha256(fingerprint.encode()).hexdigest()[:10]
    cache = xray_key(pkg, doc_id, page, size, version_key)
    image_key, zoom_key = page_image_keys(pkg, doc_id, page, version_key)
    payload = st.read_json(cache)
    if not payload:
        chunker = pipeline(NAIVE).config.preprocessing.chunker
        control, overlap_pct = chunker.size_tokens or 300, chunker.overlap_pct or 0
        dpi = pipeline(EVIDENCE).config.preprocessing.render.dpi
        pdf = st.s3.get_object(Bucket=st.bucket, Key=doc.bronze_key, VersionId=bronze_version)["Body"].read()
        naive = _jsonl(st.read_text(units_key))
        gold = _jsonl(st.read_text(s3keys.gold_units_key(pkg, doc_id, EVIDENCE)))
        pages = read_pdf(pdf)
        if not 1 <= page <= len(pages):
            raise BadRequest(f"page must be between 1 and {len(pages)}")
        held = _held_regions(st, pkg, doc_id, page)
        # the images first: a cached payload implies its masked renders exist
        for key, scale in ((image_key, 1), (zoom_key, 2)):
            [render] = render_pages(pdf, dpi=dpi * scale, held={page: held}, pages=[page])
            st.s3.put_object(Bucket=st.bucket, Key=key, Body=render.png, ContentType="image/png")
        payload = build_xray(XrayInput(
            doc_id=doc_id, page=page, pdf_pages=pages, chunk_texts=[u["text"] for u in naive],
            chunk_ids=[u["unitId"] for u in naive], held_chunk_ids=_held_chunks(st, pkg, doc_id),
            size_tokens=size, control_size=control, overlap_pct=overlap_pct, what_if_sizes=whatif_sizes(),
            evidence=evidence_from_units(gold), held=[(b.x0, b.y0, b.x1, b.y1) for b in held],
            answer=_answer(st, run_id, pkg) if run_id else None,
            image={"dpi": dpi},
            computed_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            sources={
                "bronze": {"key": doc.bronze_key, "versionId": bronze_version, "sha256": doc.sha256},
                "units": {"key": units_key, "versionId": units_version},
                "evidence": {"key": s3keys.gold_units_key(pkg, doc_id, EVIDENCE)},
                "heldRegions": [s3keys.held_regions_key(pkg, doc_id, pv) for pv in (NAIVE, EVIDENCE)],
                "pageImage": "Bronze rendered with both lanes' held regions filled",
                "pdfium": pdfium_version(), "splitter": SPLITTER, "textLayer": pypdf_version(),
                "codeSha": code_sha, "packageId": pkg, "fileName": doc.file_name, "mode": "live",
            },
        ))
        st.s3.put_object(Bucket=st.bucket, Key=cache, Body=json.dumps(payload).encode(), ContentType="application/json")
    # presigned URLs are never cached (they expire)
    payload["image"] = {"url": st.presign_get(image_key, IMAGE_URL_TTL_SECONDS),
                        "zoomUrl": st.presign_get(zoom_key, IMAGE_URL_TTL_SECONDS),
                        "dpi": (payload.get("image") or {}).get("dpi", 150), "key": image_key}
    return payload


def _response(event: dict[str, Any], status: int, body: dict[str, Any]) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Cache-Control": "no-store"} | cors_headers(event.get("headers"))
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


def _error(event: dict[str, Any], status: int, message: str) -> dict[str, Any]:
    body: dict[str, Any] = {"error": {"code": _CODES.get(status, "internal"), "message": message}}
    request_id = (event.get("requestContext") or {}).get("requestId")
    if request_id:
        body["error"]["requestId"] = request_id
    return _response(event, status, body)


def parse_request(event: dict[str, Any], allowed_sizes: tuple[int, ...] | None = None
                  ) -> tuple[str, int, int, str | None]:
    allowed_sizes = allowed_sizes or whatif_sizes()
    params = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}
    doc_id = unquote(params.get("docId") or "")
    if not doc_id:
        raise BadRequest("docId is required")
    if unquote(params.get("pv") or NAIVE) != NAIVE:
        raise BadRequest("the X-ray inspects the 1.0 lane only")
    try:
        page = int(query.get("page") or 1)
        size = int(query.get("size") or allowed_sizes[0])
    except ValueError as e:
        raise BadRequest("page and size must be integers") from e
    if size not in allowed_sizes:
        raise BadRequest(f"size must be one of {', '.join(map(str, allowed_sizes))}")
    run_id = unquote(query.get("runId") or "") or None
    return doc_id, page, size, run_id


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    sub = claims.get("sub")
    if not sub:
        return _error(event, 401, "Sign in to inspect documents.")
    principals = principals_for(sub, parse_groups_claim(claims.get("cognito:groups")))
    try:
        doc_id, page, size, run_id = parse_request(event)
        payload = compute(stores(), doc_id, page, size, principals, run_id, os.environ.get("RP_CODE_SHA") or "local")
    except ToolError as e:
        log.info("xray refused", extra={"rp": {"callerSub": short(sub), "code": e.code}})
        return _error(event, e.http_status, e.message)
    except Exception:
        log.exception("xray failed")
        return _error(event, 500, "Could not build the X-ray.")
    log.info("xray ok", extra={"rp": {"docId": doc_id, "page": page, "size": size, "callerSub": short(sub)}})
    return _response(event, 200, payload)
