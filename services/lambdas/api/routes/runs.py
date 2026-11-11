"""GET /runs?packageId=&limit=&cursor=, GET /runs/{runId}, GET /review?packageId="""

from __future__ import annotations

from typing import Any

from boto3.dynamodb.conditions import Key
from evidence_core.ddb import from_ddb
from evidence_core.ids import RUN_ID_RE
from evidence_core.models.answer import RunRecord, RunSummary
from evidence_core.models.api import ReviewList, RunPage
from evidence_core.models.units import ReviewItem
from evidence_core.s3keys import run_key, textract_page_key

from .. import store
from ..acl import can_read_doc, require_package
from ..auth import Caller
from ..deps import Deps, get_deps
from ..http import Reply, Request, bad_request, not_found
from . import REVIEW, RUN, RUNS, router


def _package_param(req: Request) -> str:
    pid = req.query.get("packageId")
    if not pid:
        raise bad_request("packageId is required")
    return pid


@router.route("GET", RUNS)
def list_runs(req: Request) -> Reply:
    deps = get_deps()
    pkg = require_package(deps, req.caller, _package_param(req))
    kwargs: dict[str, Any] = {
        "IndexName": "byPackage",
        "KeyConditionExpression": Key("packageId").eq(pkg.package_id),
        "ScanIndexForward": False,
        "Limit": req.int_query("limit", 50, hi=200),
    }
    cursor = store.decode_cursor(req.query.get("cursor"))
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    page = deps.table("runs").query(**kwargs)
    items = [RunSummary.load(from_ddb(i)) for i in page.get("Items", [])]
    return Reply(RunPage(items=items, next_cursor=store.encode_cursor(page.get("LastEvaluatedKey"))))


def load_run(deps: Deps, caller: Caller, run_id: str) -> RunRecord:
    """The full run record, after the package ACL of the run's package (shared by the v2 inspection routes)."""
    if not RUN_ID_RE.match(run_id):
        raise bad_request(f"invalid runId {run_id!r}")
    row = deps.table("runs").get_item(Key={"runId": run_id}).get("Item")
    if not row:
        raise not_found(f"Run {run_id} not found.")
    summary = RunSummary.load(from_ddb(row))
    require_package(deps, caller, summary.package_id)
    data = store.read_json(deps, summary.s3_key or run_key(run_id))
    if data is None:
        raise not_found(f"Run record {run_id} is not in S3 yet.")
    return RunRecord.load(data)


@router.route("GET", RUN)
def get_run(req: Request) -> Reply:
    return Reply(load_run(get_deps(), req.caller, req.param("runId")))


@router.route("GET", REVIEW)
def list_review(req: Request) -> Reply:
    """The package's review items whose document the caller may read, each with its extract engine's model version
    (read from the page's extract artifact) so a stand-in's placeholder confidence is never shown as measured."""
    deps = get_deps()
    pkg = require_package(deps, req.caller, _package_param(req))
    rows = store.query_all(deps.table("review_queue"), KeyConditionExpression=Key("packageId").eq(pkg.package_id))
    items = [ReviewItem.load(r) for r in rows]
    versions: dict[str, str | None] = {}
    out = []
    for item in items:
        if not can_read_doc(deps, req.caller, item.doc_id):
            continue
        if item.engine.value == "textract" and item.bbox is not None and item.engine_model_version is None:
            key = textract_page_key(item.package_id, item.doc_id, item.pipeline_version, item.bbox.page)
            if key not in versions:
                data = store.read_json(deps, key)
                versions[key] = data.get("AnalyzeDocumentModelVersion") if isinstance(data, dict) else None
            item = item.model_copy(update={"engine_model_version": versions[key]})
        out.append(item)
    return Reply(ReviewList(items=out))
