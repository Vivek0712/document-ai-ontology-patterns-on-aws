"""Minimal client for the Provenant REST API (rp-api, stage v1) including the /ask SSE stream."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from evidence_core.models.answer import AskEvent, AskRequest
from pydantic import TypeAdapter

_ASK_EVENT: TypeAdapter[Any] = TypeAdapter(AskEvent)
RUNTIME_SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"


class ApiHttpError(RuntimeError):
    def __init__(self, status: int, body: Any, path: str):
        super().__init__(f"{status} on {path}: {str(body)[:300]}")
        self.status = status
        self.body = body


def parse_sse(lines: Iterable[str]) -> Iterator[dict[str, Any]]:
    """Yield the JSON payload of each ``data:`` frame. Handles a buffered body (all frames at once)
    and multi-line data fields; ignores comments (``:``) and ``event:``/``id:`` lines."""
    buf: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if line == "":
            if buf:
                yield json.loads("\n".join(buf))
                buf = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            buf.append(line[5:].lstrip(" "))
    if buf:
        yield json.loads("\n".join(buf))


def parse_ask_events(lines: Iterable[str]) -> Iterator[Any]:
    """Typed AskEvents (RunStartEvent, ToolEndEvent, AnswerEvent, ...)."""
    for payload in parse_sse(lines):
        yield _ASK_EVENT.validate_python(payload)


@dataclass
class ApiClient:
    base_url: str
    authorization: str
    timeout: float = 60.0
    ask_timeout: float = 300.0
    # POST the ask here instead of {base_url}/ask: the AgentCore Runtime invocations URL (SSM /rp/agent/runtime-url).
    # The API's /ask refuses a caller outside the package ACL before the agent runs; straight at the Runtime the
    # agent runs and its tools apply the caller's identity (SC-7: another user's runs are logged, scoped to nothing).
    runtime_url: str | None = None

    def _url(self, path: str, query: dict[str, Any] | None = None) -> str:
        url = self.base_url.rstrip("/") + "/" + path.lstrip("/")
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        return url

    def request(self, method: str, path: str, *, body: Any = None, query: dict[str, Any] | None = None,
                authorization: str | None = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(  # noqa: S310 (https from SSM)
            self._url(path, query), data=data, method=method,
            headers={"Authorization": authorization or self.authorization, "Content-Type": "application/json",
                     "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (https from SSM)
                raw = resp.read().decode()
                return resp.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return exc.code, raw

    def get(self, path: str, **query: Any) -> Any:
        status, body = self.request("GET", path, query=query or None)
        if status >= 400:
            raise ApiHttpError(status, body, path)
        return body

    def post(self, path: str, body: Any) -> Any:
        status, body_out = self.request("POST", path, body=body)
        if status >= 400:
            raise ApiHttpError(status, body_out, path)
        return body_out

    def ask(self, req: AskRequest) -> Iterator[Any]:
        """POST /ask and yield typed AskEvents as they stream."""
        headers = {"Authorization": self.authorization, "Content-Type": "application/json",
                   "Accept": "text/event-stream"}
        if self.runtime_url:
            headers[RUNTIME_SESSION_HEADER] = f"rp-ask-{uuid.uuid4().hex}"  # the Runtime requires >= 33 chars
        http_req = urllib.request.Request(  # noqa: S310 (https from SSM)
            self.runtime_url or self._url("ask"), data=req.dump_json().encode(), method="POST", headers=headers,
        )
        try:
            with urllib.request.urlopen(http_req, timeout=self.ask_timeout) as resp:  # noqa: S310
                lines = (raw.decode("utf-8") for raw in resp)
                yield from parse_ask_events(lines)
        except urllib.error.HTTPError as exc:
            raise ApiHttpError(exc.code, exc.read().decode()[:2000], "ask") from exc
