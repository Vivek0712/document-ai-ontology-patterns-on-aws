"""Amazon OpenSearch Serverless client for ``embed_index`` and ``mark_ready`` (SigV4, service ``aoss``).

Documents are bulk-indexed with auto-assigned ``_id`` (custom ids are not accepted on vector
collections); the returned ids are stored in UnitStatus so a rerun deletes exactly those documents.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from evidence_core.retrieval import index_mapping


class BulkError(RuntimeError):
    pass


@dataclass
class AossClient:
    client: Any  # opensearchpy.OpenSearch

    @classmethod
    def connect(cls, endpoint: str, region: str) -> AossClient:
        import boto3
        from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

        host = endpoint.replace("https://", "").rstrip("/")
        auth = AWSV4SignerAuth(boto3.Session().get_credentials(), region, "aoss")
        os_client = OpenSearch(hosts=[{"host": host, "port": 443}], http_auth=auth, use_ssl=True,
                               verify_certs=True, connection_class=RequestsHttpConnection, timeout=60,
                               pool_maxsize=10)
        return cls(os_client)

    def ensure_index(self, index: str, dimensions: int) -> bool:
        """Create the index with the contract mapping if missing. Returns True when created."""
        if self.client.indices.exists(index=index):
            return False
        self.client.indices.create(index=index, body=index_mapping(dimensions))
        return True

    def bulk_index(self, index: str, docs: list[dict[str, Any]]) -> list[str]:
        """Index without ``_id``; returns the assigned ids in input order."""
        if not docs:
            return []
        lines = []
        for d in docs:
            lines.append(json.dumps({"index": {"_index": index}}))
            lines.append(json.dumps(d, default=str))
        resp = self.client.bulk(body="\n".join(lines) + "\n")
        ids: list[str] = []
        errors = []
        for item in resp.get("items", []):
            op = item.get("index", {})
            if op.get("error") or int(op.get("status", 500)) >= 300:
                errors.append(op.get("error"))
            ids.append(op.get("_id"))
        if errors:
            raise BulkError(f"{len(errors)} of {len(docs)} documents failed: {str(errors[0])[:300]}")
        return ids

    def delete_ids(self, index: str, ids: list[str]) -> int:
        if not ids:
            return 0
        body = "\n".join(json.dumps({"delete": {"_index": index, "_id": i}}) for i in ids) + "\n"
        resp = self.client.bulk(body=body)
        return sum(1 for it in resp.get("items", []) if int(it.get("delete", {}).get("status", 500)) in (200, 404))

    def count(self, index: str, doc_id: str, pipeline_version: str) -> int:
        body = {"size": 0, "track_total_hits": True, "query": {"bool": {"filter": [
            {"term": {"docId": doc_id}}, {"term": {"pipelineVersion": pipeline_version}}]}}}
        resp = self.client.search(index=index, body=body)
        total = resp.get("hits", {}).get("total", 0)
        return int(total.get("value", 0) if isinstance(total, dict) else total)
