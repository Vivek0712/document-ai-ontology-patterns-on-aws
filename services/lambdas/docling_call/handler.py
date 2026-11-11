"""rp-docling-call (2.3.1; SQS trigger, batch 1, in the VPC): the shim between the task-token
state ``DoclingExtract`` and docling-serve on AWS Fargate.

For each ``DoclingMessage``: wait for docling-serve to be ready, convert the digital pages, write
``extract/docling.json`` (the DoclingDocument as returned) plus the ``docling_pages.json`` page map,
then ``SendTaskSuccess(ExtractPart)``. A conversion failure is reported with ``SendTaskFailure`` (no
SQS retry: the token is spent).

Time budget. Everything runs against one deadline taken from the invocation's remaining time minus
a margin, so the Lambda is never killed mid-poll:

- Cold start (the service scales from 0, ExtractOpen raises its floor): the shim keeps waiting and
  sends ``SendTaskHeartbeat`` every 30 s for the whole invocation, and the stage record says
  "waiting for docling-serve (cold start ...)". At the deadline it sends a last heartbeat and returns
  the message as a batch-item failure. The message is visible again when the 960 s visibility timeout
  ends, about 100 s later, well inside ``HeartbeatSeconds`` 600.
- Long conversion: the docling-serve task id is written to ``extract/docling_task.json`` at submit.
  At the deadline the shim returns the message for a retry, and the retry polls the same task instead
  of submitting the PDF again. The marker is bound to the task token, so a reprocess never resumes an
  older execution's task.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from evidence_core import s3keys
from evidence_core.enums import ArtifactKind, Engine, StageId
from evidence_core.logs import get_logger, log_kv
from evidence_core.models.stage_outputs import DoclingMessage
from evidence_core.models.status import ExtractPart, StageProgress
from evidence_core.status import StatusWriter

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.docling_client import DoclingPending, DoclingTaskLost, DoclingUnavailable
from lambdas.ingest_lib.docling_layout import docling_block_count
from lambdas.ingest_lib.pdf import subset_pdf
from lambdas.ingest_lib.stage_io import docling_pages_key, read_source, store_for

DEADLINE_MARGIN_S = 45.0  # left at the end of the invocation for the last heartbeat and the batch response
DEFAULT_BUDGET_S = 855.0  # no Lambda context (local runs, tests): the deployed 900 s minus the margin
HEARTBEAT_EVERY_S = 30.0  # well inside HeartbeatSeconds 600
log = get_logger("rp-docling-call")


def _ticker(writer: StatusWriter, token: str, pages: int) -> Callable[..., None]:
    sfn = services().sfn
    last = [0.0]

    def tick(message: str, *, force: bool = False) -> None:
        writer.heartbeat(StageProgress(done=0, total=pages, unit="pages"), message, force=force)
        now = time.monotonic()
        if force or now - last[0] >= HEARTBEAT_EVERY_S:
            sfn.send_task_heartbeat(taskToken=token)
            last[0] = now

    return tick


def invocation_deadline(context: Any) -> float:
    """``time.monotonic()`` value by which this invocation must stop working."""
    remaining = None
    if context is not None and hasattr(context, "get_remaining_time_in_millis"):
        remaining = context.get_remaining_time_in_millis() / 1000.0
    budget = (remaining - DEADLINE_MARGIN_S) if remaining is not None else DEFAULT_BUDGET_S
    return time.monotonic() + max(0.0, budget)


def _token_sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _read_marker(msg: DoclingMessage) -> dict[str, Any] | None:
    ctx = msg.ctx
    key = s3keys.docling_task_key(ctx.package_id, ctx.doc_id, ctx.pipeline_version)
    store = store_for(ctx)
    if not store.exists(key):
        return None
    marker = store.get_json(key)
    if marker.get("tokenSha") != _token_sha(msg.task_token) or marker.get("pages") != msg.pages:
        return None  # an older execution's task
    return dict(marker)


def _write_marker(msg: DoclingMessage, task_id: str | None) -> None:
    ctx = msg.ctx
    key = s3keys.docling_task_key(ctx.package_id, ctx.doc_id, ctx.pipeline_version)
    body = {"taskId": task_id, "tokenSha": _token_sha(msg.task_token), "pages": msg.pages,
            "submittedAt": time.time() if task_id else None}
    store_for(ctx).put_json(key, body, kind=ArtifactKind.DOCLING_JSON)


def convert(msg: DoclingMessage, deadline: float) -> ExtractPart:
    ctx = msg.ctx
    svc = services()
    writer = StatusWriter(svc.table("doc_status"), ctx.doc_id, ctx.package_id, ctx.pipeline_version,
                          StageId.EXTRACT, svc.settings.heartbeat_seconds,
                          execution_arn=msg.execution_arn or ctx.execution_arn)
    tick = _ticker(writer, msg.task_token, len(msg.pages))
    backend = svc.docling()
    try:
        version = backend.wait_ready(max(0.0, deadline - time.monotonic()), tick)
        marker = _read_marker(msg)
        t0 = time.monotonic()
        source = read_source(ctx)
        whole = msg.pages == list(range(1, ctx.page_count + 1))
        pdf = source if whole else subset_pdf(source, msg.pages)
        page_map = {i + 1: p for i, p in enumerate(msg.pages)}
        result = backend.convert(pdf, ctx.file_name, tick, deadline=deadline,
                                 resume_task_id=(marker or {}).get("taskId"),
                                 submitted_at=(marker or {}).get("submittedAt"),
                                 on_submit=lambda task_id: _write_marker(msg, task_id))
    except DoclingTaskLost:
        _write_marker(msg, None)
        tick("docling-serve lost the task (service restarted); retrying", force=True)
        raise
    except DoclingPending as exc:
        tick(f"docling task {exc.task_id[:8]} still running; resuming on the next attempt", force=True)
        raise
    except DoclingUnavailable:
        tick("waiting for docling-serve (cold start); retrying on the next attempt", force=True)
        raise
    store = store_for(ctx)
    blocks = docling_block_count(result.document)
    ref = store.put_json(s3keys.docling_key(ctx.package_id, ctx.doc_id, ctx.pipeline_version), result.document,
                         kind=ArtifactKind.DOCLING_JSON, count=blocks, note=result.engine_version)
    store.put_json(docling_pages_key(ctx.package_id, ctx.doc_id, ctx.pipeline_version),
                   {"pageMap": {str(k): v for k, v in page_map.items()}, "subset": not whole,
                    "taskId": result.task_id, "engineVersion": version},
                   kind=ArtifactKind.DOCLING_JSON)
    ms = int((time.monotonic() - t0) * 1000)
    writer.heartbeat(StageProgress(done=len(msg.pages), total=len(msg.pages)),
                     f"Docling: {len(msg.pages)} page(s), {blocks} items in {result.processing_ms / 1000:.1f} s",
                     force=True)
    return ExtractPart(engine=Engine.DOCLING, pages=msg.pages, artifacts=[ref],
                       metrics={"items": blocks, "processingMs": result.processing_ms, "ms": ms,
                                "subset": int(not whole)}, duration_ms=ms)


def handle_message(body: str, deadline: float) -> None:
    msg = DoclingMessage.load(json.loads(body))
    sfn = services().sfn
    try:
        part = convert(msg, deadline)
    except DoclingUnavailable:
        raise  # cold start, pending conversion or lost task: the message is retried
    except Exception as exc:  # noqa: BLE001 - reported to Step Functions, which routes to LaneFailed
        log_kv(log, "docling conversion failed", docId=msg.ctx.doc_id, error=type(exc).__name__)
        sfn.send_task_failure(taskToken=msg.task_token, error=f"Docling.{type(exc).__name__}",
                              cause=str(exc)[:32000])
        return
    sfn.send_task_success(taskToken=msg.task_token, output=part.dump_json())
    log_kv(log, "docling conversion sent", docId=msg.ctx.doc_id, pages=len(msg.pages))


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    deadline = invocation_deadline(context)
    failures = []
    for record in event.get("Records", []):
        try:
            handle_message(record["body"], deadline)
        except DoclingUnavailable as exc:
            log_kv(log, "docling message returned for retry", reason=type(exc).__name__, detail=str(exc)[:300])
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
