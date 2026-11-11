"""Minimal SigV4 client for OpenSearch Serverless (service ``aoss``), stdlib + botocore only.

AOSS requires the ``x-amz-content-sha256`` header on signed requests with a body.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class AossError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"OpenSearch HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body


class IndexMissing(AossError):
    pass


Transport = Callable[[str, str, bytes, dict[str, str]], tuple[int, bytes]]


def _urllib_transport(method: str, url: str, body: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body or None, headers=headers, method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@dataclass
class AossClient:
    endpoint: str  # https://<id>.us-east-1.aoss.amazonaws.com
    region: str = "us-east-1"
    sign: bool = True
    transport: Transport = _urllib_transport

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.endpoint.rstrip("/") + "/" + path.lstrip("/")
        data = json.dumps(body).encode() if body is not None else b""
        headers = {"Content-Type": "application/json",
                   "x-amz-content-sha256": hashlib.sha256(data).hexdigest()}
        if self.sign:
            import botocore.session
            from botocore.auth import SigV4Auth
            from botocore.awsrequest import AWSRequest

            creds = botocore.session.get_session().get_credentials()
            req = AWSRequest(method=method, url=url, data=data, headers=headers)
            SigV4Auth(creds, "aoss", self.region).add_auth(req)
            headers = dict(req.headers)
        status, raw = self.transport(method, url, data, headers)
        text = raw.decode("utf-8", "replace")
        if status == 404 and "index_not_found" in text:
            raise IndexMissing(status, text)
        if status >= 300:
            raise AossError(status, text)
        return json.loads(text) if text else {}

    def search(self, index: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", f"{index}/_search", body)
