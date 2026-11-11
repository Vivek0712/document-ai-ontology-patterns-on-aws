"""Helpers stage handlers share: source object, stage-to-stage artifact lookup, layout loading."""

from __future__ import annotations

from typing import Any

from evidence_core import s3keys
from evidence_core.enums import ArtifactKind, StageId
from evidence_core.models.stage_outputs import PageInfo, RegisterOutput
from evidence_core.models.status import ArtifactRef, DocCtx, StageEnvelope

from .aws import services
from .docling_layout import docling_to_layout
from .layout import DocLayout
from .store import DataStore
from .textract_layout import textract_to_layout
from .vocab import VocabIndex


def store_for(ctx: DocCtx) -> DataStore:
    return DataStore(ctx.bucket, services().s3)


def source_ref(ctx: DocCtx) -> ArtifactRef:
    return ArtifactRef(key=ctx.bronze_key, kind=ArtifactKind.SOURCE_PDF, content_type=ctx.content_type,
                       size_bytes=ctx.size_bytes, sha256=ctx.source_sha256, version_id=ctx.source_version_id)


def read_source(ctx: DocCtx) -> bytes:
    return store_for(ctx).get_bytes(ctx.bronze_key, ctx.source_version_id)


def artifacts(env: StageEnvelope, stage: StageId, *kinds: ArtifactKind) -> list[ArtifactRef]:
    res = env.result(stage)
    if res is None:
        return []
    return [a for a in res.artifacts if not kinds or a.kind in kinds]


def require_artifact(env: StageEnvelope, stage: StageId, kind: ArtifactKind) -> ArtifactRef:
    found = artifacts(env, stage, kind)
    if not found:
        raise RuntimeError(f"stage {stage.value} produced no {kind.value} artifact")
    return found[0]


def pages_info(env: StageEnvelope) -> dict[int, PageInfo]:
    """Page sizes from the register manifest (StageResult.output), else from S3."""
    res = env.result(StageId.REGISTER)
    if res is not None and res.output.get("pages"):
        out = RegisterOutput.load(res.output)
    else:
        ctx = env.ctx
        out = RegisterOutput.load(store_for(ctx).get_json(s3keys.manifest_key(ctx.package_id, ctx.doc_id,
                                                                              ctx.pipeline_version)))
    return {p.page: p for p in out.pages}


docling_pages_key = s3keys.docling_pages_key


def load_layout(env: StageEnvelope, vocab: VocabIndex) -> tuple[DocLayout, dict[str, str], list[str]]:
    """Merged layout of every extract artifact. Returns (layout, engine versions, artifact keys)."""
    ctx = env.ctx
    store = store_for(ctx)
    pinfo = pages_info(env)
    parts: list[DocLayout] = []
    versions: dict[str, str] = {}
    keys: list[str] = []
    for ref in artifacts(env, StageId.EXTRACT, ArtifactKind.DOCLING_JSON, ArtifactKind.TEXTRACT_JSON):
        keys.append(ref.key)
        data: Any = store.get_json(ref.key, ref.version_id)
        if ref.kind is ArtifactKind.DOCLING_JSON:
            pm_key = docling_pages_key(ctx.package_id, ctx.doc_id, ctx.pipeline_version)
            page_map = None
            try:
                raw = store.get_json(pm_key)
                page_map = {int(k): int(v) for k, v in raw.get("pageMap", {}).items()}
            except Exception:  # noqa: BLE001 - absent sidecar means identity mapping
                page_map = None
            parts.append(docling_to_layout(data, artifact_key=ref.key, vocab=vocab, page_map=page_map))
            if ref.note:
                versions["docling"] = ref.note
        else:
            page = int(ref.page or 1)
            pi = pinfo.get(page)
            w, h = (pi.width, pi.height) if pi else (612.0, 792.0)
            parts.append(textract_to_layout(data, page=page, page_width=w, page_height=h, artifact_key=ref.key,
                                            vocab=vocab))
            if ref.note:
                versions["textract"] = ref.note
    return DocLayout.merge(parts), versions, keys
