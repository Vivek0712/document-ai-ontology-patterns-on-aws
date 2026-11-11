"""A local stand-in for the AgentCore Gateway: the real tool Lambda handlers over a loaded local world.

What runs is the v1 code, unchanged: ``search_units`` (filter inside both sub-queries, Titan V2 query
embedding, client-side min-max fusion 0.7/0.3, K from the lane config), ``graph_hop`` (approved SPARQL
templates, ACL first) and ``get_region`` / ``doc_status`` (DynamoDB + Gold units). Only the services
behind them are local:

- DynamoDB and S3: the run's world snapshot in moto (``local_pipeline.world``);
- OpenSearch Serverless: ``MemoryOpenSearch`` over the run's index documents, each re-embedded with
  **Titan Text Embeddings V2** (real Bedrock, recorded) from the Gold unit's ``embedding_text()``, exactly
  what ``embed_index`` sends;
- Neptune: pyoxigraph loaded with the TBox and the ABox the run's ``graph_write`` produced.

``invoke`` plays the REQUEST interceptor's part: it drops any identity key the model supplied and adds
the caller's ``callerSub, groups, pipelineVersion, packageId, runId``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evidence_core import s3keys
from evidence_core.config import load_pipeline
from evidence_core.models.tools import INJECTED_FIELDS
from evidence_core.models.units import Unit
from local_pipeline.world import LocalWorld

from .index import IndexStore, MemoryOpenSearch

REPO = Path(__file__).resolve().parents[2]
TOOLS = ("search_units", "graph_hop", "get_region", "doc_status")


@dataclass(frozen=True)
class Caller:
    sub: str
    groups: list[str]


@dataclass
class LocalGateway:
    world: LocalWorld
    bedrock: Any  # bedrock-runtime client (``RecordedTitan`` in the harness)
    abox: Path
    search: MemoryOpenSearch = field(default_factory=MemoryOpenSearch)
    sparql: Any = None
    calls: int = 0
    _handlers: dict[str, Any] = field(default_factory=dict, init=False, repr=False)

    def install(self) -> LocalGateway:
        """Point the tool modules at the local services (process-wide, like a Lambda container)."""
        from evidence_core.env import Settings
        from lambdas.tools._lib import stores as stores_mod
        from lambdas.tools._lib.aoss import AossClient
        from lambdas.tools.doc_status import handler as doc_status
        from lambdas.tools.get_region import handler as get_region
        from lambdas.tools.graph_hop import handler as graph_hop
        from lambdas.tools.search_units import handler as search_units
        from local_pipeline.graph_check import OxiSparql, load_tbox

        stores_mod.use_stores(stores_mod.Stores(settings=Settings.from_env(), dynamodb=self.world.dynamodb,
                                                s3=self.world.s3))
        self.search.indexes = {name: IndexStore(self._embedded(docs)) for name, docs in self.world.aoss_docs.items()}
        search_units.use_clients(AossClient(endpoint="https://local-aoss", sign=False,
                                            transport=self.search.transport), self.bedrock)
        if self.sparql is None:
            self.sparql = OxiSparql()
            onto = load_pipeline("2.3.1").config.ontology_version or "1.2.0"
            load_tbox(self.sparql, REPO, onto)
            self.sparql.load_nquads(self.abox.read_text(encoding="utf-8"))
        graph_hop.use_client(self.sparql)
        self._handlers = {"search_units": search_units.handler, "graph_hop": graph_hop.handler,
                          "get_region": get_region.handler, "doc_status": doc_status.handler}
        return self

    def _embedded(self, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Index documents with Titan V2 vectors of the Gold unit's embedding text."""
        from lambdas.tools._lib.embed import embed_query

        out = []
        for d in docs:
            pv = d["pipelineVersion"]
            r = load_pipeline(pv).config.retrieval
            unit = Unit.load(self.world.read_json(s3keys.gold_unit_key(d["packageId"], d["unitId"])))
            vec = embed_query(unit.embedding_text(), model_id=r.embed_model, dimensions=r.embed_dimensions,
                              normalize=True, client=self.bedrock)
            out.append(d | {"embedding": vec})
        return out

    def invoke(self, tool: str, arguments: dict[str, Any], caller: Caller, *, pipeline_version: str,
               package_id: str, run_id: str) -> tuple[dict[str, Any], int]:
        """-> (tool JSON payload, measured ms). Model-supplied identity keys never reach the tool."""
        if tool not in self._handlers:
            return {"error": {"code": "not_found", "message": f"unknown tool {tool}"}}, 0
        event = {k: v for k, v in (arguments or {}).items() if k not in INJECTED_FIELDS}
        event |= {"callerSub": caller.sub, "groups": list(caller.groups), "pipelineVersion": pipeline_version,
                  "packageId": package_id, "runId": run_id}
        t0 = time.monotonic()
        payload = self._handlers[tool](event, None)
        self.calls += 1
        return payload, int((time.monotonic() - t0) * 1000)

    def close(self) -> None:
        from lambdas.tools._lib import stores as stores_mod
        from lambdas.tools.graph_hop import handler as graph_hop
        from lambdas.tools.search_units import handler as search_units

        stores_mod.use_stores(None)
        search_units.use_clients(None, None)
        graph_hop.use_client(None)
