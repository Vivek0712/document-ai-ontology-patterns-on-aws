"""pyoxigraph-backed stand-in for SparqlClient (same query/update surface, SPARQL JSON results)."""

from __future__ import annotations

import json
from typing import Any

import pytest

ox = pytest.importorskip("pyoxigraph")  # test-only dep (services/agent/requirements-dev.txt)


class OxiSparql:
    def __init__(self) -> None:
        self.store = ox.Store()
        self.queries: list[str] = []
        self.updates: list[str] = []

    def load_nquads(self, text: str) -> None:
        self.store.load(text.encode(), format=ox.RdfFormat.N_QUADS)

    def query(self, sparql: str) -> dict[str, Any]:
        self.queries.append(sparql)
        res = self.store.query(sparql)
        return json.loads(res.serialize(format=ox.QueryResultsFormat.JSON))

    def update(self, sparql: str) -> None:
        self.updates.append(sparql)
        self.store.update(sparql)
