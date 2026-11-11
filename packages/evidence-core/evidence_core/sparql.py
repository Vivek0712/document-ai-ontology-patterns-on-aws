"""Minimal SigV4-signed SPARQL client for Neptune (IAM auth on, service ``neptune-db``).

Used by graph_write, graph_hop, neptune_load, graph_admin and vocab_export.
Only stdlib + botocore (present in the Lambda runtime). For local tests point
``endpoint`` at any SPARQL 1.1 endpoint and pass ``sign=False``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class SparqlError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"SPARQL HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


@dataclass
class SparqlClient:
    endpoint: str  # "host:8182" or "https://host:8182"
    region: str = "us-east-1"
    sign: bool = True
    timeout: float = 30.0

    @property
    def url(self) -> str:
        base = self.endpoint if self.endpoint.startswith("http") else f"https://{self.endpoint}"
        return base.rstrip("/") + "/sparql"

    def _send(self, form: dict[str, str], accept: str) -> str:
        body = urllib.parse.urlencode(form).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": accept}
        if self.sign:
            import botocore.session
            from botocore.auth import SigV4Auth
            from botocore.awsrequest import AWSRequest

            creds = botocore.session.get_session().get_credentials()
            req = AWSRequest(method="POST", url=self.url, data=body, headers=headers)
            SigV4Auth(creds, "neptune-db", self.region).add_auth(req)
            headers = dict(req.headers)
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")  # noqa: S310
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:  # noqa: S310
                return resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise SparqlError(e.code, e.read().decode("utf-8", "replace")) from e

    def query(self, sparql: str) -> dict[str, Any]:
        """SELECT/ASK -> SPARQL 1.1 JSON results."""
        return json.loads(self._send({"query": sparql}, "application/sparql-results+json"))

    def update(self, sparql: str) -> None:
        self._send({"update": sparql}, "application/json")


def bindings_to_rows(result: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    """SPARQL JSON results -> (columns, rows of plain values; numeric literals become strings)."""
    cols: list[str] = result.get("head", {}).get("vars", [])
    rows = []
    for b in result.get("results", {}).get("bindings", []):
        rows.append({c: b[c]["value"] for c in cols if c in b})
    return cols, rows


def literal(value: str) -> str:
    """Escape a Python string as a SPARQL string literal."""
    esc = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{esc}"'
