"""GET /pipelines, /pipelines/{pv}, /pipelines/diff?a=&b= (signed-in users)."""

from __future__ import annotations

from botocore.exceptions import ClientError
from evidence_core.config import LoadedPipeline, diff_pipelines
from evidence_core.ddb import from_ddb
from evidence_core.ids import PIPELINE_VERSION_RE
from evidence_core.models.api import PipelineDetail, PipelineList, PipelineVersionItem
from evidence_core.models.status import iso
from evidence_core.stages import resolve_stages

from .. import store
from ..deps import Deps, get_deps
from ..http import Reply, Request, bad_request, not_found
from . import PIPELINE, PIPELINE_DIFF, PIPELINES, router
from .packages import KNOWN_PIPELINE_VERSIONS


def _check_pv(pv: str | None, name: str = "pv") -> str:
    if not pv or not PIPELINE_VERSION_RE.match(pv):
        raise bad_request(f"{name} must be a pipeline version like 2.3.1")
    return pv


def _load(deps: Deps, pv: str) -> LoadedPipeline:
    try:
        return deps.pipeline(pv)
    except (FileNotFoundError, KeyError) as exc:
        raise not_found(f"Pipeline {pv} not found.") from exc
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            raise not_found(f"Pipeline {pv} not found.") from exc
        raise


def item_from_config(lp: LoadedPipeline) -> PipelineVersionItem:
    """What seed.sh registers, derived from the config (used before seeding and as a fallback)."""
    models = {"answer": lp.config.generation.model_id, "embed": lp.config.retrieval.embed_model}
    cls = lp.config.preprocessing.classify
    if cls != "none":
        models["compact"] = cls.tie_break_model
    return PipelineVersionItem(
        pipeline_version=lp.pipeline_version, title=lp.config.title, mode=lp.config.mode, config_key=lp.source_key,
        config_version_id=lp.version_id, config_sha=lp.config_sha, block_shas=lp.block_shas,
        prompt_sha=lp.prompt_sha, model_ids=models, ontology_version=lp.config.ontology_version,
        registered_at=iso(),
    )


def registered(deps: Deps, pv: str) -> PipelineVersionItem | None:
    item = deps.table("pipeline_versions").get_item(Key={"pipelineVersion": pv}).get("Item")
    return PipelineVersionItem.load(from_ddb(item)) if item else None


@router.route("GET", PIPELINES)
def list_pipelines(req: Request) -> Reply:
    deps = get_deps()
    items = [PipelineVersionItem.load(r) for r in store.scan_all(deps.table("pipeline_versions"))]
    if not items:
        items = [item_from_config(_load(deps, pv)) for pv in KNOWN_PIPELINE_VERSIONS]
    items.sort(key=lambda i: tuple(int(p) for p in i.pipeline_version.split(".")))
    return Reply(PipelineList(items=items))


@router.route("GET", PIPELINE)
def get_pipeline(req: Request) -> Reply:
    deps = get_deps()
    pv = _check_pv(req.param("pv"))
    lp = _load(deps, pv)
    item = registered(deps, pv) or item_from_config(lp)
    return Reply(PipelineDetail(item=item, yaml=lp.raw_text, resolved=lp.resolved, stages=resolve_stages(lp.config),
                                prompt_text=lp.prompt_text))


@router.route("GET", PIPELINE_DIFF)
def get_diff(req: Request) -> Reply:
    deps = get_deps()
    a = _check_pv(req.query.get("a", "1.0"), "a")
    b = _check_pv(req.query.get("b", "2.3.1"), "b")
    return Reply(diff_pipelines(_load(deps, a), _load(deps, b)))
