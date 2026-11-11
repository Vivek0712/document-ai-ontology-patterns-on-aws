"""Stage registry: the ordered stages per pipeline mode that the UI renders and every
stage writer records against. Exported to contracts/generated/stages.json.

Mode ``naive`` (pipeline 1.0) and ``evidence_units`` (2.3.1) share stage ids so the board
columns line up; a stage without a variant for a mode (or disabled by config) is written
as SKIPPED by `register`, with the reason, so the inspector shows why it did not run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .base import RpModel
from .config import PipelineConfig
from .enums import ArtifactKind as AK
from .enums import Engine, StageGroup, StageId

Mode = Literal["naive", "evidence_units"]


class StageVariant(RpModel):
    title: str
    engines: list[Engine]
    services: list[str]  # full AWS product names (lower-thirds / inspector)
    functions: list[str]  # Lambda function names (rp-<name>); empty for pure SFN integrations
    sfn_states: list[str]  # state names in rp-ingest that belong to this stage
    produces: list[AK]
    consumes: list[AK]
    config_paths: list[str]
    description: str


class StageDef(RpModel):
    id: StageId
    order: int
    group: StageGroup
    variants: dict[str, StageVariant]  # keyed by Mode
    skip_reason_when_absent: str


class ResolvedStage(RpModel):
    """A stage as it applies to one pipeline version (what the UI renders)."""

    id: StageId
    order: int
    group: StageGroup
    title: str
    enabled: bool
    skip_reason: str | None = None
    engines: list[Engine]
    services: list[str]
    functions: list[str]
    sfn_states: list[str]
    produces: list[AK]
    consumes: list[AK]
    config_paths: list[str]
    description: str


_S3 = "Amazon S3"
_DDB = "Amazon DynamoDB"
_LAMBDA = "AWS Lambda"
_SFN = "AWS Step Functions"

REGISTRY: tuple[StageDef, ...] = (
    StageDef(
        id=StageId.REGISTER,
        order=1,
        group=StageGroup.UPLOAD,
        skip_reason_when_absent="",
        variants={
            m: StageVariant(
                title="Register",
                engines=[Engine.LAMBDA],
                services=["Amazon EventBridge", _SFN, _LAMBDA, _S3, _DDB],
                functions=["rp-register"],
                sfn_states=["Register", "ModeChoice"],
                produces=[AK.MANIFEST],
                consumes=[AK.SOURCE_PDF],
                config_paths=["pipelineVersion"],
                description="Validate the Bronze object, hash it, count pages, load the pipeline config, "
                "write DocAcl and PENDING/SKIPPED records for every stage.",
            )
            for m in ("naive", "evidence_units")
        },
    ),
    StageDef(
        id=StageId.ROUTE,
        order=2,
        group=StageGroup.EXTRACT,
        skip_reason_when_absent="preprocessing.extraction has no router: text-layer extraction for every page",
        variants={
            "evidence_units": StageVariant(
                title="Route pages",
                engines=[Engine.PYPDF],
                services=[_LAMBDA],
                functions=["rp-route"],
                sfn_states=["Route"],
                produces=[AK.ROUTE],
                consumes=[AK.SOURCE_PDF],
                config_paths=["preprocessing.extraction.router"],
                description="Per page: text-layer chars > scanTextCharsMax -> digital (Docling), else scan (Textract).",
            )
        },
    ),
    StageDef(
        id=StageId.EXTRACT,
        order=3,
        group=StageGroup.EXTRACT,
        skip_reason_when_absent="",
        variants={
            "naive": StageVariant(
                title="Extract text layer",
                engines=[Engine.TEXT_LAYER],
                services=[_LAMBDA],
                functions=["rp-text-layer"],
                sfn_states=["TextLayer"],
                produces=[AK.TEXT_LAYER],
                consumes=[AK.SOURCE_PDF],
                config_paths=["preprocessing.extraction"],
                description="pypdf extract_text() in default (stream-order) mode; scanned pages yield no text.",
            ),
            "evidence_units": StageVariant(
                title="Extract layout",
                engines=[Engine.DOCLING, Engine.TEXTRACT],
                services=["Amazon SQS", "AWS Fargate", "Amazon Textract", _LAMBDA],
                functions=["rp-stage-status", "rp-docling-call", "rp-textract-page"],
                sfn_states=[
                    "ExtractOpen", "Extract", "DoclingExtract", "TextractPages", "TextractPage", "ExtractClose"
                ],
                produces=[AK.DOCLING_JSON, AK.TEXTRACT_JSON],
                consumes=[AK.SOURCE_PDF, AK.ROUTE],
                config_paths=["preprocessing.extraction"],
                description="Digital pages: SQS -> docling-serve on Fargate (task token). "
                "Scanned pages: Map -> Textract AnalyzeDocument (LAYOUT, TABLES, FORMS).",
            ),
        },
    ),
    StageDef(
        id=StageId.CLASSIFY,
        order=4,
        group=StageGroup.UNITS,
        skip_reason_when_absent="preprocessing.classify: none (enrichment.docType false)",
        variants={
            "evidence_units": StageVariant(
                title="Classify",
                engines=[Engine.SKOS, Engine.NOVA],
                services=[_LAMBDA, "Amazon Bedrock"],
                functions=["rp-classify"],
                sfn_states=["Classify"],
                produces=[AK.CLASSIFICATION],
                consumes=[AK.DOCLING_JSON, AK.TEXTRACT_JSON],
                config_paths=["preprocessing.classify", "preprocessing.ontology"],
                description="DocumentType from SKOS indicator phrases in the vocab snapshot; "
                "Amazon Nova 2 Lite breaks ties when the margin is below minMargin.",
            )
        },
    ),
    StageDef(
        id=StageId.UNITS,
        order=5,
        group=StageGroup.UNITS,
        skip_reason_when_absent="",
        variants={
            "naive": StageVariant(
                title="Fixed chunks",
                engines=[Engine.FIXED_CHUNKER],
                services=[_LAMBDA],
                functions=["rp-naive-chunk"],
                sfn_states=["NaiveChunk"],
                produces=[AK.UNITS_JSONL, AK.BUILD_REPORT],
                consumes=[AK.TEXT_LAYER],
                config_paths=["preprocessing.chunker", "preprocessing.units", "preprocessing.enrichment"],
                description="Fixed-size chunks (sizeTokens, overlapPct); no bbox, no breadcrumb, no ontology.",
            ),
            "evidence_units": StageVariant(
                title="Evidence units",
                engines=[Engine.EU_BUILDER],
                services=[_LAMBDA, _S3, _DDB],
                functions=["rp-eu-builder"],
                sfn_states=["BuildUnits"],
                produces=[AK.UNITS_JSONL, AK.BUILD_REPORT],
                consumes=[AK.DOCLING_JSON, AK.TEXTRACT_JSON, AK.CLASSIFICATION],
                config_paths=["preprocessing.units", "preprocessing.enrichment", "preprocessing.ontology"],
                description="Canonical bboxes (PDF points, top-left origin), roles via vocab snapshot, Phase A seeds, "
                "Phase B cosine tau, Phase C residual; fields with page + bbox; low-confidence fields to ReviewQueue.",
            ),
        },
    ),
    StageDef(
        id=StageId.PII_GATE,
        order=6,
        group=StageGroup.GATE,
        skip_reason_when_absent="",
        variants={
            m: StageVariant(
                title="PII gate",
                engines=[Engine.COMPREHEND],
                services=["Amazon Comprehend", _LAMBDA, _DDB],
                functions=["rp-pii-gate"],
                sfn_states=["PiiGate"],
                produces=[AK.PII_DECISIONS],
                consumes=[AK.UNITS_JSONL],
                config_paths=["preprocessing.piiGate"],
                description="DetectPiiEntities per unit; hold when an unmasked SSN/account/routing/card span "
                "scores >= minScore (rule rp-pii-v1). Held units stay in Silver.",
            )
            for m in ("naive", "evidence_units")
        },
    ),
    StageDef(
        id=StageId.PROMOTE,
        order=7,
        group=StageGroup.GATE,
        skip_reason_when_absent="",
        variants={
            m: StageVariant(
                title="Promote to Gold",
                engines=[Engine.PDFIUM],
                services=[_LAMBDA, _S3],
                functions=["rp-promote"],
                sfn_states=["Promote"],
                produces=[AK.GOLD_UNITS_JSONL, AK.GOLD_UNIT, AK.PAGE_IMAGE],
                consumes=[AK.UNITS_JSONL, AK.PII_DECISIONS, AK.SOURCE_PDF],
                config_paths=["preprocessing.render"],
                description="Write passed units to Gold; render page PNGs with held bboxes filled black.",
            )
            for m in ("naive", "evidence_units")
        },
    ),
    StageDef(
        id=StageId.INDEX,
        order=8,
        group=StageGroup.INDEX,
        skip_reason_when_absent="",
        variants={
            m: StageVariant(
                title="Embed and index",
                engines=[Engine.TITAN_AOSS],
                services=["Amazon Titan Text Embeddings V2", "Amazon OpenSearch Serverless", _LAMBDA],
                functions=["rp-embed-index"],
                sfn_states=["IndexAndGraph", "EmbedIndex"],
                produces=[AK.INDEX_RECEIPT],
                consumes=[AK.GOLD_UNITS_JSONL],
                config_paths=["retrieval.embedModel", "retrieval.embedDimensions"],
                description="Embed breadcrumb + text with Titan V2 (1024-d, normalized); bulk index with "
                "auto ids into units-v<pv>; ids stored in UnitStatus.",
            )
            for m in ("naive", "evidence_units")
        },
    ),
    StageDef(
        id=StageId.GRAPH,
        order=9,
        group=StageGroup.INDEX,
        skip_reason_when_absent="preprocessing.ontology: none (this version binds no ontology)",
        variants={
            "evidence_units": StageVariant(
                title="Graph write",
                engines=[Engine.NEPTUNE],
                services=["Amazon Neptune", _LAMBDA],
                functions=["rp-graph-write"],
                sfn_states=["GraphChoice", "GraphWrite"],
                produces=[AK.ABOX_NQUADS, AK.GRAPH_RECEIPT],
                consumes=[AK.GOLD_UNITS_JSONL],
                config_paths=["preprocessing.ontology"],
                description="Typed facts (fin:), units and page regions with PROV-O lineage into the "
                "package's named graph via SPARQL UPDATE (SigV4).",
            )
        },
    ),
    StageDef(
        id=StageId.READY,
        order=10,
        group=StageGroup.INDEX,
        skip_reason_when_absent="",
        variants={
            m: StageVariant(
                title="Lane ready",
                engines=[Engine.LAMBDA],
                services=["Amazon OpenSearch Serverless", _DDB],
                functions=["rp-mark-ready"],
                sfn_states=["MarkReady", "Done"],
                produces=[AK.READY_RECEIPT],
                consumes=[AK.INDEX_RECEIPT, AK.GRAPH_RECEIPT],
                config_paths=["retrieval.readiness"],
                description="Wait until the index count for this doc and lane equals the unit count "
                "(cap maxWaitSeconds), then mark the lane SUCCEEDED.",
            )
            for m in ("naive", "evidence_units")
        },
    ),
)

STAGE_IDS: tuple[StageId, ...] = tuple(s.id for s in REGISTRY)
_BY_ID: dict[StageId, StageDef] = {s.id: s for s in REGISTRY}


def stage_def(stage_id: StageId | str) -> StageDef:
    return _BY_ID[StageId(stage_id)]


def _config_enables(stage_id: StageId, cfg: PipelineConfig) -> tuple[bool, str | None]:
    if stage_id is StageId.ROUTE and not cfg.route_enabled:
        return False, stage_def(stage_id).skip_reason_when_absent
    if stage_id is StageId.CLASSIFY and not cfg.classify_enabled:
        return False, stage_def(stage_id).skip_reason_when_absent
    if stage_id is StageId.GRAPH and not cfg.graph_enabled:
        return False, stage_def(stage_id).skip_reason_when_absent
    return True, None


def resolve_stages(cfg: PipelineConfig) -> list[ResolvedStage]:
    """Ordered stages for one pipeline version, including SKIPPED ones (enabled=False)."""
    out: list[ResolvedStage] = []
    for sd in REGISTRY:
        variant = sd.variants.get(cfg.mode)
        enabled, reason = _config_enables(sd.id, cfg)
        if variant is None:
            enabled, reason = False, sd.skip_reason_when_absent
            # show the other mode's variant metadata so the UI can still name the stage
            variant = next(iter(sd.variants.values()))
        out.append(
            ResolvedStage(
                id=sd.id,
                order=sd.order,
                group=sd.group,
                title=variant.title,
                enabled=enabled,
                skip_reason=None if enabled else reason,
                engines=variant.engines,
                services=variant.services,
                functions=variant.functions,
                sfn_states=variant.sfn_states,
                produces=variant.produces,
                consumes=variant.consumes,
                config_paths=variant.config_paths,
                description=variant.description,
            )
        )
    return out


@dataclass(frozen=True)
class StageKeys:
    pk: str
    sk: str
    gsi1pk: str
    gsi1sk: str


def stage_keys(doc_id: str, package_id: str, pv: str, stage_id: StageId | str) -> StageKeys:
    sd = stage_def(stage_id)
    return StageKeys(
        pk=doc_id,
        sk=f"STAGE#{pv}#{sd.order:02d}#{sd.id.value}",
        gsi1pk=package_id,
        gsi1sk=f"{pv}#{doc_id}#S{sd.order:02d}#{sd.id.value}",
    )


def lane_keys(doc_id: str, package_id: str, pv: str) -> StageKeys:
    return StageKeys(pk=doc_id, sk=f"LANE#{pv}", gsi1pk=package_id, gsi1sk=f"{pv}#{doc_id}#LANE")


def history_run_key(started_at: str | None, execution_arn: str) -> str:
    """Sort key part for one archived execution: lane start time, then the execution name."""
    return f"{started_at or '-'}#{execution_arn.rsplit(':', 1)[-1]}"


def history_sk(pv: str, run_key: str, suffix: str) -> str:
    """``HIST#{pv}#{runKey}#LANE`` or ``HIST#{pv}#{runKey}#S{order}#{stageId}`` (archived records)."""
    return f"HIST#{pv}#{run_key}#{suffix}"


def event_sk(pv: str, at: str, stage_id: StageId | str, event: str) -> str:
    return f"EVENT#{pv}#{at}#{StageId(stage_id).value}#{event}"
