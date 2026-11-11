"""REST API request/response models (mirrored in contracts/api.ts). Routes: services/lambdas/api/routes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..base import RpModel
from ..bbox import BBox
from ..config import ConfigDiff
from ..enums import Engine, LaneState, PiiFlag, StageGroup, StageId, StageState, UnitType
from ..stages import ResolvedStage
from .answer import RunRecord, RunSummary
from .status import ArtifactRef, LaneRecord, StageError, StageEvent, StageProgress, StageRecord
from .units import PiiDecision, ReviewItem, Unit, UnitStatusItem

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_URL_TTL_SECONDS = 300
REGION_URL_TTL_SECONDS = 300
ARTIFACT_PREVIEW_MAX_BYTES = 64 * 1024


class ApiErrorBody(RpModel):
    code: Literal["bad_request", "unauthorized", "forbidden", "not_found", "conflict", "not_ready", "internal"]
    message: str
    request_id: str | None = None


class ApiError(RpModel):
    error: ApiErrorBody


# --- identity -------------------------------------------------------------------------------

class Me(RpModel):
    sub: str
    username: str
    email: str | None = None
    groups: list[str]
    principals: list[str]


# --- packages ---------------------------------------------------------------------------------

class LaneDocCounts(RpModel):
    total: int = 0
    pending: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0


class LaneRollup(RpModel):
    """Package-level readiness per pipeline version (rp-status-stream writes it)."""

    state: Literal["EMPTY", "PENDING", "RUNNING", "SUCCEEDED", "FAILED", "PARTIAL"]
    ready: bool  # every non-skipped doc lane SUCCEEDED and indexed == expected
    indexed: int = 0  # units indexed in AOSS for this lane
    expected: int = 0  # passed units (units - held) for this lane
    docs: LaneDocCounts = Field(default_factory=LaneDocCounts)


class Counters(RpModel):
    units: int = 0
    held: int = 0
    indexed: int = 0
    triples: int = 0


class Package(RpModel):
    """Table rp-packages (pk packageId; GSI byOwner ownerSub + createdAt)."""

    package_id: str
    name: str
    description: str | None = None
    owner_sub: str
    created_at: str
    updated_at: str | None = None
    allowed_principals: list[str]
    pipeline_versions: list[str] = Field(default_factory=lambda: ["1.0", "2.3.1"])
    doc_count: int = 0
    lanes: dict[str, LaneRollup] = Field(default_factory=dict)
    counters: dict[str, Counters] = Field(default_factory=dict)


class PackageCreate(RpModel):
    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    share_with_groups: list[str] = Field(default_factory=list)  # must be a subset of the caller's groups
    pipeline_versions: list[str] = Field(default_factory=lambda: ["1.0", "2.3.1"])


class PackageList(RpModel):
    items: list[Package]


class DocumentItem(RpModel):
    """Table rp-documents (pk packageId, sk docId; GSI byDocId)."""

    package_id: str
    doc_id: str
    file_name: str
    bronze_key: str
    content_type: str = "application/pdf"
    size_bytes: int | None = None
    sha256: str | None = None
    source_version_id: str | None = None
    page_count: int | None = None
    doc_type: str | None = None
    uploaded_by: str
    upload_state: Literal["AWAITING_UPLOAD", "IN_BRONZE", "QUARANTINED"] = "AWAITING_UPLOAD"
    upload_url_issued_at: str
    in_bronze_at: str | None = None
    quarantine_reason: str | None = None
    quarantine_key: str | None = None


class DocAclItem(RpModel):
    """Table rp-doc-acl (pk docId). Written by upload_url (initial) and register (confirmed)."""

    doc_id: str
    package_id: str
    owner_sub: str
    allowed_principals: list[str]


class PackageDetail(RpModel):
    package: Package
    documents: list[DocumentItem]
    can_write: bool  # caller is the owner


class UploadUrlRequest(RpModel):
    file_name: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._ -]+\.pdf$")
    content_type: Literal["application/pdf"] = "application/pdf"
    size_bytes: int = Field(gt=0, le=MAX_UPLOAD_BYTES)


class UploadUrlResponse(RpModel):
    doc_id: str
    key: str  # bronze/{sub}/{packageId}/{fileName}
    url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]  # must be sent with the PUT, e.g. {"Content-Type": "application/pdf"}
    expires_in: int = UPLOAD_URL_TTL_SECONDS


class DeletePackageResponse(RpModel):
    package_id: str
    deleted: dict[str, int]  # {"s3Objects": n, "docStatusItems": n, "aossDocs": n, "graphs": n, ...}


# --- status (polled every 1 s) ---------------------------------------------------------------------

class StageCell(RpModel):
    stage_id: StageId
    group: StageGroup
    order: int
    title: str
    state: StageState
    engine: Engine | None = None
    headline: str | None = None
    attempt: int = 0
    queued_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    last_heartbeat_at: str | None = None
    heartbeat_count: int = 0
    progress: StageProgress | None = None
    metrics: dict[str, float | int] = Field(default_factory=dict)
    skip_reason: str | None = None
    error: StageError | None = None


class LaneView(RpModel):
    state: LaneState
    current_stage: StageId | None = None
    execution_arn: str | None = None  # truncated for display by the UI
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    config_sha: str | None = None
    stages: list[StageCell]


class DocStatusView(RpModel):
    doc_id: str
    file_name: str
    size_bytes: int | None = None
    page_count: int | None = None
    doc_type: str | None = None
    upload_state: Literal["AWAITING_UPLOAD", "IN_BRONZE", "QUARANTINED"]
    bronze_key: str
    in_bronze_at: str | None = None
    lanes: dict[str, LaneView]


class PackageStatus(RpModel):
    package_id: str
    updated_at: str
    server_time: str  # lets the UI tick elapsed timers without clock skew
    lanes: dict[str, LaneRollup]
    counters: dict[str, Counters]
    objects_in_bronze: int
    ask_ready: bool  # every lane in package.pipelineVersions is ready
    docs: list[DocStatusView]


# --- documents and stage inspection -------------------------------------------------------------------

class LaneDetail(RpModel):
    lane: LaneRecord
    stages: list[StageRecord]


class DocumentDetail(RpModel):
    document: DocumentItem
    lanes: dict[str, LaneDetail]
    can_write: bool


class StageDetail(RpModel):
    """GET /documents/{docId}/lanes/{pv}/stages/{stageId}: everything about one stage run."""

    record: StageRecord
    definition: ResolvedStage
    events: list[StageEvent]  # the current run's events only (executionArn of the record)
    previous_stage: StageId | None = None
    next_stage: StageId | None = None
    history: list[StageRecord] = Field(default_factory=list)  # this stage in earlier runs, newest first


class OverlayBox(RpModel):
    """One box drawn on a page in the stage inspector. Never carries document text (Silver holds unmasked PII)."""

    kind: Literal["block", "member", "unit", "field", "held"]
    bbox: BBox
    role: str | None = None  # canonical role (blocks, members)
    label: str | None = None  # engine label ("section_header", "LAYOUT_TABLE"), unit type, or field label
    ref: str | None = None  # engine pointer ("#/texts/3", Textract block id), memberId, fieldId
    unit_id: str | None = None
    phase: str | None = None  # eu_builder phase A/B/C (members)
    alloc_score: float | None = None
    pii_flag: str | None = None


class StageOverlay(RpModel):
    """GET /documents/{docId}/lanes/{pv}/stages/{stageId}/overlay?page=: the stage's boxes on one page, computed
    server-side from the stage's Silver/Gold artifacts (whole files, never a truncated or digit-masked preview)."""

    doc_id: str
    pipeline_version: str
    stage_id: StageId
    page: int
    page_count: int
    page_width: float | None = None
    page_height: float | None = None
    frame: Literal["pdf-points-top-left"] = "pdf-points-top-left"
    page_image_url: str | None = None  # Gold page render (presigned, REGION_URL_TTL_SECONDS); None before promote
    sources: list[str] = Field(default_factory=list)  # artifact keys the boxes were read from
    boxes: list[OverlayBox] = Field(default_factory=list)
    note: str | None = None  # why there is nothing to draw (no regions in 1.0, stage not run yet, ...)


