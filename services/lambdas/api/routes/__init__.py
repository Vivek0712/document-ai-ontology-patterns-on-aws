"""Route modules register on ``router``; ``lambdas.api.handler`` imports them all."""

from __future__ import annotations

from ..http import Router

router = Router()

# Resource templates shared with infra/lib/api-routes.ts (the API Gateway resource tree).
ME = "/me"
PACKAGES = "/packages"
PACKAGE = "/packages/{packageId}"
PACKAGE_STATUS = "/packages/{packageId}/status"
DOCUMENT = "/documents/{docId}"
DOC_STAGE = "/documents/{docId}/lanes/{pv}/stages/{stageId}"
DOC_OVERLAY = "/documents/{docId}/lanes/{pv}/stages/{stageId}/overlay"
DOC_EVENTS = "/documents/{docId}/lanes/{pv}/events"
DOC_UNITS = "/documents/{docId}/lanes/{pv}/units"
DOC_REPROCESS = "/documents/{docId}/reprocess"
UNIT = "/units/{unitId}"
UNIT_LINEAGE = "/units/{unitId}/lineage"
ARTIFACTS = "/artifacts"
PIPELINES = "/pipelines"
PIPELINE_DIFF = "/pipelines/diff"
PIPELINE = "/pipelines/{pv}"
RUNS = "/runs"
RUN = "/runs/{runId}"
REVIEW = "/review"
# v2 inspection routes
DOC_BUILD = "/documents/{docId}/lanes/{pv}/build"
INSPECT_FILTERS = "/inspect/filters"
RUN_CALL_PATH = "/runs/{runId}/calls/{callId}/path"
SHOWCASES = "/showcases"
SHOWCASE_PAYLOAD = "/showcases/{showcaseId}/payload"
# v2 governance, knowledge and measurement routes
ACCESS_DECISIONS = "/access/decisions"
GOVERNED_DEFINITION = "/governance/definitions/{name}"
REVIEW_DECISION = "/review/{packageId}/items/{reviewKey}/decision"
EVAL_RUNS = "/eval/runs"
EVAL_RUN = "/eval/runs/{evalRunId}"
COST = "/cost"
ONTOLOGY_CLASSES = "/ontology/classes"
ONTOLOGY_TERM = "/ontology/terms/{iri}"
GRAPH_NEIGHBOURHOOD = "/graph/neighbourhood"
# COA environment lifecycle (Settings → COA environment; proxied to rp-coa-control)
COA_ENV = "/coa/environment"
COA_ENV_UP = "/coa/environment/up"
COA_ENV_DOWN = "/coa/environment/down"
COA_ENV_SETTINGS = "/coa/environment/settings"
COA_ENV_KEEP_ALIVE = "/coa/environment/keep-alive"
