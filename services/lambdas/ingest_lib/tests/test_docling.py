"""docling-serve HTTP client against a local stub server, and the SQS task-token shim."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from evidence_core.testing import make_ctx
from lambdas.docling_call import handler as docling_call
from lambdas.ingest_lib.docling_client import DoclingFailed, DoclingPending, DoclingServeBackend, DoclingUnavailable
from lambdas.ingest_lib.pytest_fixtures import put_bronze
from local_pipeline import synthetic

DOC = {"schema_name": "DoclingDocument", "texts": [{"self_ref": "#/texts/0"}], "tables": [], "pictures": [],
       "key_value_items": [], "pages": {"1": {"size": {"width": 612, "height": 792}, "page_no": 1}}}


class _Stub(BaseHTTPRequestHandler):
    polls = 0
    bodies: list[bytes] = []
    fail = False

    def log_message(self, *args: Any) -> None:
        pass

    def _json(self, code: int, body: Any) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/ready":
            self._json(200, {"status": "ready"})
        elif self.path == "/version":
            self._json(200, {"docling-serve": "1.38.2", "docling": "2.145.0"})
        elif self.path.startswith("/v1/status/poll/t-1"):
            type(self).polls += 1
            status = "failure" if type(self).fail else ("success" if type(self).polls >= 2 else "started")
            self._json(200, {"task_id": "t-1", "task_status": status, "task_position": None})
        elif self.path.startswith("/v1/status/poll/gone-"):
            self._json(404, {"detail": "Task not found."})
        elif self.path == "/v1/result/t-1":
            self._json(200, {"document": {"filename": "x.pdf", "json_content": DOC}, "status": "success",
                             "errors": [], "processing_time": 1.2})
        else:
            self._json(404, {"detail": "nope"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        type(self).bodies.append(self.rfile.read(length))
        self._json(200, {"task_id": "t-1", "task_status": "pending", "task_position": 1})


@pytest.fixture
def server() -> Iterator[str]:
    _Stub.polls, _Stub.bodies, _Stub.fail = 0, [], False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_docling_serve_backend_round_trip(server: str) -> None:
    backend = DoclingServeBackend(server, poll_wait_s=0.01)
    ticks: list[str] = []
    assert backend.wait_ready(5, ticks.append) == "docling-serve 1.38.2"
    res = backend.convert(b"%PDF-1.7 fake", "w2.pdf", ticks.append)
    assert res.document == DOC and res.task_id == "t-1" and res.engine_version == "docling-serve 1.38.2"
    body = _Stub.bodies[0]
    assert b'name="to_formats"\r\n\r\njson' in body and b'name="do_ocr"\r\n\r\nfalse' in body
    assert b'filename="w2.pdf"' in body and b"%PDF-1.7 fake" in body
    assert any("started" in t for t in ticks)


def test_docling_serve_failure_and_unavailable(server: str) -> None:
    _Stub.fail = True
    with pytest.raises(DoclingFailed):
        DoclingServeBackend(server, poll_wait_s=0.01).convert(b"%PDF", "x.pdf", lambda m: None)
    with pytest.raises(DoclingUnavailable):
        DoclingServeBackend("http://127.0.0.1:9").wait_ready(0, lambda m: None)


def _message(ctx: Any) -> dict[str, Any]:
    body = {"taskToken": "tok-1", "ctx": ctx.dump(), "pages": [1], "executionArn": "arn:x"}
    return {"Records": [{"messageId": "m-1", "body": json.dumps(body)}]}


def test_shim_sends_task_success_with_extract_part(aws: Any, server: str) -> None:
    put_bronze(aws, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf())
    aws.docling_override = DoclingServeBackend(server, poll_wait_s=0.01)
    ctx = make_ctx(page_count=1, bucket="rp-data-test")
    out = docling_call.handler(_message(ctx))
    assert out == {"batchItemFailures": []}
    sent = aws.sfn.successes[0]
    part = json.loads(sent["output"])
    assert sent["token"] == "tok-1" and part["engine"] == "docling" and part["pages"] == [1]
    assert part["artifacts"][0]["key"].endswith("/2.3.1/extract/docling.json")
    assert part["artifacts"][0]["note"] == "docling-serve 1.38.2"


def test_shim_reports_failure_and_retries_when_unavailable(aws: Any, server: str) -> None:
    put_bronze(aws, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf())
    ctx = make_ctx(page_count=1, bucket="rp-data-test")
    _Stub.fail = True
    aws.docling_override = DoclingServeBackend(server, poll_wait_s=0.01)
    assert docling_call.handler(_message(ctx)) == {"batchItemFailures": []}
    assert aws.sfn.failures[0]["error"] == "Docling.DoclingFailed"

    class Down:
        def wait_ready(self, timeout_s: float, tick: Any) -> str:
            raise DoclingUnavailable("not ready")

    aws.docling_override = Down()
    assert docling_call.handler(_message(ctx)) == {"batchItemFailures": [{"itemIdentifier": "m-1"}]}


class _Ctx:
    def __init__(self, remaining_ms: int) -> None:
        self.remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self) -> int:
        return self.remaining_ms


def test_deadline_comes_from_the_lambda_context() -> None:
    import time

    now = time.monotonic()
    assert docling_call.invocation_deadline(_Ctx(900_000)) - now == pytest.approx(855, abs=1)
    assert docling_call.invocation_deadline(_Ctx(10_000)) - now == pytest.approx(0, abs=1)
    assert docling_call.invocation_deadline(None) - now == pytest.approx(docling_call.DEFAULT_BUDGET_S, abs=1)


def test_cold_start_heartbeats_until_the_deadline_then_retries(aws: Any) -> None:
    """Not ready: keep waiting for the whole invocation, heartbeat on the way out, retry the message."""
    put_bronze(aws, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf())
    ctx = make_ctx(page_count=1, bucket="rp-data-test")
    waited: list[float] = []

    class Down:
        def wait_ready(self, timeout_s: float, tick: Any) -> str:
            waited.append(timeout_s)
            raise DoclingUnavailable("not ready")

    aws.docling_override = Down()
    before = aws.sfn.heartbeats
    out = docling_call.handler(_message(ctx), _Ctx(900_000))
    assert out == {"batchItemFailures": [{"itemIdentifier": "m-1"}]}
    assert waited[0] == pytest.approx(855, abs=1)  # not a fixed 240 s
    assert aws.sfn.heartbeats > before and not aws.sfn.failures


def test_pending_conversion_resumes_the_same_task_on_retry(aws: Any, server: str) -> None:
    """At the deadline the task id is kept; the retry polls it instead of submitting the PDF again."""
    import time

    put_bronze(aws, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf())
    ctx = make_ctx(page_count=1, bucket="rp-data-test")
    backend = DoclingServeBackend(server, poll_wait_s=60)  # one poll needs 100 s of budget
    aws.docling_override = backend
    msg = docling_call.DoclingMessage.load(json.loads(_message(ctx)["Records"][0]["body"]))
    with pytest.raises(DoclingPending) as exc:
        docling_call.convert(msg, deadline=time.monotonic() + 95)  # submit fits, polling does not
    assert exc.value.task_id == "t-1" and len(_Stub.bodies) == 1
    out = docling_call.handler(_message(ctx), _Ctx(900_000))
    assert out == {"batchItemFailures": []}
    assert len(_Stub.bodies) == 1  # resumed, not resubmitted
    assert json.loads(aws.sfn.successes[-1]["output"])["engine"] == "docling"


def test_marker_from_another_execution_is_ignored(aws: Any, server: str) -> None:
    import time

    put_bronze(aws, "w2_2025_maya_fictus.pdf", synthetic.w2_pdf())
    ctx = make_ctx(page_count=1, bucket="rp-data-test")
    aws.docling_override = DoclingServeBackend(server, poll_wait_s=60)
    msg = docling_call.DoclingMessage.load(json.loads(_message(ctx)["Records"][0]["body"]))
    with pytest.raises(DoclingPending):
        docling_call.convert(msg, deadline=time.monotonic() + 95)
    other = json.loads(_message(ctx)["Records"][0]["body"]) | {"taskToken": "tok-2"}
    assert docling_call._read_marker(docling_call.DoclingMessage.load(other)) is None
    assert docling_call._read_marker(msg) is not None


def test_lost_task_is_resubmitted(server: str) -> None:
    backend = DoclingServeBackend(server, poll_wait_s=0.01)
    ticks: list[str] = []
    res = backend.convert(b"%PDF", "x.pdf", ticks.append, resume_task_id="gone-123")
    assert res.task_id == "t-1" and len(_Stub.bodies) == 1
    assert any("lost" in t for t in ticks)
