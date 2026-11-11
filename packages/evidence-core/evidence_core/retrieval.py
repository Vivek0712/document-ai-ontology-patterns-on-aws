"""Retriever pieces shared by search_units, the local pipeline and eval.

two sub-queries (k-NN dense, BM25), the same filter INSIDE both, top
``subQueryK`` each, min-max normalise within each list, fused = 0.7·dense_n + 0.3·bm25_n
(a unit missing from a list scores 0 there), ties broken by unitId, return K.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .ids import index_name

# --- OpenSearch Serverless index mapping (both indexes) -----------------------------------------

def index_mapping(dimensions: int = 1024) -> dict[str, Any]:
    kw = {"type": "keyword"}
    return {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                "unitId": kw,
                "docId": kw,
                "packageId": kw,
                "pipelineVersion": kw,
                "unitType": kw,
                "label": {"type": "keyword", "index": False},
                "chunkLabel": kw,
                "fileName": kw,
                "docType": kw,
                "fieldConcepts": kw,
                "period": kw,
                "piiFlag": kw,
                "allowedPrincipals": kw,
                "configSha": kw,
                "breadcrumb": {"type": "text", "copy_to": "searchText"},
                "text": {"type": "text", "copy_to": "searchText"},
                "searchText": {"type": "text"},
                "pages": {"type": "integer"},
                "pageWidth": {"type": "float"},
                "pageHeight": {"type": "float"},
                "bboxes": {"type": "object", "enabled": False},
                "fieldIds": kw,
                "fields": {"type": "object", "enabled": False},
                "confidence": {"type": "float"},
                "indexedAt": {"type": "date"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dimensions,
                    "method": {
                        # No "engine": NextGen OpenSearch Serverless chooses it and rejects the parameter.
                        "name": "hnsw",
                        "space_type": "innerproduct",
                        "parameters": {"m": 16, "ef_construction": 128},
                    },
                },
            }
        },
    }


INDEX_NAMES: dict[str, str] = {pv: index_name(pv) for pv in ("1.0", "2.3.1")}


def build_filter(
    *,
    principals: Sequence[str],
    package_id: str,
    pipeline_version: str,
    doc_type: str | None = None,
    unit_types: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Bool filter placed inside BOTH sub-queries. Never a post_filter."""
    if not principals:
        raise ValueError("empty principal list")
    clauses: list[dict[str, Any]] = [
        {"terms": {"allowedPrincipals": list(principals)}},
        {"term": {"packageId": package_id}},
        {"term": {"pipelineVersion": pipeline_version}},
    ]
    if doc_type:
        clauses.append({"term": {"docType": doc_type}})
    if unit_types:
        clauses.append({"terms": {"unitType": list(unit_types)}})
    return {"bool": {"filter": clauses, "must_not": [{"term": {"piiFlag": "held"}}]}}


_SOURCE_EXCLUDES = ["embedding"]


def dense_query(vector: Sequence[float], flt: dict[str, Any], k: int) -> dict[str, Any]:
    """k-NN with efficient filtering inside the knn clause."""
    return {
        "size": k,
        "_source": {"excludes": _SOURCE_EXCLUDES},
        "query": {"knn": {"embedding": {"vector": list(vector), "k": k, "filter": flt}}},
    }


def bm25_query(text: str, flt: dict[str, Any], k: int) -> dict[str, Any]:
    return {
        "size": k,
        "_source": {"excludes": _SOURCE_EXCLUDES},
        "query": {"bool": {"must": [{"match": {"searchText": {"query": text}}}], "filter": [flt]}},
    }


@dataclass(frozen=True)
class Hit:
    unit_id: str
    score: float
    source: dict[str, Any]


@dataclass(frozen=True)
class FusedHit:
    unit_id: str
    dense_raw: float | None
    bm25_raw: float | None
    dense_norm: float
    bm25_norm: float
    fused: float
    source: dict[str, Any]


def min_max(scores: Sequence[float]) -> list[float]:
    """Min-max within one list. A single hit (or all equal) normalises to 1.0."""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    if hi == lo:
        return [1.0 for _ in scores]
    return [(s - lo) / (hi - lo) for s in scores]


def fuse(dense: Sequence[Hit], bm25: Sequence[Hit], *, w_dense: float = 0.7, w_bm25: float = 0.3,
         k: int = 5) -> list[FusedHit]:
    dn = dict(zip((h.unit_id for h in dense), min_max([h.score for h in dense]), strict=True))
    bn = dict(zip((h.unit_id for h in bm25), min_max([h.score for h in bm25]), strict=True))
    draw = {h.unit_id: h.score for h in dense}
    braw = {h.unit_id: h.score for h in bm25}
    sources: dict[str, dict[str, Any]] = {}
    for h in list(dense) + list(bm25):
        sources.setdefault(h.unit_id, h.source)
    fused: list[FusedHit] = []
    for uid in sources:
        d, b = dn.get(uid, 0.0), bn.get(uid, 0.0)
        fused.append(
            FusedHit(
                unit_id=uid,
                dense_raw=draw.get(uid),
                bm25_raw=braw.get(uid),
                dense_norm=round(d, 4),
                bm25_norm=round(b, 4),
                fused=round(w_dense * d + w_bm25 * b, 4),
                source=sources[uid],
            )
        )
    fused.sort(key=lambda f: (-f.fused, f.unit_id))
    return fused[:k]
