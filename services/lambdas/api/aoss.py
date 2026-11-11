"""Minimal SigV4-signed client for OpenSearch Serverless (service ``aoss``), stdlib + botocore only.

The API deletes index documents by their stored auto ``_id`` (DELETE package; OpenSearch Serverless has no
``_delete_by_query``) and counts documents for the filter inspection (``_count``, GET /inspect/filters)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import urllib3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session


class IndexCountError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"_count -> {status}: {body}")
        self.status = status


@dataclass
class AossClient:
    endpoint: str
    region: str
    service: str = "aoss"
    timeout_s: float = 10.0

    def __post_init__(self) -> None:
        self.endpoint = self.endpoint.rstrip("/")
        self._http = urllib3.PoolManager(timeout=urllib3.Timeout(total=self.timeout_s))

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode() if body is not None else b""
        url = f"{self.endpoint}{path}"
        headers = {"Content-Type": "application/json", "x-amz-content-sha256": _sha256(data)}
        req = AWSRequest(method=method, url=url, data=data, headers=headers)
        creds = Session().get_credentials()
        if creds is None:
            raise RuntimeError("no AWS credentials for OpenSearch Serverless")
        SigV4Auth(creds.get_frozen_credentials(), self.service, self.region).add_auth(req)
        resp = self._http.request(method, url, body=data or None, headers=dict(req.headers.items()))
        try:
            payload = json.loads(resp.data or b"{}")
        except json.JSONDecodeError:
            payload = {"raw": resp.data[:500].decode("utf-8", "replace")}
        return resp.status, payload

    label = "OpenSearch Serverless _count"

    def count(self, index: str, query: dict[str, Any]) -> int:
        """Documents of ``index`` matching ``query`` (``POST /{index}/_count``). A missing index raises."""
        status, payload = self.request("POST", f"/{quote(index)}/_count", {"query": query})
        if status != 200 or "count" not in payload:
            raise IndexCountError(status, str(payload)[:300])
        return int(payload["count"])

    def delete_doc(self, index: str, doc_id: str) -> bool:
        status, _ = self.request("DELETE", f"/{quote(index)}/_doc/{quote(doc_id, safe='')}")
        return status in (200, 202, 404)


def _sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()
