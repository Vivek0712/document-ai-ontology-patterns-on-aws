"""search_units@1: hybrid (k-NN + BM25) search with client-side min-max fusion.

Both sub-queries carry the same ACL/package/version filter INSIDE the query (never a
post_filter). ``pipelineVersion`` from the interceptor selects the index ``units-v<pv>``.
Displayed scores are the normalised values; raw scores are kept for the run record.
"""

from __future__ import annotations

import os
import time
from typing import Any

from evidence_core.bbox import BBox
from evidence_core.enums import PiiFlag, UnitType
from evidence_core.ids import index_name
from evidence_core.models.tools import (
    FieldBrief,
    RetrievedUnit,
    SearchQueryEcho,
    SearchUnitsArgs,
    SearchUnitsResult,
    UnitScores,
)
from evidence_core.retrieval import FusedHit, Hit, bm25_query, build_filter, dense_query, fuse

from lambdas.tools._lib.aoss import AossClient, IndexMissing
from lambdas.tools._lib.embed import embed_query
from lambdas.tools._lib.errors import NotReady
from lambdas.tools._lib.runtime import ToolCall, tool_handler
from lambdas.tools._lib.stores import pipeline

_AOSS: AossClient | None = None
_BEDROCK: Any = None


def aoss_client() -> AossClient:
    global _AOSS
    if _AOSS is None:
        endpoint = os.environ.get("RP_AOSS_ENDPOINT")
        if not endpoint:
            raise NotReady("RP_AOSS_ENDPOINT is not configured")
        _AOSS = AossClient(endpoint=endpoint, region=os.environ.get("AWS_REGION", "us-east-1"))
    return _AOSS


def use_clients(aoss: AossClient | None, bedrock: Any) -> None:
    """Tests: install fake clients."""
    global _AOSS, _BEDROCK
    _AOSS, _BEDROCK = aoss, bedrock


def _bedrock() -> Any:
    global _BEDROCK
    if _BEDROCK is None:
        import boto3

        _BEDROCK = boto3.client("bedrock-runtime")
    return _BEDROCK


def _hits(resp: dict[str, Any]) -> list[Hit]:
    out = []
    for h in resp.get("hits", {}).get("hits", []):
        src = h.get("_source", {})
        uid = src.get("unitId")
        if uid:
            out.append(Hit(unit_id=uid, score=float(h.get("_score") or 0.0), source=src | {"_id": h.get("_id")}))
    return out


def to_retrieved(rank: int, f: FusedHit) -> RetrievedUnit:
    s = f.source
    fields = [
        FieldBrief(
            field_id=fd["fieldId"], label=fd.get("label", ""), value=fd.get("value", ""),
            value_number=fd.get("valueNumber"), concept=fd.get("concept"), period=fd.get("period"),
            page=(fd.get("bbox") or {}).get("page"),
            bbox=BBox.load(fd["bbox"]) if fd.get("bbox") else None,
        )
        for fd in s.get("fields") or []
        if fd.get("fieldId")
    ]
    bboxes = [BBox.load(b) for b in s.get("bboxes") or []]
    return RetrievedUnit(
        rank=rank,
        unit_id=f.unit_id,
        doc_id=s["docId"],
        file_name=s.get("fileName", ""),
        unit_type=UnitType(s.get("unitType", "chunk")),
        label=s.get("label", f.unit_id),
        chunk_label=s.get("chunkLabel"),
        doc_type=s.get("docType"),
        breadcrumb=s.get("breadcrumb") or "",
        text=s.get("text", ""),
        pages=list(s.get("pages") or []),
        page_width=s.get("pageWidth"),
        page_height=s.get("pageHeight"),
        bboxes=bboxes,
        fields=fields,
        period=s.get("period"),
        pii_flag=PiiFlag(s.get("piiFlag", "none")),
        resolvable=bool(bboxes),
        scores=UnitScores(dense_raw=f.dense_raw, bm25_raw=f.bm25_raw, dense_norm=f.dense_norm,
                          bm25_norm=f.bm25_norm, fused=f.fused),
    )


@tool_handler("search_units")
def search_units(args: SearchUnitsArgs, call: ToolCall) -> SearchUnitsResult:
    t0 = time.monotonic()
    call.stores.require_package(call.ctx.package_id, call.principals)
    lp = pipeline(call.ctx.pipeline_version)
    r = lp.config.retrieval
    k = args.k or r.k
    sub_k = max(r.fusion.sub_query_k, k)
    index = index_name(call.ctx.pipeline_version)
    flt = build_filter(
        principals=call.principals,
        package_id=call.ctx.package_id,
        pipeline_version=call.ctx.pipeline_version,
        doc_type=args.doc_type,
        unit_types=[u.value for u in args.unit_types] if args.unit_types else None,
    )
    embed_spec = lp.models.models.get("EMBED_MODEL")
    vector = embed_query(args.query, model_id=r.embed_model, dimensions=r.embed_dimensions,
                         normalize=bool(embed_spec.normalize) if embed_spec else True, client=_bedrock())
    client = aoss_client()
    try:
        dense = _hits(client.search(index, dense_query(vector, flt, sub_k)))
        bm25 = _hits(client.search(index, bm25_query(args.query, flt, sub_k)))
    except IndexMissing as exc:  # a readiness problem, not "no evidence": the trace must say so
        raise NotReady(f"index {index} does not exist yet: no {call.ctx.pipeline_version} lane has been "
                       "indexed (or the index was reset). Upload documents and wait for the lane to be ready.") from exc
    fused = fuse(dense, bm25, w_dense=r.fusion.dense, w_bm25=r.fusion.bm25, k=k)
    return SearchUnitsResult(
        units=[to_retrieved(i + 1, f) for i, f in enumerate(fused)],
        query=SearchQueryEcho(
            index=index, k=k, sub_query_k=sub_k,
            weights={"dense": r.fusion.dense, "bm25": r.fusion.bm25},
            filter=flt, embed_model=r.embed_model,
        ),
        latency_ms=int((time.monotonic() - t0) * 1000),
    )


handler = search_units