class StageEventPage(RpModel):
    items: list[StageEvent]
    next_cursor: str | None = None


class ReprocessRequest(RpModel):
    pipeline_version: str


class ReprocessResponse(RpModel):
    doc_id: str
    pipeline_version: str
    execution_arn: str


# --- units, lineage, artifacts ---------------------------------------------------------------------------

class UnitListItem(RpModel):
    unit_id: str
    label: str
    unit_type: UnitType
    pages: list[int]
    pii_flag: PiiFlag
    held: bool
    indexed: bool
    aoss_id: str | None = None
    field_count: int = 0
    chars: int = 0
    silver_key: str
    gold_key: str | None = None
    review_items: int = 0


class UnitList(RpModel):
    items: list[UnitListItem]


class UnitView(RpModel):
    """GET /units/{unitId}. For held units ``unit.text`` and member/field text are replaced by
    ``[held: PII]`` and ``held`` is true."""

    unit: Unit
    status: UnitStatusItem | None = None
    pii: PiiDecision | None = None
    held: bool = False


class StageRef(RpModel):
    stage_id: StageId
    state: StageState
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    attempt: int = 0
    execution_arn: str | None = None


LineageKind = Literal["claim", "field", "unit", "blocks", "region", "page_image", "source", "index", "graph"]


