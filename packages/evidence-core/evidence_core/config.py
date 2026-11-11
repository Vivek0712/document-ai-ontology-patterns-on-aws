"""Pipeline config (config/pipelines/*.yaml), models.yaml, loader, hashes and diff.

Only the ``preprocessing`` block may differ between pipeline versions.
``configSha`` = sha256 of the canonical JSON of the *resolved* config (``${KEY}``
model references replaced by model IDs). ``blockShas`` hash each top-level block the
same way, so the diff can show ``retrieval 4d1e… = 4d1e…``.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import Field

from . import s3keys
from .base import RpModel
from .hashing import sha256_json, sha256_text

BLOCKS: tuple[str, ...] = ("preprocessing", "retrieval", "generation", "agent")
_REF_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


# --- models.yaml ------------------------------------------------------------------

class ModelSpec(RpModel):
    model_id: str
    provider: Literal["anthropic", "amazon"]
    kind: Literal["text", "embedding"] = "text"
    accepts_temperature: bool = True
    dimensions: int | None = None
    normalize: bool | None = None
    notes: str | None = None


class ModelsConfig(RpModel):
    region: str = "us-east-1"
    models: dict[str, ModelSpec]

    def model_id(self, key: str) -> str:
        if key not in self.models:
            raise KeyError(f"model key {key!r} not in models.yaml")
        return self.models[key].model_id


# --- pipeline config ----------------------------------------------------------------

class RouterConfig(RpModel):
    digital: Literal["docling"] = "docling"
    scan: Literal["textract"] = "textract"
    docx: Literal["docling-native"] = "docling-native"
    scan_text_chars_max: int = 50
    textract_features: list[str] = Field(default_factory=lambda: ["LAYOUT", "TABLES", "FORMS"])


class ExtractionConfig(RpModel):
    engine: Literal["text-layer"] | None = None
    library: str | None = None
    mode: str | None = None
    layout: Literal["none", "roles"] = "none"
    router: RouterConfig | None = None


class ChunkerConfig(RpModel):
    type: Literal["fixed", "evidence_units"]
    size_tokens: int | None = None
    overlap_pct: int | None = None
    tokenizer: str | None = None


class UnitsConfig(RpModel):
    tau: float = 0.40
    phase_b: Literal["cosine"] = "cosine"
    marker_link: Literal["hard-rule", "off"] = "hard-rule"
    residual: Literal["section_text"] = "section_text"
    max_residual_tokens: int = 400
    field_bbox: bool = True
    bbox_frame: Literal["pdf-points-top-left"] = "pdf-points-top-left"
    review_min_confidence: float = 0.80


class OntologyConfig(RpModel):
    version: str
    graphs: list[str]


class EnrichmentConfig(RpModel):
    breadcrumb: bool = False
    doc_type: Literal[False, "ontology"] = False
    field_concept: Literal[False, "ontology"] = False
    period: bool = False
    bbox: bool = False


class ClassifyConfig(RpModel):
    method: Literal["skos-indicators"] = "skos-indicators"
    tie_break_model: str
    min_margin: float = 0.15


class PiiGateConfig(RpModel):
    engine: Literal["comprehend"] = "comprehend"
    action: Literal["hold"] = "hold"
    rule: str = "rp-pii-v1"
    min_score: float = 0.80
    entity_types: list[str] = Field(
        default_factory=lambda: ["SSN", "BANK_ACCOUNT_NUMBER", "BANK_ROUTING", "CREDIT_DEBIT_NUMBER"]
    )
    unmasked_min_digits: dict[str, int] = Field(
        default_factory=lambda: {
            "SSN": 9,
            "BANK_ACCOUNT_NUMBER": 8,
            "BANK_ROUTING": 9,
            "CREDIT_DEBIT_NUMBER": 12,
        }
    )
    language_code: str = "en"


class RenderConfig(RpModel):
    dpi: int = 150


class PreprocessingConfig(RpModel):
    extraction: ExtractionConfig
    chunker: ChunkerConfig
    units: UnitsConfig | Literal["none"] = "none"
    ontology: OntologyConfig | Literal["none"] = "none"
    enrichment: EnrichmentConfig
    classify: ClassifyConfig | Literal["none"] = "none"
    pii_gate: PiiGateConfig
    render: RenderConfig = Field(default_factory=RenderConfig)


class FusionConfig(RpModel):
    dense: float = 0.7
    bm25: float = 0.3
    normalization: Literal["min_max"] = "min_max"
    sub_query_k: int = 20


class ReadinessConfig(RpModel):
    max_wait_seconds: int = 90
    poll_seconds: int = 5


class RetrievalConfig(RpModel):
    embed_model: str
    embed_dimensions: int = 1024
    fusion: FusionConfig = Field(default_factory=FusionConfig)
    k: int = 5
    filters_inside_query: list[str] = Field(
        default_factory=lambda: ["allowedPrincipals", "packageId", "pipelineVersion"]
    )
    readiness: ReadinessConfig = Field(default_factory=ReadinessConfig)


class GenerationConfig(RpModel):
    model_id: str
    temperature: float | None = 0
    max_tokens: int = 700
    prompt: str = "prompts/answer_v1.txt"

    @property
    def prompt_name(self) -> str:
        return Path(self.prompt).stem


class MemoryConfig(RpModel):
    retrieval: Literal["off"] = "off"
    write: Literal["off", "post-lock", "on"] = "post-lock"


class AgentConfig(RpModel):
    runtime: str = "rp-agent"
    tools: list[str]
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    golden_set_check: bool = True
    max_tool_calls: int = 8
    citation_retry: int = 1


class PipelineConfig(RpModel):
    pipeline_version: str
    title: str
    description: str | None = None
    preprocessing: PreprocessingConfig
    retrieval: RetrievalConfig
    generation: GenerationConfig
    agent: AgentConfig

    @property
    def mode(self) -> Literal["naive", "evidence_units"]:
        """Choice state input in the ``ingest`` state machine."""
        return "naive" if self.preprocessing.chunker.type == "fixed" else "evidence_units"

    @property
    def ontology_version(self) -> str | None:
        o = self.preprocessing.ontology
        return None if o == "none" else o.version

    @property
    def graph_enabled(self) -> bool:
        return self.preprocessing.ontology != "none"

    @property
    def classify_enabled(self) -> bool:
        return self.preprocessing.classify != "none"

    @property
    def route_enabled(self) -> bool:
        return self.preprocessing.extraction.router is not None


# --- sources -------------------------------------------------------------------------

class ConfigSource(Protocol):
    def read_text(self, key: str, version_id: str | None = None) -> tuple[str, str | None]:
        """Return (text, s3VersionId or None) for an S3-style key under ``config/``.

        ``version_id`` pins an S3 object version (stages re-read the exact config
        version `register` recorded in DocCtx.configVersionId)."""
        ...


@dataclass
class LocalConfigSource:
    """Maps S3 config keys onto the repo checkout (dev, tests, local pipeline).

    config/pipelines/*, config/models.yaml -> {root}/config/...
    config/prompts/{name}.txt                -> {root}/prompts/{name}.txt
    config/ontology/{ver}/...                -> {root}/ontology/dist/{ver}/...
    """

    root: Path

    def path_for(self, key: str) -> Path:
        if key.startswith("config/prompts/"):
            return self.root / "prompts" / key.removeprefix("config/prompts/")
        if key.startswith("config/ontology/"):
            return self.root / "ontology" / "dist" / key.removeprefix("config/ontology/")
        return self.root / key

    def read_text(self, key: str, version_id: str | None = None) -> tuple[str, str | None]:
        return self.path_for(key).read_text(encoding="utf-8"), None


@dataclass
class S3ConfigSource:
    bucket: str
    client: Any = None  # boto3 S3 client; created lazily

    def read_text(self, key: str, version_id: str | None = None) -> tuple[str, str | None]:
        if self.client is None:
            import boto3

            self.client = boto3.client("s3")
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if version_id:
            kwargs["VersionId"] = version_id
        obj = self.client.get_object(**kwargs)
        return obj["Body"].read().decode("utf-8"), obj.get("VersionId")


def default_source() -> ConfigSource:
    """S3 when RP_CONFIG_SOURCE=s3 (deployed), else the repo checkout."""
    from .env import Settings

    s = Settings.from_env()
    if s.config_source == "s3":
        return S3ConfigSource(bucket=s.data_bucket)
    return LocalConfigSource(root=s.repo_root)


# --- loading ---------------------------------------------------------------------------

def parse_models(text: str) -> ModelsConfig:
    return ModelsConfig.model_validate(yaml.safe_load(text))


def resolve_refs(value: Any, lookup: Callable[[str], str]) -> Any:
    """Replace ``${KEY}`` in every string with ``lookup(KEY)``."""
    if isinstance(value, str):
        return _REF_RE.sub(lambda m: lookup(m.group(1)), value)
    if isinstance(value, list):
        return [resolve_refs(v, lookup) for v in value]
    if isinstance(value, dict):
        return {k: resolve_refs(v, lookup) for k, v in value.items()}
    return value


@dataclass
class LoadedPipeline:
    config: PipelineConfig
    raw_text: str
    resolved: dict[str, Any]
    config_sha: str
    block_shas: dict[str, str]
    version_id: str | None
    prompt_text: str
    prompt_sha: str
    models: ModelsConfig
    source_key: str
    model_refs: dict[str, str] = field(default_factory=dict)  # ${KEY} -> modelId used

    @property
    def pipeline_version(self) -> str:
        return self.config.pipeline_version


def load_pipeline(
    pipeline_version: str, source: ConfigSource | None = None, *, version_id: str | None = None
) -> LoadedPipeline:
    """Load and resolve a pipeline config. Pass ``version_id`` to pin the S3 object version."""
    src = source or default_source()
    models_text, _ = src.read_text(s3keys.models_config_key())
    models = parse_models(models_text)
    key = s3keys.pipeline_config_key(pipeline_version)
    raw_text, version_id = src.read_text(key, version_id)
    raw = yaml.safe_load(raw_text)
    used: dict[str, str] = {}

    def lookup(ref: str) -> str:
        used[ref] = models.model_id(ref)
        return used[ref]

    resolved = resolve_refs(raw, lookup)
    cfg = PipelineConfig.model_validate(resolved)
    if cfg.pipeline_version != pipeline_version:
        raise ValueError(f"{key} declares pipelineVersion {cfg.pipeline_version!r}")
    prompt_text, _ = src.read_text(s3keys.prompt_key(cfg.generation.prompt_name))
    return LoadedPipeline(
        config=cfg,
        raw_text=raw_text,
        resolved=resolved,
        config_sha=sha256_json(resolved),
        block_shas={b: sha256_json(resolved.get(b)) for b in BLOCKS},
        version_id=version_id,
        prompt_text=prompt_text,
        prompt_sha=sha256_text(prompt_text),
        models=models,
        source_key=key,
        model_refs=used,
    )


def config_slice(loaded: LoadedPipeline, path: str) -> Any:
    """Dotted path into the resolved config, e.g. ``preprocessing.units``."""
    node: Any = loaded.resolved
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


# --- diff ----------------------------------------------------------------------------------

class DiffLine(RpModel):
    op: Literal[" ", "-", "+"]
    a_no: int | None = None
    b_no: int | None = None
    text: str
    block: str | None = None


class BlockCompare(RpModel):
    a: str
    b: str
    equal: bool


class ConfigDiff(RpModel):
    a: str
    b: str
    a_config_sha: str
    b_config_sha: str
    changed_blocks: list[str]
    identical_blocks: list[str]
    blocks: dict[str, BlockCompare]
    prompt_sha: BlockCompare
    model_id: BlockCompare
    lines: list[DiffLine]
    summary: str


def _block_of_lines(text: str) -> list[str | None]:
    current: str | None = None
    out: list[str | None] = []
    for line in text.splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            key = line.split(":", 1)[0].strip()
            current = key if key in BLOCKS else None
        out.append(current)
    return out


def diff_pipelines(a: LoadedPipeline, b: LoadedPipeline) -> ConfigDiff:
    blocks = {
        name: BlockCompare(
            a=a.block_shas[name], b=b.block_shas[name], equal=a.block_shas[name] == b.block_shas[name]
        )
        for name in BLOCKS
    }
    changed = [n for n, c in blocks.items() if not c.equal]
    identical = [n for n, c in blocks.items() if c.equal]
    a_lines, b_lines = a.raw_text.splitlines(), b.raw_text.splitlines()
    a_blocks, b_blocks = _block_of_lines(a.raw_text), _block_of_lines(b.raw_text)
    lines: list[DiffLine] = []
    sm = difflib.SequenceMatcher(a=a_lines, b=b_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                lines.append(
                    DiffLine(op=" ", a_no=i1 + k + 1, b_no=j1 + k + 1, text=a_lines[i1 + k], block=a_blocks[i1 + k])
                )
            continue
        for i in range(i1, i2):
            lines.append(DiffLine(op="-", a_no=i + 1, text=a_lines[i], block=a_blocks[i]))
        for j in range(j1, j2):
            lines.append(DiffLine(op="+", b_no=j + 1, text=b_lines[j], block=b_blocks[j]))
    others = ", ".join(identical) if identical else "none"
    summary = (
        f"Changed: {' and '.join(changed) if changed else 'nothing'}"
        f"{' only' if len(changed) == 1 else ''} · {others} identical"
    )
    return ConfigDiff(
        a=a.pipeline_version,
        b=b.pipeline_version,
        a_config_sha=a.config_sha,
        b_config_sha=b.config_sha,
        changed_blocks=changed,
        identical_blocks=identical,
        blocks=blocks,
        prompt_sha=BlockCompare(a=a.prompt_sha, b=b.prompt_sha, equal=a.prompt_sha == b.prompt_sha),
        model_id=BlockCompare(
            a=a.config.generation.model_id,
            b=b.config.generation.model_id,
            equal=a.config.generation.model_id == b.config.generation.model_id,
        ),
        lines=lines,
        summary=summary,
    )
