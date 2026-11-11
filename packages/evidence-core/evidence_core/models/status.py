"""Ingestion status contracts: Step Functions envelopes, stage records, events, lanes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from ..base import RpModel
from ..enums import ArtifactKind, Engine, LaneState, StageEventType, StageGroup, StageId, StageState


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    """ISO-8601 UTC with milliseconds and ``Z``: ``2026-11-16T17:04:05.123Z``."""
    d = (dt or utcnow()).astimezone(UTC)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


class ArtifactRef(RpModel):
    """A file a stage read or wrote. Always in the data bucket (RP_DATA_BUCKET)."""

    key: str
    kind: ArtifactKind
    content_type: str = "application/json"
    size_bytes: int | None = None
    sha256: str | None = None
    version_id: str | None = None
    page: int | None = None
    count: int | None = None  # lines in a JSONL, blocks in a layout JSON, units in a receipt
    note: str | None = None


class StageError(RpModel):
    type: str  # exception class or Step Functions error name (States.Timeout, ...)
    message: str
    cause: str | None = None  # truncated to 4 KB
    retryable: bool = False
    at: str = Field(default_factory=iso)


class StageProgress(RpModel):
    done: int
    total: int | None = None
    unit: str = "pages"


class StageVersions(RpModel):
    """Everything that determines a stage's output, shown in the stage inspector."""

    pipeline_version: str
    config_sha: str
    config_version_id: str | None = None
    ontology_version: str | None = None
    engine_version: str | None = None  # "docling-serve-cpu 1.38.2", "pypdf 5.4.0", "textract 2018-06-27"
    model_id: str | None = None  # Bedrock model when the stage calls one (classify tie-break, embed)
    prompt_sha: str | None = None
    code_sha: str | None = None  # RP_CODE_SHA baked at build time


class StageConfigSnapshot(RpModel):
    """The slice(s) of the resolved pipeline config this stage used."""

    paths: list[str]  # e.g. ["preprocessing.units", "preprocessing.enrichment"]
    values: dict[str, Any]
    sha: str  # sha256 of canonical JSON of `values`


# --- Step Functions payloads -------------------------------------------------------------

class IngestTrigger(RpModel):
    """Execution input. EventBridge input transformer (trigger=s3) or POST reprocess."""

    pipeline_version: str
    bucket: str
    key: str
    version_id: str | None = None
    etag: str | None = None
    size: int | None = None
    event_time: str | None = None
    trigger: Literal["s3", "reprocess"] = "s3"
    requested_by: str | None = None  # Cognito sub for reprocess


class DocCtx(RpModel):
    """Per-execution context built by `register`; passed unchanged to every later state."""

    package_id: str
    doc_id: str
    pipeline_version: str
    mode: Literal["naive", "evidence_units"]
    owner_sub: str
    file_name: str
    bucket: str
    bronze_key: str
    source_version_id: str | None = None
    source_sha256: str
    size_bytes: int
    content_type: str = "application/pdf"
    page_count: int
    config_key: str
    config_version_id: str | None = None
    config_sha: str
    prompt_sha: str
    ontology_version: str | None = None
    route_enabled: bool
    classify_enabled: bool
    graph_enabled: bool
    allowed_principals: list[str]
    execution_arn: str
    lane_started_at: str
    trigger: Literal["s3", "reprocess"] = "s3"


class StageResult(RpModel):
    """What every stage Lambda returns to Step Functions (stored at ``$.stages.<stageId>``).

    Keep it small (< 8 KB): large outputs go to S3 and appear as artifacts.
    """

    stage_id: StageId
    state: StageState
    engine: Engine | None = None
    headline: str | None = None  # one line for the board cell: "4 units", "Docling 00:11"
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    metrics: dict[str, float | int] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)  # stage-specific: the stage's own output keys
    duration_ms: int | None = None


class ExtractPart(RpModel):
    """One branch result inside the extract Parallel (docling for digital pages, textract per scan page)."""

    engine: Literal[Engine.DOCLING, Engine.TEXTRACT]
    pages: list[int]
    artifacts: list[ArtifactRef]
    metrics: dict[str, float | int] = Field(default_factory=dict)
    duration_ms: int | None = None


class StageEnvelope(RpModel):
    """Input to every stage Lambda after `register` (Parameters block in the ASL)."""

    ctx: DocCtx
    stages: dict[str, StageResult] = Field(default_factory=dict)
    execution_arn: str | None = None
    state_name: str | None = None
    attempt: int = 0
    extract_parts: list[Any] | None = None  # raw Parallel output, consumed by ExtractClose
    error: dict[str, Any] | None = None  # Catch output {Error, Cause}, consumed by LaneFailed

    def result(self, stage: StageId) -> StageResult | None:
        return self.stages.get(stage.value)


# --- DynamoDB records (rp-doc-status) -------------------------------------------------------

class StageRecord(RpModel):
    """Current state of one stage of one (doc, pipelineVersion) lane.

    Key: pk = docId, sk = ``STAGE#{pv}#{order:02d}#{stageId}``;
    GSI ``byPackage``: gsi1pk = packageId, gsi1sk = ``{pv}#{docId}#S{order:02d}#{stageId}``.
    """

    doc_id: str
    package_id: str
    pipeline_version: str
    stage_id: StageId
    order: int
    group: StageGroup
    title: str
    state: StageState = StageState.PENDING
    attempt: int = 0
    engine: Engine | None = None
    services: list[str] = Field(default_factory=list)
    function_name: str | None = None
    sfn_state: str | None = None
    execution_arn: str | None = None
    queued_at: str | None = None
    started_at: str | None = None
    last_heartbeat_at: str | None = None
    heartbeat_count: int = 0
    ended_at: str | None = None
    duration_ms: int | None = None
    progress: StageProgress | None = None
    message: str | None = None
    headline: str | None = None
    inputs: list[ArtifactRef] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    input: dict[str, Any] | None = None  # small summary of what the stage was given
    output: dict[str, Any] | None = None  # StageResult.output
    metrics: dict[str, float | int] = Field(default_factory=dict)
    config: StageConfigSnapshot | None = None
    versions: StageVersions | None = None
    skip_reason: str | None = None
    error: StageError | None = None
    updated_at: str = Field(default_factory=iso)


class StageEvent(RpModel):
    """Append-only event. Key: pk = docId, sk = ``EVENT#{pv}#{at}#{stageId}#{event}``; TTL 30 days."""

    doc_id: str
    package_id: str
    pipeline_version: str
    stage_id: StageId
    event: StageEventType
    at: str = Field(default_factory=iso)
    attempt: int = 0
    message: str | None = None
    progress: StageProgress | None = None
    duration_ms: int | None = None
    error: StageError | None = None
    execution_arn: str | None = None  # the run that wrote it (the inspector filters by the lane's current run)


class LaneRecord(RpModel):
    """One (doc, pipelineVersion) lane = one execution. Key: pk = docId, sk = ``LANE#{pv}``;
    gsi1sk = ``{pv}#{docId}#LANE``."""

    doc_id: str
    package_id: str
    pipeline_version: str
    state: LaneState = LaneState.PENDING
    current_stage: StageId | None = None
    execution_arn: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    config_sha: str | None = None
    config_version_id: str | None = None
    prompt_sha: str | None = None
    ontology_version: str | None = None
    units: int | None = None
    held: int | None = None
    indexed: int | None = None
    triples: int | None = None
    skip_reason: str | None = None
    error: StageError | None = None
    updated_at: str = Field(default_factory=iso)
