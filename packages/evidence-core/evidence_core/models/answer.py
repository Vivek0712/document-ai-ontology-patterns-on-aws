"""Ask path contracts: request, streamed events, answer/citation payload, run record.

Wire format of POST /ask: ``text/event-stream``; each event is ``data: <json>\\n\\n`` with one
of the AskEvent models below (discriminator ``type``). When API Gateway buffers the
response, the same SSE text arrives at once; the UI parser handles both.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from ..base import RpModel
from ..bbox import BBox
from .tools import RetrievedUnit

# Generation profile of one ask: swaps the answer model for that run only; the lane config and its
# configSha are unchanged. "frontier" = ${MODEL_FRONTIER} (SC-10, a bigger model), "compact" = ${MODEL_COMPACT}
# (SC-13, the compact tier). Recorded in the RunRecord; every comparison shows it in the config-diff strip.
GenerationProfile = Literal["default", "frontier", "compact"]
GENERATION_PROFILE_MODEL_KEYS: dict[str, str] = {"frontier": "MODEL_FRONTIER", "compact": "MODEL_COMPACT"}


class AskRequest(RpModel):
    question: str = Field(min_length=1, max_length=2000)
    package_id: str
    pipeline_version: str
    session_id: str | None = None  # >= 33 chars when given (AgentCore runtime session id)
    tools_disabled: list[str] = Field(default_factory=list)  # gate control: ["graph_hop"] (graph-off run)
    generation_profile: GenerationProfile = "default"


class Claim(RpModel):
    """One cited figure. The model supplies text/value + {unitId, fieldId}; the
    citation validator attaches docId/page/bbox from tool results, never from model text."""

    text: str  # the figure as written in the answer: "98,400.00"
    value: str | None = None  # normalised decimal "98400.00"
    unit_id: str
    field_id: str | None = None
    doc_id: str | None = None
    file_name: str | None = None
    label: str | None = None  # chip label: "W-2 · Maya Fictus · Box 1 · p.1" or "chunk c-03"
    page: int | None = None
    bbox: BBox | None = None  # field bbox (else unit bbox); None -> not resolvable
    unit_bbox: BBox | None = None
    resolvable: bool
    chunk_label: str | None = None  # v1.0 chips: "chunk c-03"
    reason: str | None = None  # why not resolvable: "chunk has no page region", "not retrieved in session"


class AnswerPayload(RpModel):
    answer: str
    total: str | None = None  # display string "169,650.00"
    currency: str | None = None
    arithmetic: str | None = None  # "98,400.00 + 71,250.00 = 169,650.00"
    claims: list[Claim] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    citation_retries: int = 0
    uncited_numbers: list[str] = Field(default_factory=list)  # numbers left uncited after the retry


class Check(RpModel):
    """Golden-set check (agent code). Emitted only when the question matches a golden qid."""

    qid: str
    expected: str
    actual: str | None = None
    match: bool


class RunFooter(RpModel):
    run_id: str
    package_id: str
    pipeline_version: str
    config_sha: str
    block_shas: dict[str, str]
    prompt_sha: str
    model_id: str
    corpus_hash: str
    memory_records_injected: int = 0
    latency_ms: int
    usage: dict[str, int] = Field(default_factory=dict)  # inputTokens, outputTokens, totalTokens
    started_at: str
    runtime_session_id: str | None = None


# --- streamed events -------------------------------------------------------------------------

class RunStartEvent(RpModel):
    type: Literal["run_start"] = "run_start"
    run_id: str
    package_id: str
    pipeline_version: str
    question: str
    model_id: str
    config_sha: str
    prompt_sha: str
    tools: list[str]
    t: float = 0.0


class ToolStartEvent(RpModel):
    type: Literal["tool_start"] = "tool_start"
    call_id: str
    tool: str  # bare tool name (gateway prefix stripped)
    input: dict[str, Any]  # model-visible args only
    t: float  # seconds since run_start


class ToolEndEvent(RpModel):
    type: Literal["tool_end"] = "tool_end"
    call_id: str
    tool: str
    ms: int
    status: Literal["ok", "error", "denied"]
    summary: str  # "5 units", "2 rows · total 169,650.00", "p.1 Box 1"
    units: list[str] = Field(default_factory=list)  # search_units: unitIds in rank order
    scores: list[dict[str, Any]] = Field(default_factory=list)  # [{unitId, denseNorm, bm25Norm, fused}]
    retrieved: list[RetrievedUnit] = Field(default_factory=list)  # search_units full rows (retrieved panel)
    rows: list[dict[str, Any]] = Field(default_factory=list)  # graph_hop rows
    total: str | None = None
    template: str | None = None
    error: str | None = None
    t: float


class TextEvent(RpModel):
    type: Literal["text"] = "text"
    delta: str


class AnswerEvent(AnswerPayload):
    type: Literal["answer"] = "answer"


class CheckEvent(Check):
    type: Literal["check"] = "check"


class RunEvent(RunFooter):
    type: Literal["run"] = "run"
    question: str | None = None  # echoed so a replayed stream is self-contained


class ErrorEvent(RpModel):
    type: Literal["error"] = "error"
    code: str  # "forbidden", "not_ready", "agent_error", "timeout"
    message: str
    run_id: str | None = None


AskEvent = Annotated[
    RunStartEvent | ToolStartEvent | ToolEndEvent | TextEvent | AnswerEvent | CheckEvent | RunEvent | ErrorEvent,
    Field(discriminator="type"),
]


def sse(event: RpModel) -> str:
    """Serialise one event as an SSE frame."""
    return f"data: {event.dump_json()}\n\n"


# --- run record ----------------------------------------------------------------------------------

class ToolCallRecord(RpModel):
    call_id: str
    tool: str
    input: dict[str, Any]  # model args
    injected: dict[str, Any]  # interceptor-injected values as seen by the tool (sub truncated)
    started_at: str
    ms: int
    status: Literal["ok", "error", "denied"]
    output: dict[str, Any]  # full tool result JSON (S3 copy); DynamoDB keeps a summary only
    error: str | None = None


class RunRecord(RpModel):
    """Full record: S3 runs/{runId}.json. DynamoDB rp-runs holds RunSummary + s3Key."""

    run_id: str
    created_at: str
    package_id: str
    pipeline_version: str
    question: str
    caller_sub: str
    groups: list[str]
    model_id: str
    prompt_sha: str
    config_sha: str
    block_shas: dict[str, str]
    corpus_hash: str
    memory_records_injected: int = 0
    tools_enabled: list[str]
    tool_calls: list[ToolCallRecord]
    retrieved: list[RetrievedUnit]  # union of search_units results, rank order of first appearance
    retrieved_text: list[str]  # for the gate's mechanism check
    answer: AnswerPayload | None = None
    check: Check | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    latency_ms: int
    status: Literal["ok", "error"]
    error: str | None = None
    runtime_session_id: str | None = None
    s3_key: str
    generation_profile: GenerationProfile = "default"  # "default" on every record logged before the field existed
    model_key: str | None = None  # models.yaml key of model_id ("MODEL_ANSWER", "MODEL_FRONTIER", ...)
    # sha256 of the tool specs sent to Converse (names, descriptions, JSON schemas); None on records logged before
    # it was recorded (2026-11-11), which a comparison then reports as unverified, never as identical
    tool_spec_sha: str | None = None


class RunSummary(RpModel):
    """Item in rp-runs (pk runId, GSI byPackage: packageId + createdAt)."""

    run_id: str
    created_at: str
    package_id: str
    pipeline_version: str
    question: str
    caller_sub: str
    model_id: str
    prompt_sha: str
    config_sha: str
    corpus_hash: str
    total: str | None = None
    check_match: bool | None = None
    qid: str | None = None
    claims: int = 0
    resolvable_claims: int = 0
    tool_sequence: list[str] = Field(default_factory=list)
    latency_ms: int
    status: Literal["ok", "error"]
    memory_records_injected: int = 0
    s3_key: str
    generation_profile: GenerationProfile = "default"
