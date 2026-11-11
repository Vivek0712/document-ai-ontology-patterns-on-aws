"""In-memory stand-in for OpenSearch Serverless behind the real ``AossClient``.

``MemoryOpenSearch.transport`` has the ``Transport`` signature of ``lambdas.tools._lib.aoss`` and answers
the two queries ``search_units`` sends (``retrieval.dense_query`` and ``retrieval.bm25_query``), so the
tool's own code builds the filter, embeds the query, runs both sub-queries and fuses them:

- **k-NN:** exact inner product over the stored 1024-d vectors with the filter applied first (the index
  is small, so exact search returns what HNSW with efficient filtering returns), scored like the faiss
  ``innerproduct`` space (``1 + ip`` for ip >= 0, ``1 / (1 - ip)`` below);
- **BM25:** Lucene's formula (k1 1.2, b 0.75, idf ``ln(1 + (N - df + 0.5) / (df + 0.5))``) over
  ``searchText`` (``breadcrumb`` + ``text``, as the mapping's ``copy_to``), with a tokenizer that
  approximates the standard analyzer (lower case, numbers such as ``98,400.00`` kept whole).

Collection statistics are computed over every document in the index, as Lucene does per shard (one
shard here). This is a labelled stand-in: scores are close to, not identical with, OpenSearch's.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

K1, B = 1.2, 0.75
_TOKEN = re.compile(r"[0-9a-z]+(?:[.,'’][0-9a-z]+)*")
LABEL = "in-memory k-NN (exact inner product) + BM25 (Lucene formula) over the run's units; NOT OpenSearch"


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def search_text(doc: dict[str, Any]) -> str:
    return f"{doc.get('breadcrumb') or ''} {doc.get('text') or ''}"


def knn_score(ip: float) -> float:
    return 1.0 + ip if ip >= 0 else 1.0 / (1.0 - ip)


def matches(doc: dict[str, Any], clause: dict[str, Any]) -> bool:
    """Evaluate the subset of the query DSL that ``retrieval.build_filter`` emits."""
    if "bool" in clause:
        b = clause["bool"]
        return (all(matches(doc, c) for c in b.get("filter", []) + b.get("must", []))
                and not any(matches(doc, c) for c in b.get("must_not", [])))
    if "term" in clause:
        (fld, value), = clause["term"].items()
        value = value["value"] if isinstance(value, dict) else value
        return value in _values(doc.get(fld))
    if "terms" in clause:
        (fld, values), = clause["terms"].items()
        return bool(set(values) & set(_values(doc.get(fld))))
    raise ValueError(f"unsupported filter clause {sorted(clause)}")


def _values(v: Any) -> list[Any]:
    return v if isinstance(v, list) else [] if v is None else [v]


@dataclass
class IndexStore:
    """One index: documents (with ``embedding``) and BM25 statistics."""

    docs: list[dict[str, Any]]
    _tf: list[Counter[str]] = field(init=False, repr=False)
    _df: Counter[str] = field(init=False, repr=False)
    _avgdl: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._tf = [Counter(tokenize(search_text(d))) for d in self.docs]
        self._df = Counter(t for tf in self._tf for t in tf)
        lengths = [sum(tf.values()) for tf in self._tf]
        self._avgdl = (sum(lengths) / len(lengths)) if lengths else 0.0

    def bm25(self, i: int, query_tokens: Sequence[str]) -> float:
        tf, n = self._tf[i], len(self.docs)
        dl = sum(tf.values())
        score = 0.0
        for t in query_tokens:
            f = tf.get(t, 0)
            if not f:
                continue
            df = self._df[t]
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            score += idf * f / (f + K1 * (1 - B + B * dl / (self._avgdl or 1.0)))
        return score

    def count(self, query: dict[str, Any]) -> int:
        """``_count`` for the queries the filter inspection sends: ``match_all``, a bool of filters, or the BM25
        sub-query's bool (``must`` match + filter; a document counts when its BM25 score is above zero)."""
        if "match_all" in query:
            return len(self.docs)
        b = query["bool"]
        must = b.get("must") or []
        qtok = []
        if must:
            match = must[0]["match"]["searchText"]
            qtok = tokenize(match["query"] if isinstance(match, dict) else match)
        n = 0
        for i, d in enumerate(self.docs):
            if not all(matches(d, f) for f in b.get("filter", [])):
                continue
            if any(matches(d, c) for c in b.get("must_not", [])):
                continue
            if must and self.bm25(i, qtok) <= 0:
                continue
            n += 1
        return n

    def search(self, body: dict[str, Any]) -> dict[str, Any]:
        size = int(body.get("size", 10))
        query = body["query"]
        scored: list[tuple[float, int]] = []
        if "knn" in query:
            spec = query["knn"]["embedding"]
            vec, flt, k = spec["vector"], spec.get("filter"), int(spec.get("k", size))
            for i, d in enumerate(self.docs):
                if flt is None or matches(d, flt):
                    scored.append((knn_score(sum(a * b for a, b in zip(vec, d["embedding"], strict=True))), i))
            size = min(size, k)
        else:
            must = query["bool"]["must"][0]["match"]["searchText"]
            text = must["query"] if isinstance(must, dict) else must
            qtok = tokenize(text)
            for i, d in enumerate(self.docs):
                if all(matches(d, f) for f in query["bool"].get("filter", [])):
                    s = self.bm25(i, qtok)
                    if s > 0:
                        scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        hits = [{"_id": self.docs[i].get("_id", str(i)), "_score": round(s, 6),
                 "_source": {k: v for k, v in self.docs[i].items() if k not in ("embedding", "_id")}}
                for s, i in scored[:size]]
        return {"hits": {"total": {"value": len(scored), "relation": "eq"}, "hits": hits}}


@dataclass
class MemoryOpenSearch:
    indexes: dict[str, IndexStore] = field(default_factory=dict)
    requests: int = 0
    label: str = "in-memory stand-in for OpenSearch Serverless _count over the run's index documents; NOT OpenSearch"

    def count(self, index: str, query: dict[str, Any]) -> int:
        """The ``AossClient.count`` surface of rp-api (``GET /inspect/filters``)."""
        self.requests += 1
        store = self.indexes.get(index)
        if store is None:
            raise RuntimeError(f"index {index} does not exist")
        return store.count(query)

    def transport(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
        self.requests += 1
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        index, _, op = path.partition("/")
        if method != "POST" or op != "_search":
            return 400, json.dumps({"error": f"unsupported {method} {path}"}).encode()
        store = self.indexes.get(index)
        if store is None:
            return 404, json.dumps({"error": {"type": "index_not_found_exception", "index": index},
                                    "status": 404}).encode()
        return 200, json.dumps(store.search(json.loads(body))).encode()
