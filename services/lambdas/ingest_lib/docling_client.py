"""Docling backends.

- ``DoclingServeBackend``: the deployed path. HTTP client for upstream ``docling-serve`` (v1 API)
  running on AWS Fargate at ``RP_DOCLING_URL``: ``GET /health`` + ``GET /ready``,
  ``POST /v1/convert/file/async``, ``GET /v1/status/poll/{task_id}?wait=``, ``GET /v1/result/{task_id}``.
  Standard library only (the caller runs in a VPC Lambda).
- ``LocalDoclingBackend``: dev only (``make units``), the ``docling`` Python package in-process.
- ``TextRunLayoutBackend``: dev only fallback when Docling is not installed; pypdfium2 text runs
  shaped like a DoclingDocument, every item labelled ``text``. Its engine version says so.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .glyphs import pdfium_text

Tick = Callable[[str], None]  # progress callback: message -> None (stage heartbeat + SendTaskHeartbeat)

CONVERT_OPTIONS: dict[str, str | list[str]] = {
    "to_formats": ["json"],
    "image_export_mode": "placeholder",
    "do_ocr": "false",  # digital pages only (the router sends scans to Textract)
    "do_table_structure": "true",
    "table_mode": "accurate",
    "abort_on_error": "true",
}


class DoclingUnavailable(RuntimeError):
    """docling-serve did not become healthy before the invocation deadline (the SQS message is retried)."""


class DoclingPending(DoclingUnavailable):
    """The conversion is still running at the invocation deadline. The SQS retry resumes it by task id."""

    def __init__(self, task_id: str, message: str) -> None:
        super().__init__(message)
        self.task_id = task_id


class DoclingTaskLost(DoclingUnavailable):
    """docling-serve no longer knows the task (the Fargate task restarted). The retry submits again."""


class DoclingFailed(RuntimeError):
    """docling-serve reported a failed conversion (the task token is failed, not retried)."""


@dataclass
class DoclingResult:
    document: dict[str, Any]  # DoclingDocument JSON (json_content)
    engine_version: str
    processing_ms: int
    task_id: str | None = None
    status: str = "success"
    errors: list[Any] | None = None


class DoclingBackend(Protocol):
    def wait_ready(self, timeout_s: float, tick: Tick) -> str: ...

    def convert(self, pdf: bytes, file_name: str, tick: Tick, *, deadline: float | None = None,
                resume_task_id: str | None = None, submitted_at: float | None = None,
                on_submit: Callable[[str], None] | None = None) -> DoclingResult: ...


def _multipart(fields: dict[str, str | list[str]], file_field: str, file_name: str, data: bytes,
               content_type: str = "application/pdf") -> tuple[bytes, str]:
    boundary = f"rp-{uuid.uuid4().hex}"
    out = bytearray()
    for name, value in fields.items():
        for v in value if isinstance(value, list) else [value]:
            out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{v}\r\n".encode()
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; filename=\"{file_name}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n").encode()
    out += data + b"\r\n" + f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


@dataclass
class DoclingServeBackend:
    """One docling-serve instance (the Fargate service runs exactly one task: async task state lives in
    that task's memory with the default ``local`` engine, so submit, poll and result must reach it).

    ``deadline`` (``time.monotonic()``) is the end of the caller's invocation budget. When a conversion
    is still running at the deadline, ``convert`` raises ``DoclingPending`` with the task id; the next
    attempt passes it back as ``resume_task_id`` and keeps polling the same task.
    """

    base_url: str
    request_timeout_s: float = 30.0
    poll_wait_s: float = 5.0
    convert_timeout_s: float = 1800.0  # total since submit, across resumed attempts
    _version: str | None = None

    def _url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    def _request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None,
                 timeout: float | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(self._url(path), data=body, method=method, headers=headers or {})  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.request_timeout_s) as resp:  # noqa: S310
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _get_json(self, path: str, timeout: float | None = None) -> Any:
        code, body = self._request("GET", path, timeout=timeout)
        if code >= 400:
            raise DoclingFailed(f"GET {path} -> HTTP {code}: {body[:300]!r}")
        return json.loads(body)

    def _get_task(self, path: str, task_id: str, timeout: float) -> Any:
        code, body = self._request("GET", path, timeout=timeout)
        if code == 404:
            raise DoclingTaskLost(f"docling-serve does not know task {task_id} (service restarted?)")
        if code >= 400:
            raise DoclingFailed(f"GET {path} -> HTTP {code}: {body[:300]!r}")
        return json.loads(body)

    def wait_ready(self, timeout_s: float, tick: Tick) -> str:
        """Poll /health then /ready until models are loaded. Returns the engine version string."""
        deadline = time.monotonic() + timeout_s
        last_err = "no response"
        while True:
            try:
                code, _ = self._request("GET", "/health", timeout=5)
                if code == 200:
                    rcode, _ = self._request("GET", "/ready", timeout=5)
                    if rcode in (200, 404):  # /ready absent on older servers
                        return self.version()
                    last_err = f"/ready HTTP {rcode}"
                else:
                    last_err = f"/health HTTP {code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
                last_err = type(e).__name__
            if time.monotonic() >= deadline:
                break
            tick(f"waiting for docling-serve (cold start: Fargate task starting, image pull, model load; {last_err})")
            time.sleep(min(10.0, max(0.0, deadline - time.monotonic())))
        raise DoclingUnavailable(f"docling-serve not ready after {int(timeout_s)} s ({last_err})")

    def version(self) -> str:
        if self._version is None:
            try:
                info = self._get_json("/version", timeout=5)
                ver = info.get("docling-serve") or info.get("docling_serve") or info.get("docling")
                self._version = f"docling-serve {ver}" if ver else "docling-serve"
            except Exception:  # noqa: BLE001 - version endpoint can be disabled
                self._version = "docling-serve"
        return self._version

    def _submit(self, pdf: bytes, file_name: str) -> dict[str, Any]:
        body, ctype = _multipart(CONVERT_OPTIONS, "files", file_name, pdf)
        code, raw = self._request("POST", "/v1/convert/file/async", body, {"Content-Type": ctype}, timeout=60)
        if code >= 400:
            raise DoclingFailed(f"convert submit HTTP {code}: {raw[:300]!r}")
        return dict(json.loads(raw))

    def convert(self, pdf: bytes, file_name: str, tick: Tick, *, deadline: float | None = None,
                resume_task_id: str | None = None, submitted_at: float | None = None,
                on_submit: Callable[[str], None] | None = None) -> DoclingResult:
        t0 = time.monotonic()

        def left() -> float:
            return float("inf") if deadline is None else deadline - time.monotonic()

        task: dict[str, Any] | None = None
        if resume_task_id:
            try:
                task = self._get_task(f"/v1/status/poll/{resume_task_id}?wait=0", resume_task_id, timeout=30)
                tick(f"resuming docling task {resume_task_id[:8]} ({task.get('task_status', 'pending')})")
            except DoclingTaskLost:
                tick(f"docling task {resume_task_id[:8]} was lost; submitting again")
                task, submitted_at = None, None
        if task is None:
            if left() < 90:
                raise DoclingUnavailable("too little time left in this invocation to submit; retrying")
            task = self._submit(pdf, file_name)
            submitted_at = time.time()
            if on_submit is not None:
                on_submit(str(task["task_id"]))
        task_id = str(task["task_id"])
        started = submitted_at if submitted_at is not None else time.time()
        status = task.get("task_status", "pending")
        while status in ("pending", "started"):
            if time.time() - started > self.convert_timeout_s:
                raise DoclingFailed(f"conversion {task_id} exceeded {int(self.convert_timeout_s)} s")
            if left() < self.poll_wait_s + 40:
                raise DoclingPending(task_id, f"docling task {task_id} still {status} at the invocation deadline")
            pos = task.get("task_position")
            tick(f"docling task {task_id[:8]} {status}" + (f" (queue position {pos})" if pos else ""))
            task = self._get_task(f"/v1/status/poll/{task_id}?wait={self.poll_wait_s}", task_id,
                                  timeout=self.poll_wait_s + 30)
            status = task.get("task_status", "pending")
        if status not in ("success", "partial_success"):
            raise DoclingFailed(f"docling task {task_id} ended {status}: {task.get('error_message') or ''}")
        if left() < 45:
            raise DoclingPending(task_id, f"docling task {task_id} finished; fetching the result on the retry")
        result = self._get_task(f"/v1/result/{task_id}", task_id, timeout=min(120.0, max(10.0, left() - 20)))
        doc = (result.get("document") or {}).get("json_content")
        if not doc:
            raise DoclingFailed(f"docling task {task_id} returned no json_content")
        return DoclingResult(document=doc, engine_version=self.version(),
                             processing_ms=int((time.monotonic() - t0) * 1000), task_id=task_id,
                             status=str(result.get("status", status)), errors=result.get("errors"))


@dataclass
class LocalDoclingBackend:
    """In-process Docling (dev only). Same options as the deployed request: no OCR, accurate tables."""

    _converter: Any = None

    @staticmethod
    def available() -> bool:
        try:
            import docling  # noqa: F401
        except ImportError:
            return False
        return True

    def wait_ready(self, timeout_s: float, tick: Tick) -> str:
        return self.version()

    def version(self) -> str:
        from importlib.metadata import version

        return f"docling {version('docling')} (local, dev mode)"

    def convert(self, pdf: bytes, file_name: str, tick: Tick, **_: Any) -> DoclingResult:
        import io

        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption

        if self._converter is None:
            opts = PdfPipelineOptions()
            opts.do_ocr = False
            opts.do_table_structure = True
            opts.table_structure_options.mode = TableFormerMode.ACCURATE
            fmt = {InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            self._converter = DocumentConverter(format_options=fmt)
        t0 = time.monotonic()
        tick("docling (local) converting")
        res = self._converter.convert(DocumentStream(name=file_name, stream=io.BytesIO(pdf)))
        return DoclingResult(document=res.document.export_to_dict(), engine_version=self.version(),
                             processing_ms=int((time.monotonic() - t0) * 1000), status=str(res.status.value))


@dataclass
class TextRunLayoutBackend:
    """Fallback when Docling is not installed (dev only): one ``text`` item per pypdfium2 text run,
    in a DoclingDocument-shaped dict. No tables, no roles beyond ``text``."""

    def wait_ready(self, timeout_s: float, tick: Tick) -> str:
        return self.version()

    @staticmethod
    def version() -> str:
        import pypdfium2

        return f"FALLBACK pypdfium2 {pypdfium2.version.PYPDFIUM_INFO} text runs (docling not installed)"

    def convert(self, pdf: bytes, file_name: str, tick: Tick, **_: Any) -> DoclingResult:
        import pypdfium2 as pdfium

        t0 = time.monotonic()
        doc = pdfium.PdfDocument(pdf)
        texts: list[dict[str, Any]] = []
        pages: dict[str, Any] = {}
        try:
            for i in range(len(doc)):
                page = doc[i]
                w, h = page.get_size()
                pages[str(i + 1)] = {"size": {"width": w, "height": h}, "page_no": i + 1}
                tp = page.get_textpage()
                for j in range(tp.count_rects()):
                    left, bottom, right, top = tp.get_rect(j)
                    text = pdfium_text(tp.get_text_bounded(left, bottom, right, top))
                    if not text:
                        continue
                    texts.append({
                        "self_ref": f"#/texts/{len(texts)}", "label": "text", "content_layer": "body",
                        "text": text, "orig": text,
                        "prov": [{"page_no": i + 1,
                                  "bbox": {"l": left, "t": top, "r": right, "b": bottom, "coord_origin": "BOTTOMLEFT"},
                                  "charspan": [0, len(text)]}],
                    })
        finally:
            doc.close()
        body = {"self_ref": "#/body", "children": [{"$ref": t["self_ref"]} for t in texts], "name": "_root_",
                "label": "unspecified", "content_layer": "body"}
        out = {"schema_name": "DoclingDocument", "version": "fallback", "name": file_name, "body": body,
               "furniture": {"self_ref": "#/furniture", "children": []}, "groups": [], "texts": texts,
               "pictures": [], "tables": [], "key_value_items": [], "form_items": [], "pages": pages}
        return DoclingResult(document=out, engine_version=self.version(),
                             processing_ms=int((time.monotonic() - t0) * 1000))
