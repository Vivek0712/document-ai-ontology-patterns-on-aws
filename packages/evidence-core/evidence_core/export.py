"""Write machine-readable contracts to contracts/generated/ (run: make contracts).

Generated files (do not edit by hand; tests fail when they drift):
  stages.json        resolved stage registry per pipeline version (UI, API /pipelines)
  mcp_tools.json     Gateway tool specs (scripts/deploy_agentcore.sh)
  aoss_mapping.json  index body for units-v1-0 and units-v2-3-1 (embed_index creates indexes)
  tables.json        DynamoDB key schema (infra/ mirrors it)
  coa_control.json   config/coa.yaml resolved (+ the COA price block, admin group, rp-coa-env table): infra/ and the
                     rp-coa-lifecycle build bundle read it
  json_schema/*.json JSON Schema of key models (cross-checks for api.ts)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from . import PIPELINE_VERSIONS
from .config import LocalConfigSource, load_pipeline
from .ddb import TABLE_SCHEMA
from .ids import index_name
from .models import answer, api, coa_env, stage_outputs, status, tools, units
from .retrieval import index_mapping
from .stages import resolve_stages

SCHEMA_MODELS = {
    "Unit": units.Unit,
    "StageRecord": status.StageRecord,
    "StageResult": status.StageResult,
    "StageEnvelope": status.StageEnvelope,
    "DocCtx": status.DocCtx,
    "IngestTrigger": status.IngestTrigger,
    "PackageStatus": api.PackageStatus,
    "Lineage": api.Lineage,
    "RunRecord": answer.RunRecord,
    "RegionResult": tools.RegionResult,
    "SearchUnitsResult": tools.SearchUnitsResult,
    "GraphHopResult": tools.GraphHopResult,
    "VocabSnapshot": units.VocabSnapshot,
    "DoclingMessage": stage_outputs.DoclingMessage,
    "TextractPageInput": stage_outputs.TextractPageInput,
    "RegisterResult": stage_outputs.RegisterResult,
    "ExtractPart": status.ExtractPart,
    "CoaEnvironmentView": coa_env.CoaEnvironmentView,
}


def coa_control(repo_root: Path) -> dict[str, Any]:
    cfg = coa_env.parse_config((repo_root / "config" / "coa.yaml").read_text(encoding="utf-8"),
                               (repo_root / "config" / "prices.yaml").read_text(encoding="utf-8"))
    out = cfg.dump()
    out["adminGroup"] = coa_env.ADMIN_GROUP
    out["function"] = coa_env.COA_CONTROL_FUNCTION
    out["table"] = coa_env.COA_ENV_TABLE
    out["tableEnv"] = coa_env.COA_ENV_TABLE_ENV
    return out


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=False, ensure_ascii=False) + "\n", encoding="utf-8")


def build(repo_root: Path) -> dict[str, Any]:
    src = LocalConfigSource(root=repo_root)
    stages_out = {}
    for pv in PIPELINE_VERSIONS:
        lp = load_pipeline(pv, src)
        stages_out[pv] = {
            "pipelineVersion": pv,
            "mode": lp.config.mode,
            "configSha": lp.config_sha,
            "stages": [s.dump() for s in resolve_stages(lp.config)],
        }
    files: dict[str, Any] = {
        "stages.json": stages_out,
        "mcp_tools.json": {"version": 1, "tools": tools.tool_specs(), "injectedFields": list(tools.INJECTED_FIELDS)},
        "aoss_mapping.json": {"indexes": [index_name(pv) for pv in PIPELINE_VERSIONS], "body": index_mapping()},
        "tables.json": TABLE_SCHEMA,
        "coa_control.json": coa_control(repo_root),
    }
    for name, model in SCHEMA_MODELS.items():
        files[f"json_schema/{name}.json"] = model.model_json_schema(by_alias=True)
    return files


def main(argv: list[str]) -> int:
    repo_root = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parents[3]
    out = repo_root / "contracts" / "generated"
    for rel, value in build(repo_root).items():
        _dump(out / rel, value)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