class LineageStep(RpModel):
    """One hop of the chain figure -> field -> unit -> blocks -> page region -> source object."""

    kind: LineageKind
    title: str  # "Field f-box1", "Evidence unit eu-01", "Docling blocks (3)", "Page 1 region", "Bronze object"
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)  # kind-specific detail
    bbox: BBox | None = None
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    stage: StageRef | None = None


class Lineage(RpModel):
    unit_id: str
    field_id: str | None = None
    package_id: str
    pipeline_version: str
    steps: list[LineageStep]  # ordered from the figure back to the source object
    versions: dict[str, str | None]  # pipelineVersion, configSha, ontologyVersion, engine, engineVersion


class ArtifactView(RpModel):
    key: str
    tier: str
    content_type: str
    size_bytes: int
    etag: str | None = None
    version_id: str | None = None
    last_modified: str | None = None
    preview: str | None = None  # text/JSON preview (<= 64 KB); bronze/silver previews are digit-masked
    preview_truncated: bool = False
    preview_masked: bool = False
    url: str | None = None  # presigned GET for gold/config/runs, and bronze for the owner; never silver
    expires_in: int | None = None


# --- pipelines --------------------------------------------------------------------------------------------

class PipelineVersionItem(RpModel):
    """Table rp-pipeline-versions (pk pipelineVersion). Written by scripts/seed.sh."""

    pipeline_version: str
    title: str
    mode: Literal["naive", "evidence_units"]
    config_key: str
    config_version_id: str | None = None
    config_sha: str
    block_shas: dict[str, str]
    prompt_sha: str
    model_ids: dict[str, str]  # {"answer": ..., "embed": ..., "compact": ...}
    ontology_version: str | None = None
    status: Literal["active", "retired"] = "active"
    registered_at: str


class PipelineList(RpModel):
    items: list[PipelineVersionItem]


class PipelineDetail(RpModel):
    item: PipelineVersionItem
    yaml: str
    resolved: dict[str, Any]
    stages: list[ResolvedStage]
    prompt_text: str


PipelineDiff = ConfigDiff


# --- runs and review ----------------------------------------------------------------------------------------

class RunPage(RpModel):
    items: list[RunSummary]
    next_cursor: str | None = None


RunDetail = RunRecord


class ReviewList(RpModel):
    items: list[ReviewItem]
