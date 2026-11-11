from __future__ import annotations

from pathlib import Path

from evidence_core.config import LocalConfigSource, config_slice, diff_pipelines, load_pipeline
from evidence_core.enums import StageGroup, StageId
from evidence_core.stages import REGISTRY, lane_keys, resolve_stages, stage_keys


def _load(repo_root: Path, pv: str):
    return load_pipeline(pv, LocalConfigSource(repo_root))


def test_both_pipelines_load_and_resolve_models(repo_root: Path) -> None:
    a, b = _load(repo_root, "1.0"), _load(repo_root, "2.3.1")
    assert a.config.mode == "naive" and b.config.mode == "evidence_units"
    assert a.config.generation.model_id == b.config.generation.model_id == "us.anthropic.claude-sonnet-4-6"
    assert a.config.retrieval.embed_model == "amazon.titan-embed-text-v2:0"
    assert b.config.preprocessing.classify != "none"
    assert b.config.preprocessing.classify.tie_break_model == "us.amazon.nova-2-lite-v1:0"
    assert b.config.ontology_version == "1.2.0" and a.config.ontology_version is None
    assert "qualifying income" in a.prompt_text
    assert a.prompt_sha == b.prompt_sha
    assert config_slice(b, "preprocessing.units")["tau"] == 0.40


def test_only_preprocessing_differs(repo_root: Path) -> None:
    d = diff_pipelines(_load(repo_root, "1.0"), _load(repo_root, "2.3.1"))
    assert d.changed_blocks == ["preprocessing"]
    assert d.identical_blocks == ["retrieval", "generation", "agent"]
    assert d.prompt_sha.equal and d.model_id.equal
    changed_blocks = {ln.block for ln in d.lines if ln.op != " "}
    assert changed_blocks <= {"preprocessing", None}  # None = header lines (pipelineVersion, title)
    assert d.summary.startswith("Changed: preprocessing only")


def test_registry_shape() -> None:
    orders = [s.order for s in REGISTRY]
    assert orders == sorted(orders) and len(set(orders)) == len(orders)
    assert [s.id for s in REGISTRY][0] is StageId.REGISTER and REGISTRY[-1].id is StageId.READY
    groups = {s.group for s in REGISTRY}
    assert groups == set(StageGroup)


def test_resolved_stages_per_version(repo_root: Path) -> None:
    naive = {s.id: s for s in resolve_stages(_load(repo_root, "1.0").config)}
    eu = {s.id: s for s in resolve_stages(_load(repo_root, "2.3.1").config)}
    assert {k for k, v in naive.items() if not v.enabled} == {StageId.ROUTE, StageId.CLASSIFY, StageId.GRAPH}
    assert all(v.enabled for v in eu.values())
    assert naive[StageId.GRAPH].skip_reason and "ontology" in naive[StageId.GRAPH].skip_reason
    assert naive[StageId.EXTRACT].functions == ["rp-text-layer"]
    assert "rp-docling-call" in eu[StageId.EXTRACT].functions
    assert naive[StageId.UNITS].title == "Fixed chunks" and eu[StageId.UNITS].title == "Evidence units"


def test_status_keys_sort_in_stage_order() -> None:
    keys = [stage_keys("d", "p", "2.3.1", s.id).sk for s in REGISTRY]
    assert keys == sorted(keys)
    assert lane_keys("d", "p", "1.0").sk == "LANE#1.0"
    assert stage_keys("d", "p", "1.0", StageId.PII_GATE).gsi1sk == "1.0#d#S06#pii_gate"
