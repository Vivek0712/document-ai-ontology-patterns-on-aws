"""rp-textract-page (2.3.1, Map item): one scanned page -> Amazon Textract sync ``AnalyzeDocument``
(features from ``router.textractFeatures``), raw response to ``extract/textract/p{NNN}.json``.

Input: ``TextractPageInput {ctx, page, executionArn}``. Output: ``ExtractPart`` (engine textract).
Throttling errors propagate with their own names so the ASL Retry on ``ThrottlingException`` applies.
"""

from __future__ import annotations

import time
from typing import Any

from evidence_core import s3keys
from evidence_core.config import load_pipeline
from evidence_core.enums import ArtifactKind, Engine, StageId
from evidence_core.models.stage_outputs import TextractPageInput
from evidence_core.models.status import ExtractPart
from evidence_core.status import StatusWriter

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.pdf import subset_pdf
from lambdas.ingest_lib.stage_io import read_source, store_for
from lambdas.ingest_lib.textract_layout import textract_block_count, textractor_version

SYNC_MAX_BYTES = 10 * 1024 * 1024


def analyze_page(pdf_page: bytes, features: list[str]) -> dict[str, Any]:
    if len(pdf_page) > SYNC_MAX_BYTES:
        raise ValueError(f"page is {len(pdf_page)} bytes; sync AnalyzeDocument accepts 10 MB")
    resp = services().textract.analyze_document(Document={"Bytes": pdf_page}, FeatureTypes=features)
    resp.pop("ResponseMetadata", None)
    return resp


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    item = TextractPageInput.load(event)
    ctx = item.ctx
    svc = services()
    writer = StatusWriter(svc.table("doc_status"), ctx.doc_id, ctx.package_id, ctx.pipeline_version,
                          StageId.EXTRACT, svc.settings.heartbeat_seconds,
                          execution_arn=item.execution_arn or ctx.execution_arn)
    pipeline = load_pipeline(ctx.pipeline_version, version_id=ctx.config_version_id)
    router = pipeline.config.preprocessing.extraction.router
    features = router.textract_features if router else ["LAYOUT", "TABLES", "FORMS"]
    t0 = time.monotonic()
    page_pdf = subset_pdf(read_source(ctx), [item.page])
    resp = analyze_page(page_pdf, features)
    ms = int((time.monotonic() - t0) * 1000)
    blocks = textract_block_count(resp)
    key = s3keys.textract_page_key(ctx.package_id, ctx.doc_id, ctx.pipeline_version, item.page)
    ref = store_for(ctx).put_json(key, resp, kind=ArtifactKind.TEXTRACT_JSON, page=item.page, count=blocks,
                                  note=textractor_version())
    writer.heartbeat(message=f"Textract p{item.page:03d}: {blocks} layout blocks in {ms / 1000:.1f} s", force=True)
    return ExtractPart(engine=Engine.TEXTRACT, pages=[item.page], artifacts=[ref],
                       metrics={"layoutBlocks": blocks, "blocks": len(resp.get("Blocks", [])), "ms": ms,
                                "features": len(features)}, duration_ms=ms).dump()
