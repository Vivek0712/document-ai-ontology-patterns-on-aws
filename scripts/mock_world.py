"""``make mock-world``: the UI's v1 mock world, exported from the real local run (nothing typed by hand).

Inputs:
- ``--build`` (repeatable; default ``build/e2e`` then ``build/e2e-{quarterly,tradeconf,lending-02}`` when present):
  one local pipeline run per package and its world snapshot (``make e2e-local``, ``make e2e-packages``):
  Bronze bytes and their sha256, every stage record and event, Silver/Gold units, the PII gate's decisions
  and held regions, the masked Gold page renders, index receipts and the review queue;
- ``--runs`` (default ``runs/local``): the logged local runs (``make local-ask``), RunRecords and event
  streams, loaded into the world's ``rp-runs`` table and ``runs/`` prefix as the agent's run store writes them.

Every object in ``world.json`` is a **response of the real API code**: the ``rp-api`` Lambda handler
(packages, documents, stages, events, stage overlays, units, lineage, artifacts, pipelines, diff, runs,
review) and the
``rp-get-region`` handler, invoked in-process over the snapshot (moto), as User A (the package owner) and
User B (another group). The only edits are URLs: presigned S3 URLs become ``/mock/pages/…`` paths for the
masked Gold page PNGs copied next to ``world.json``, and other presigned URLs are dropped.

The v2 inspection routes are exported the same way: ``builds`` (``GET /documents/{docId}/lanes/{pv}/build``),
``filterInspections`` and ``graphPaths`` (``GET /inspect/filters`` and ``GET /runs/{runId}/calls/{callId}/path``
for the representative logged runs' tool calls; counts from the in-memory index stand-in, labelled), and
``showcasePayloads`` (``GET /showcases/sc-3/payload`` per security, ``rp-graph-hop`` run in-process on pyoxigraph).
Each package keeps its own ``baseTime`` (its builds ran at different times).

It also adds, the same way: ``accessDecisions`` (``GET /access/decisions`` per logged run), the ``sc-7`` and
``sc-10`` composer payloads, ``regionChecks`` and ``askChecks`` (User B through the real ``rp-get-region`` and
``POST /ask`` handlers: the 403s SC-7 shows), ``cost`` (``GET /cost`` with ``RP_PUBLIC_METRICS`` off; the published
view too only with ``--public-metrics``, see ``METRIC_STATES``),
``graphNeighbourhoods`` (``neighbourhood_v1`` on the in-process ``rp-graph-hop``), ``ontology`` (``GET
/ontology/classes`` and the fin:/doc: terms with ``usedBy``), ``evalRuns`` (``GET /eval/runs`` over
``eval/results/e-logged-*``, in one moto world holding every package) and ``governance`` (``GET
/governance/definitions/qualifying_income``, the record ``make ontology-check`` writes).

``asks`` holds the event stream of the representative logged run of the headline question per lane;
``askReplays`` holds the same for every other golden question that has logged runs (per package: two packages ask
the same question); ``localRuns`` is the N-of-N summary from ``runs/local/index.json`` (one row per cohort: the gate
runs, SC-10's bigger model, SC-13's compact tier, SC-7's User B) and ``runScores`` the eval harness's score of each
run. Replays and N of N are the gate cohort's (default generation profile, User A).

Writes ``usecases/provenant/ui/mock-public/mock/world.json`` (the core every screen needs at start),
``packages/{packageId}.json``
(per-package shards the mock API fetches the first time a route touches that package), ``knowledge.json`` (the
ontology explorer's export), ``runs/*.json`` (run records), ``artifacts/*.txt`` (large artifact previews),
``pages/*.png`` and ``config/*.yaml`` (mock and replay modes only). The export is deterministic: it is dated by its
inputs, so rebuilding unchanged data gives no diff.

    PYTHONPATH=services:packages/evidence-core .venv/bin/python scripts/mock_world.py
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[1]
GOLDEN_KEY = "config/golden.jsonl"  # the golden set as deployed (W-16 reads it through GET /artifacts)
for _p in (REPO / "services", REPO / "packages" / "evidence-core", REPO / "services" / "agent"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from evidence_core.ddb import TABLE_SCHEMA, to_ddb  # noqa: E402
from evidence_core.ids import principals_for, pv_slug  # noqa: E402
from evidence_core.models.answer import RunRecord  # noqa: E402
from evidence_core.models.golden import load_golden, normalise_question  # noqa: E402
from local_pipeline.world import LocalWorld, open_world  # noqa: E402

LANES = ("1.0", "2.3.1")
USER_B = ("b5d09f13-0000-4000-8000-00000000000b", "user-b", ["markets-analyst"])
OUT = REPO / "usecases" / "provenant" / "ui" / "mock-public" / "mock"
# The RP_PUBLIC_METRICS views the export holds. The mock world is static files every mock and replay
# build serves, so an unpublished export never holds the published views (dollar amounts, per-lane rates): only
# `--public-metrics` (`make mock-world PUBLIC_METRICS=on`) adds them, for a build where publishing is decided.
METRIC_STATES: tuple[str, ...] = ("off",)
# Study numbers that must never appear in fixtures
BANNED = [r"\b44 ?%", r"\b16[–-]25\b", r"\b17[–-]33\b", r"\b81/85\b", r"\b47/73\b", r"\b82/81\b", "15 points"]


class ApiFailure(RuntimeError):
    pass


def event(method: str, resource: str, *, sub: str, username: str, groups: list[str],
          path_params: dict[str, str] | None = None, query: dict[str, str] | None = None) -> dict[str, Any]:
    """An API Gateway proxy event with Cognito claims, as API Gateway hands it to the Lambda."""
    path = resource
    for k, v in (path_params or {}).items():
        path = path.replace("{" + k + "}", quote(v, safe=":"))
    claims = {"sub": sub, "username": username, "email": f"{username}@example.com", "client_id": "rp-web",
              "cognito:groups": "[" + " ".join(groups) + "]", "token_use": "access"}
    return {"resource": resource, "path": path, "httpMethod": method,
            "headers": {"Origin": "http://localhost:5188", "Authorization": "Bearer local"},
            "pathParameters": path_params, "queryStringParameters": query, "body": None, "isBase64Encoded": False,
            "requestContext": {"requestId": "mock-world", "authorizer": {"claims": claims}}}


class Api:
    """Calls the real handlers as one user."""

    def __init__(self, sub: str, username: str, groups: list[str]) -> None:
        from lambdas.api.handler import handler as api
        from lambdas.get_region_http.handler import handler as region

        self.sub, self.username, self.groups = sub, username, groups
        self._api, self._region = api, region

    def _call(self, fn: Any, method: str, resource: str, **kw: Any) -> tuple[int, Any]:
        resp = fn(event(method, resource, sub=self.sub, username=self.username, groups=self.groups, **kw), None)
        return resp["statusCode"], json.loads(resp["body"]) if resp.get("body") else None

    def get(self, resource: str, ok: Iterable[int] = (200,), **kw: Any) -> Any:
        status, body = self._call(self._api, "GET", resource, **kw)
        if status not in ok:
            raise ApiFailure(f"GET {resource} {kw} -> {status} {body}")
        return body if status == 200 else None

    def region(self, unit_id: str, field_id: str | None) -> dict[str, Any] | None:
        status, body = self.region_status(unit_id, field_id)
        return body if status == 200 else None

    def region_status(self, unit_id: str, field_id: str | None) -> tuple[int, Any]:
        return self._call(self._region, "GET", "/region/{unitId}", path_params={"unitId": unit_id},
                          query={"field": field_id} if field_id else None)

    def ask_status(self, package_id: str, question: str, pv: str) -> tuple[int, Any]:
        """``POST /ask`` through the real handler: the package check runs before any agent call."""
        from lambdas.api.ask.handler import handler as ask

        ev = event("POST", "/ask", sub=self.sub, username=self.username, groups=self.groups)
        ev["body"] = json.dumps({"question": question, "packageId": package_id, "pipelineVersion": pv})
        resp = ask(ev, None)
        body = resp.get("body") or ""
        try:
            parsed: Any = json.loads(body)
        except json.JSONDecodeError:
            parsed = body
        return resp["statusCode"], parsed

    def get_status(self, resource: str, **kw: Any) -> tuple[int, Any]:
        return self._call(self._api, "GET", resource, **kw)


# --- URLs ----------------------------------------------------------------------------------------

def page_file(key: str) -> str | None:
    """gold/{pkg}/{docId}/{pv}/pages/p001.png -> {docId}-{pv}-p001.png"""
    m = re.match(r"gold/[^/]+/([^/]+)/([^/]+)/pages/(p\d+)\.png$", key)
    return f"{m.group(1)}-{pv_slug(m.group(2))}-{m.group(3)}.png" if m else None


def key_of_presigned(url: str | None) -> str | None:
    if not url:
        return None
    m = re.match(r"https?://[^/]+/(?:rp-data-local/)?([^?]+)", url)
    return m.group(1) if m else None


def mock_url(key: str | None, pages: dict[str, str]) -> str | None:
    if key is None:
        return None
    if key in pages:
        return f"/mock/pages/{pages[key]}"
    m = re.match(r"config/pipelines/v(.+)\.yaml$", key)
    return f"/mock/config/v{m.group(1)}.yaml" if m else None


# --- runs ----------------------------------------------------------------------------------------

def load_runs(runs_dir: Path, world: LocalWorld, package_id: str) -> list[RunRecord]:
    """Put the package's logged local runs where the agent's run store would: runs/{runId}.json + rp-runs rows."""
    from rp_agent.runs import summary_of

    records = []
    for path in sorted(runs_dir.glob("r-*.json")):
        raw = json.loads(path.read_text())
        rec = RunRecord.load(raw)
        if rec.package_id != package_id:
            continue
        world.s3.put_object(Bucket=world.bucket, Key=rec.s3_key, Body=path.read_bytes(), ContentType="application/json")
        world.dynamodb.Table(TABLE_SCHEMA["runs"]["name"]).put_item(Item=to_ddb(summary_of(rec).dump()))
        records.append(rec)
    return records


def run_events(runs_dir: Path, run_id: str) -> list[dict[str, Any]]:
    path = runs_dir / "events" / f"{run_id}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


# --- export ----------------------------------------------------------------------------------------

def export_package(build: Path, runs_dir: Path, pages_dir: Path, index: dict[str, Any]) -> dict[str, Any]:
    """Every rp-api / rp-get-region response for the one package a local build holds, as User A (and User B's
    view of the package), plus the v2 inspection routes over the same snapshot."""
    from evidence_core.config import LocalConfigSource
    from evidence_core.env import Settings
    from lambdas.api.deps import Deps, set_deps
    from lambdas.tools._lib import stores as tool_stores

    with open_world(build) as world:
        deps = Deps(settings=Settings.from_env(), ddb=world.dynamodb, s3=world.s3,
                    config_source=LocalConfigSource(root=REPO))
        set_deps(deps)
        tool_stores.use_stores(tool_stores.Stores(settings=Settings.from_env(), dynamodb=world.dynamodb, s3=world.s3))
        try:
            pkg_row = world.table_items("packages")[0]
            package_id = pkg_row["packageId"]
            records = load_runs(runs_dir, world, package_id)
            owner = pkg_row["ownerSub"]
            groups = list(world.meta.get("groups", ["lending-analyst"]))
            a = Api(owner, "user-a", groups)
            b = Api(*USER_B)

            # page renders: every masked Gold PNG the run wrote
            pages: dict[str, str] = {}
            for key in world.objects:
                name = page_file(key)
                if name:
                    (pages_dir / name).write_bytes(world.read_bytes(key))
                    pages[key] = name

            def fix_url(obj: dict[str, Any]) -> dict[str, Any]:
                if "pageImageUrl" in obj:  # stage overlays carry their page render as a presigned URL too
                    obj = obj | {"pageImageUrl": mock_url(key_of_presigned(obj.get("pageImageUrl")), pages)}
                if "url" in obj:
                    url = mock_url(key_of_presigned(obj.get("url")), pages)
                    obj = {k: v for k, v in obj.items() if k not in ("url", "expiresIn")}
                    if url:
                        obj |= {"url": url, "expiresIn": 300}
                return obj

            pkg = a.get("/packages/{packageId}", path_params={"packageId": package_id})
            docs = pkg["documents"]
            lanes: dict[str, dict[str, Any]] = {}
            unit_lists: dict[str, dict[str, Any]] = {}
            units: dict[str, Any] = {}
            lineage: dict[str, Any] = {}
            regions: dict[str, Any] = {}
            overlays: dict[str, Any] = {}
            builds: dict[str, Any] = {}
            for d in docs:
                doc_id = d["docId"]
                detail = a.get("/documents/{docId}", path_params={"docId": doc_id})
                for pv in LANES:
                    lane = detail["lanes"].get(pv)
                    if not lane:
                        continue
                    evs: list[dict[str, Any]] = []
                    cursor = None
                    while True:
                        page = a.get("/documents/{docId}/lanes/{pv}/events", path_params={"docId": doc_id, "pv": pv},
                                     query={"limit": "200"} | ({"cursor": cursor} if cursor else {}))
                        evs += page["items"]
                        cursor = page.get("nextCursor")
                        if not cursor:
                            break
                    lanes.setdefault(doc_id, {})[pv] = {"lane": lane["lane"], "stages": lane["stages"], "events": evs}
                    for rec in lane["stages"]:
                        for page_no in range(1, int(detail["document"].get("pageCount") or 1) + 1):
                            ov = a.get("/documents/{docId}/lanes/{pv}/stages/{stageId}/overlay", ok=(200, 404),
                                       path_params={"docId": doc_id, "pv": pv, "stageId": rec["stageId"]},
                                       query={"page": str(page_no)})
                            if ov:
                                overlays[f"{doc_id}|{pv}|{rec['stageId']}|{page_no}"] = fix_url(ov)
                    build_view = a.get("/documents/{docId}/lanes/{pv}/build", ok=(200, 404),
                                       path_params={"docId": doc_id, "pv": pv})
                    if build_view:
                        builds.setdefault(doc_id, {})[pv] = build_view
                    ul = a.get("/documents/{docId}/lanes/{pv}/units", path_params={"docId": doc_id, "pv": pv})
                    unit_lists.setdefault(doc_id, {})[pv] = ul
                    for item in ul["items"]:
                        uid = item["unitId"]
                        view = a.get("/units/{unitId}", path_params={"unitId": uid})
                        units[uid] = view
                        if view["held"]:
                            continue
                        lin = a.get("/units/{unitId}/lineage", ok=(200, 404), path_params={"unitId": uid})
                        if lin:
                            lineage[uid] = lin
                        reg = a.region(uid, None)
                        if reg:
                            regions[uid] = fix_url(reg)
                        for f in view["unit"].get("fields") or []:
                            fid = f["fieldId"]
                            lin = a.get("/units/{unitId}/lineage", ok=(200, 404), path_params={"unitId": uid},
                                        query={"field": fid})
                            if lin:
                                lineage[f"{uid}|{fid}"] = lin
                            reg = a.region(uid, fid)
                            if reg:
                                regions[f"{uid}|{fid}"] = fix_url(reg)

            # the golden set, published next to the configs as the deployment does (infra base-stack GoldenDeploy)
            world.s3.put_object(Bucket=world.bucket, Key=GOLDEN_KEY, Body=(REPO / "eval" / "golden.jsonl").read_bytes(),
                                ContentType="application/x-ndjson")
            # artifacts: every object in the bucket as the owner sees them; of the run records, only the
            # representative ones (the others are in ``runs`` already)
            representative = {s.get("representativeRunId") for s in index.get("summary", [])}
            artifacts = {}
            for key in _all_keys(world):
                if key.startswith("runs/") and key.removeprefix("runs/").removesuffix(".json") not in representative:
                    continue
                view = a.get("/artifacts", ok=(200, 403, 404), query={"key": key})
                if view and key == GOLDEN_KEY:
                    # put during this export, so moto's version id and time would differ run to run: drop them
                    view = {k: v for k, v in view.items() if k not in ("versionId", "lastModified")}
                if view:
                    artifacts[key] = fix_url(view)
            for pv in LANES:
                lp = deps.pipeline(pv)
                view = a.get("/artifacts", ok=(200, 404), query={"key": lp.source_key})
                if view:
                    artifacts[lp.source_key] = fix_url(view) | {"url": f"/mock/config/v{pv}.yaml", "expiresIn": 300}

            inspections = inspect_runs(a, deps, world, [r for r in records if r.run_id in representative])
            with local_graph(deps, build):
                showcases = showcase_payloads(a, package_id, units)
                v2 = export_v2(a, b, deps, package_id, records, units)
            pipelines = a.get("/pipelines")
            details = {pv: a.get("/pipelines/{pv}", path_params={"pv": pv}) for pv in LANES}
            diff = a.get("/pipelines/diff", query={"a": LANES[0], "b": LANES[1]})
            review = a.get("/review", query={"packageId": package_id})
            run_page = a.get("/runs", query={"packageId": package_id, "limit": "200"})
            runs = [a.get("/runs/{runId}", path_params={"runId": r.run_id}) for r in records]
            me_a, me_b = a.get("/me"), b.get("/me")
            allowed_b = set(principals_for(USER_B[0], USER_B[2])) & set(pkg["package"]["allowedPrincipals"])
            seen_by_b = b.get("/packages/{packageId}", ok=(200, 403), path_params={"packageId": package_id})
            assert (seen_by_b is not None) == bool(allowed_b), f"User B's access to {package_id} breaks its ACL"
        finally:
            set_deps(None)
            tool_stores.use_stores(None)

    base = min(datetime.fromisoformat(d["uploadUrlIssuedAt"].replace("Z", "+00:00")) for d in docs)
    # The snapshot has no pipeline registry, so the API falls back to items derived from the configs and stamps
    # them "now"; seed.sh registers them before any upload, so date them at the run's first upload. Run records
    # are put into moto by this export (they were never S3 objects locally): no versionId, dated by the run.
    first = base.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    for item in [*pipelines["items"], *(d["item"] for d in details.values())]:
        item["registeredAt"] = first
    created = {r["runId"]: r["createdAt"] for r in runs}
    for key, view in artifacts.items():
        if key.startswith("runs/"):
            view.pop("versionId", None)
            run_id = key.removeprefix("runs/").removesuffix(".json")
            view["lastModified"] = created.get(run_id, view.get("lastModified"))
    ends = [lane["lane"].get("endedAt") for per in lanes.values() for lane in per.values()]
    return {
        "packageId": package_id, "build": str(build.relative_to(REPO)), "base": base,
        "asOf": max(e for e in ends if e), "engines": {k: json.loads((build / "run.json").read_text()).get(k)
                                                        for k in ("docling", "scanPages", "phaseBEmbeddings", "piiGate",
                                                                  "aws")},
        "users": {"a": me_a, "b": me_b},
        "package": pkg | {"baseTime": (base - timedelta(milliseconds=600)).isoformat().replace("+00:00", "Z")},
        "lanes": lanes, "overlays": overlays, "units": units, "unitLists": unit_lists, "lineage": lineage,
        "regions": regions, "artifacts": artifacts, "review": review["items"], "pipelines": pipelines,
        "pipelineDetails": details, "diff": diff, "runs": runs, "runSummaries": run_page["items"],
        "builds": builds, **inspections, "showcasePayloads": showcases | v2.pop("showcasePayloads"), **v2,
    }


def inspect_runs(a: Api, deps: Any, world: LocalWorld, records: list[RunRecord]) -> dict[str, Any]:
    """``GET /inspect/filters`` for every ``search_units`` call and ``GET /runs/{runId}/calls/{callId}/path`` for
    every ``graph_hop`` call of the representative logged runs. Counts come from the in-memory stand-in for
    OpenSearch over the run's own index documents (labelled in ``countedBy``)."""
    from local_agent.index import IndexStore, MemoryOpenSearch

    deps.aoss = MemoryOpenSearch(indexes={name: IndexStore(docs) for name, docs in world.aoss_docs.items()})
    filters: dict[str, Any] = {}
    paths: dict[str, Any] = {}
    for rec in records:
        for c in rec.tool_calls:
            if c.status != "ok":
                continue
            key = f"{rec.run_id}|{c.call_id}"
            if c.tool == "search_units":
                # counted at export, not at the run's time; null keeps the export deterministic (the UI says so)
                view = a.get("/inspect/filters", query={"runId": rec.run_id, "callId": c.call_id})
                view["countedAt"] = None
                view["sources"] = [src | {"at": None} for src in view["sources"]]
                filters[key] = view
            elif c.tool == "graph_hop":
                paths[key] = a.get("/runs/{runId}/calls/{callId}/path",
                                   path_params={"runId": rec.run_id, "callId": c.call_id})
    return {"filterInspections": filters, "graphPaths": paths}


class _RpApiContext:
    """The Lambda context ``rp-api``'s invocations carry (``rpSource: rp-api`` in the client context), so the
    in-process ``rp-graph-hop`` sees what it sees on AWS (the API-only templates are accepted only from it)."""

    class client_context:  # noqa: N801 - mirrors the LambdaContext attribute
        custom = {"rpSource": "rp-api"}


class local_graph:  # noqa: N801 - a context manager used like a function
    """``rp-graph-hop`` in-process over the build's TBox and ABox on pyoxigraph (as ``make e2e-local`` checked it),
    installed as ``deps.graph_hop`` for the routes and composers that invoke it."""

    def __init__(self, deps: Any, build: Path) -> None:
        self.deps, self.build = deps, build

    def __enter__(self) -> local_graph:
        from evidence_core.config import load_pipeline
        from lambdas.tools.graph_hop import handler as graph_hop
        from local_pipeline.graph_check import OxiSparql, load_tbox

        sparql = OxiSparql()
        load_tbox(sparql, REPO, load_pipeline("2.3.1").config.ontology_version or "1.2.0")
        sparql.load_nquads((self.build / "graph" / "abox.nq").read_text(encoding="utf-8"))
        graph_hop.use_client(sparql)
        self.deps.graph_hop = lambda event: graph_hop.handler(event, _RpApiContext())
        return self

    def __exit__(self, *exc: Any) -> None:
        from lambdas.tools.graph_hop import handler as graph_hop

        graph_hop.use_client(None)
        self.deps.graph_hop = None


def local_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Say what a composed payload is: composed over the local snapshot (``local-run``), the graph on pyoxigraph."""
    from lambdas.api.routes.showcases import payload_sha

    payload["payloadSha"] = payload_sha(payload["data"])
    payload["mode"] = "local-run"
    payload["provenance"] = {k: "local-run" if v == "measured" else v for k, v in payload["provenance"].items()}
    for src in payload["sources"]:
        if src["kind"] == "neptune":
            src.update(kind="local", key=f"pyoxigraph in-process (not Neptune) · {src['key']}")
    return payload


def showcase_payloads(a: Api, package_id: str, units: dict[str, Any]) -> dict[str, Any]:
    """``GET /showcases/sc-3/payload`` for every security a 2.3.1 unit prints (inside ``local_graph``)."""
    securities = sorted({f["value"] for v in units.values() if v["unit"]["pipelineVersion"] == "2.3.1"
                         for f in v["unit"].get("fields") or [] if f.get("concept") == "fin:SecurityIdentifier"})
    out = {}
    for sec in securities:
        payload = a.get("/showcases/{showcaseId}/payload", path_params={"showcaseId": "sc-3"},
                        query={"packageId": package_id, "security": sec})
        # keep the export deterministic: the composer's per-request run id names the export instead, and the
        # in-process tool's wall-clock latency is dropped (the AWS route returns the measured one)
        payload["data"]["graph"] |= {"runId": "mock-world-export", "latencyMs": None}
        if payload["data"].get("path"):
            payload["data"]["path"]["latencyMs"] = None  # the same wall clock, carried by the path
        out[f"sc-3|{package_id}|{sec}"] = local_payload(payload)
    return out


GRAPH_FOCUS_EXTRA = ("fin:QualifyingIncome",)  # beside every fin: concept the package's 2.3.1 fields carry


def export_v2(a: Api, b: Api, deps: Any, package_id: str, records: list[RunRecord],
              units: dict[str, Any]) -> dict[str, Any]:
    """v2 routes over one package snapshot, as Users A and B (inside ``local_graph``):
    access decisions for every logged run; the SC-7 and SC-10 composers for each question that has runs by another
    user or with another generation profile; cost (published and not); graph neighbourhoods around the package's
    concepts; each concept's ``usedBy``; B's region request for the cited field and B's ``POST /ask`` (both real
    handler responses: the 403s SC-7 shows)."""
    import os

    access = {r.run_id: a.get("/access/decisions", query={"runId": r.run_id}) for r in records}
    owner = next((r.caller_sub for r in records if r.generation_profile == "default"), None)
    payloads: dict[str, Any] = {}
    region_checks: dict[str, Any] = {}
    ask_checks: dict[str, Any] = {}
    for question in sorted({r.question for r in records}):
        same = [r for r in records if r.question == question]
        if any(r.generation_profile == "frontier" for r in same):
            for lane in sorted({r.pipeline_version for r in same if r.generation_profile == "frontier"}):
                p = a.get("/showcases/{showcaseId}/payload", ok=(200, 404), path_params={"showcaseId": "sc-10"},
                          query={"packageId": package_id, "question": question, "lane": lane})
                if p:
                    payloads[f"sc-10|{package_id}|{normalise_question(question)}|{lane}"] = local_payload(p)
        if owner and any(r.caller_sub != owner for r in same):
            p = a.get("/showcases/{showcaseId}/payload", ok=(200, 404), path_params={"showcaseId": "sc-7"},
                      query={"packageId": package_id, "question": question})
            if p:
                payloads[f"sc-7|{package_id}|{normalise_question(question)}"] = local_payload(p)
                probe = p["data"].get("regionProbe")
                if probe:
                    status, body = b.region_status(probe["unitId"], probe.get("fieldId"))
                    region_checks[f"{probe['unitId']}|{probe.get('fieldId') or ''}"] = {
                        "as": "b", "status": status, "body": body if status != 200 else None}
                for pv in sorted({r.pipeline_version for r in same}):
                    status, body = b.ask_status(package_id, question, pv)
                    ask_checks[f"{normalise_question(question)}|{pv}"] = {"as": "b", "status": status, "body": body}
    cost: dict[str, Any] = {}
    flag = os.environ.get("RP_PUBLIC_METRICS")
    try:
        os.environ["RP_PUBLIC_METRICS"] = "off"
        cost["off"] = {"docType": a.get("/cost", query={"packageId": package_id, "groupBy": "docType"})}
        if "on" in METRIC_STATES:
            os.environ["RP_PUBLIC_METRICS"] = "on"
            cost["on"] = {g: a.get("/cost", query={"packageId": package_id, "groupBy": g})
                          for g in ("docType", "stage", "lane", "service")}
    finally:
        if flag is None:
            os.environ.pop("RP_PUBLIC_METRICS", None)
        else:
            os.environ["RP_PUBLIC_METRICS"] = flag
    concepts = sorted({f["concept"] for v in units.values() if v["unit"]["pipelineVersion"] == "2.3.1"
                       for f in v["unit"].get("fields") or [] if (f.get("concept") or "").startswith("fin:")})
    focus = sorted(set(concepts) | set(GRAPH_FOCUS_EXTRA))
    neighbourhoods = {}
    for iri in focus:
        for depth in ("1", "2"):
            view = a.get("/graph/neighbourhood", query={"iri": iri, "packageId": package_id, "depth": depth})
            view["latencyMs"] = None  # the in-process wall clock (the AWS route returns the measured one)
            neighbourhoods[f"{iri}|{depth}"] = view
    used_by = {iri: a.get("/ontology/terms/{iri}", path_params={"iri": iri}).get("usedBy") or {} for iri in focus}
    return {"accessDecisions": access, "showcasePayloads": payloads, "regionChecks": region_checks,
            "askChecks": ask_checks, "cost": cost, "graphNeighbourhoods": neighbourhoods, "usedBy": used_by}


def export_eval(parts: list[dict[str, Any]], results: Path) -> dict[str, Any]:
    """``GET /eval/runs`` and ``GET /eval/runs/{id}`` (published and not) over every ``eval/results/e-logged-*``
    result, in one moto world that holds every package (a result spans packages; the route checks each)."""
    import os

    from moto import mock_aws

    files = sorted(results.glob("e-logged-*.json"))
    if not files:
        return {}
    with mock_aws():
        import boto3
        from evidence_core.config import LocalConfigSource
        from evidence_core.ddb import create_tables_for_tests
        from evidence_core.env import Settings
        from lambdas.api.deps import Deps, set_deps

        os.environ.setdefault("RP_DATA_BUCKET", "rp-data-local")
        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        create_tables_for_tests(ddb)
        s3 = boto3.client("s3", region_name="us-east-1")
        settings = Settings.from_env()
        s3.create_bucket(Bucket=settings.data_bucket)
        for part in parts:
            ddb.Table(settings.table("packages")).put_item(Item=to_ddb(part["package"]["package"]))
        for f in files:
            s3.put_object(Bucket=settings.data_bucket, Key=f"eval/results/{f.name}", Body=f.read_bytes())
        set_deps(Deps(settings=settings, ddb=ddb, s3=s3, config_source=LocalConfigSource(root=REPO)))
        a, b = Api(parts[0]["package"]["package"]["ownerSub"], "user-a", ["lending-analyst"]), Api(*USER_B)
        flag = os.environ.get("RP_PUBLIC_METRICS")
        try:
            out: dict[str, Any] = {"list": {}, "runs": {}, "asB": {}}
            for state in METRIC_STATES:
                os.environ["RP_PUBLIC_METRICS"] = state
                out["list"][state] = a.get("/eval/runs")
                for f in files:
                    out["runs"].setdefault(f.stem, {})[state] = a.get("/eval/runs/{evalRunId}",
                                                                      path_params={"evalRunId": f.stem})
            out["asB"] = {f.stem: b.get_status("/eval/runs/{evalRunId}", path_params={"evalRunId": f.stem})[0]
                          for f in files}
            return out
        finally:
            set_deps(None)
            if flag is None:
                os.environ.pop("RP_PUBLIC_METRICS", None)
            else:
                os.environ["RP_PUBLIC_METRICS"] = flag


def export(builds: list[Path], runs_dir: Path, out: Path) -> dict[str, Any]:
    """One world over every package build; the first build is the lending package the hero showcases replay."""
    golden = load_golden(REPO / "eval" / "golden.jsonl")
    headline = next(g for g in golden if g.qid == "q-lend-01")
    index = json.loads((runs_dir / "index.json").read_text()) if (runs_dir / "index.json").exists() else {}
    pages_dir = out / "pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir(parents=True)
    parts = [export_package(build, runs_dir, pages_dir, index) for build in builds]
    primary = parts[0]

    # replays of logged runs: representative run per (question, lane)
    by_q = {g.qid: g for g in golden}
    asks: dict[str, list[dict[str, Any]]] = {}
    replays: dict[str, dict[str, Any]] = {}
    for s in index.get("summary", []):
        rid = s.get("representativeRunId")
        g = by_q.get(s["qid"])
        if not rid or not g:
            continue
        evs = run_events(runs_dir, rid)
        if not evs:
            continue
        if (s.get("generationProfile") or "default") != "default" or (s.get("caller") or "a") != "a":
            continue  # replays are the gate cohort's runs; the other cohorts are read through their composers
        if g.qid == headline.qid:
            asks[s["pipelineVersion"]] = evs
        # per package: two packages ask the same question ("What is the total income across this package?")
        key = f"{g.package_id}|{normalise_question(g.question)}"
        entry = replays.setdefault(key, {"qid": g.qid, "question": g.question, "packageId": g.package_id,
                                         "lanes": {}})
        entry["lanes"][s["pipelineVersion"]] = evs

    def merged(key: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for part in parts:
            out.update(part[key])
        return out

    used_by: dict[str, dict[str, int]] = {}
    for part in parts:
        for iri, counts in part["usedBy"].items():
            used_by.setdefault(iri, {}).update(counts)

    # the eval harness's score of each logged run (eval/run.py outcome_from_events), joined onto its summary
    def gold_of(qid: str) -> dict[str, Any] | None:
        g = by_q.get(qid)
        if not g or not g.doc_id:
            return None
        return {"docId": g.doc_id, "page": g.page, "bbox": g.gold_bbox.dump() if g.gold_bbox else None}

    scores = {r["runId"]: {"qid": r["qid"], "expected": r["expected"], "answerCorrect": bool(r.get("answerCorrect")),
                           "citationValid": bool(r.get("citationValid")), "by": "eval/run.py outcome_from_events",
                           "gold": gold_of(r["qid"])}
              for r in index.get("runs", []) if r.get("runId")}
    unit_lists, units = merged("unitLists"), merged("units")
    # deterministic: the export is dated by its inputs (last lane end, last logged run), never by the clock
    as_of = max([p["asOf"] for p in parts] + ([index["updatedAt"]] if index.get("updatedAt") else []))
    return {
        "generatedBy": "scripts/mock_world.py",
        "generatedAt": as_of,
        "source": {"mode": "local-run", "build": primary["build"], "builds": [p["build"] for p in parts],
                   "runs": str(runs_dir.relative_to(REPO)),
                   "api": "rp-api + rp-get-region handlers over each package's world snapshot (moto)",
                   "engines": primary["engines"], "enginesByPackage": {p["packageId"]: p["engines"] for p in parts},
                   "runsProvenance": index.get("provenance")},
        "baseTime": (primary["base"] - timedelta(milliseconds=600)).isoformat().replace("+00:00", "Z"),
        "users": primary["users"],
        "packages": [p["package"] for p in parts],
        "lanes": merged("lanes"), "overlays": merged("overlays"), "units": units, "unitLists": unit_lists,
        "lineage": merged("lineage"), "regions": merged("regions"), "artifacts": merged("artifacts"),
        "review": [r for p in parts for r in p["review"]],
        "pipelines": primary["pipelines"], "pipelineDetails": primary["pipelineDetails"], "diff": primary["diff"],
        "asks": asks, "headlineQuestion": headline.question, "askReplays": replays,
        "runs": [r for p in parts for r in p["runs"]],
        "runSummaries": sorted((s for p in parts for s in p["runSummaries"]), key=lambda s: s["createdAt"],
                               reverse=True),
        "runScores": scores,
        "localRuns": {"provenance": index.get("provenance"),
                      "summary": [row | {"question": by_q[row["qid"]].question,
                                         "packageId": by_q[row["qid"]].package_id,
                                         "goldRegion": gold_region(by_q[row["qid"]], row["pipelineVersion"],
                                                                   unit_lists, units)}
                                  for row in index.get("summary", []) if row["qid"] in by_q],
                      "spentUsd": index.get("spentUsd"), "laneParity": index.get("laneParity")},
        "governance": core_get("/governance/definitions/{name}", path_params={"name": "qualifying_income"}),
        # v2 inspection routes, exported from in-process calls like everything above
        "builds": merged("builds"),
        "filterInspections": merged("filterInspections"),
        "graphPaths": merged("graphPaths"),
        "showcaseCatalog": primary_catalog(),
        "showcasePayloads": merged("showcasePayloads"),
        # v2 routes (SC-7, SC-8, SC-10, SC-12, SC-13, W-09, W-11, W-13)
        "accessDecisions": merged("accessDecisions"),
        "regionChecks": merged("regionChecks"),
        "askChecks": {p["packageId"]: p["askChecks"] for p in parts if p["askChecks"]},
        "cost": {p["packageId"]: p["cost"] for p in parts},
        "graphNeighbourhoods": {p["packageId"]: p["graphNeighbourhoods"] for p in parts},
        "ontology": ontology_export(used_by),
        "evalRuns": export_eval(parts, REPO / "eval" / "results"),
    }


def primary_catalog() -> dict[str, Any]:
    from evidence_core.models.inspect import ShowcaseCatalog
    from lambdas.api.routes.showcases import COMPOSERS

    return ShowcaseCatalog(items=list(COMPOSERS.values())).dump()


def gold_region(gold: Any, pv: str, unit_lists: dict[str, Any], units: dict[str, Any]) -> dict[str, Any] | None:
    """What the lane holds where the golden answer is printed (derived from the API's unit views):
    ``held`` when a unit the PII gate held covers the gold box, ``no-units`` when the lane built no unit for the
    document (e.g. no text layer on a scan); ``None`` otherwise."""
    if not gold.doc_id or gold.doc_id not in unit_lists or pv not in unit_lists[gold.doc_id]:
        return None
    items = unit_lists[gold.doc_id][pv]["items"]
    if not items:
        return {"state": "no-units", "docId": gold.doc_id}
    g = gold.gold_bbox
    for it in items:
        view = units.get(it["unitId"])
        if not view or not view["held"]:
            continue
        boxes = view["unit"].get("bboxes") or []
        if g is None or any(b["page"] == g.page and b["x0"] < g.x1 and b["x1"] > g.x0 and b["y0"] < g.y1
                            and b["y1"] > g.y0 for b in boxes):
            return {"state": "held", "docId": gold.doc_id, "unitId": it["unitId"]}
    return None


def core_get(resource: str, **kw: Any) -> Any:
    """A route that reads configuration only (no package data), called in-process on the repo's config."""
    from evidence_core.config import LocalConfigSource
    from evidence_core.env import Settings
    from lambdas.api.deps import Deps, set_deps

    set_deps(Deps(settings=Settings.from_env(), ddb=None, s3=None, config_source=LocalConfigSource(root=REPO)))
    try:
        return Api("mock-world-export", "user-a", []).get(resource, **kw)
    finally:
        set_deps(None)


def ontology_export(used_by: dict[str, dict[str, int]]) -> dict[str, Any]:
    """``GET /ontology/classes`` (every module) and, for the terms the explorer opens first (``fin:`` and ``doc:``),
    ``GET /ontology/terms/{iri}`` with ``usedBy`` summed over the package snapshots (each counted by the route in its
    own world, as User A)."""
    classes = core_get("/ontology/classes")
    terms = {}
    for t in classes["items"]:
        if t["module"] in ("fin", "doc"):
            terms[t["curie"]] = t | {"usedBy": dict(sorted(used_by.get(t["curie"], {}).items()))}
    return {"classes": classes, "terms": terms}


def _all_keys(world: LocalWorld) -> list[str]:
    keys: list[str] = []
    for page in world.s3.get_paginator("list_objects_v2").paginate(Bucket=world.bucket):
        keys += [o["Key"] for o in page.get("Contents", [])]
    return sorted(keys)


def held_spans(build: Path) -> list[str]:
    """The text of every unmasked PII span the run's gate held (from its pii_decisions and Silver units)."""
    from evidence_core.models.units import PiiDecisions, Unit

    s3 = build / "world" / "s3"
    spans = []
    for path in s3.glob("silver/*/*/*/pii_gate/decisions.json"):
        lines = (path.parent.parent / "units" / "units.jsonl").read_text().splitlines()
        units = {u.unit_id: u for u in (Unit.load(json.loads(line)) for line in lines if line)}
        for d in PiiDecisions.load(json.loads(path.read_text())).decisions:
            for hit in d.entities:
                if not hit.masked and d.unit_id in units:
                    spans.append(units[d.unit_id].text[hit.begin:hit.end])
    return spans


def check(text: str, spans: list[str], where: str = "world.json") -> None:
    """No span the PII gate held appears anywhere, in any shape (the API masks them in Silver previews; a held
    span is not always a long digit run), and no study number is in the fixtures."""
    for span in spans:
        forms = {span, " ".join(span.split()), json.dumps(span, ensure_ascii=False)[1:-1]}
        if any(f.strip() and f in text for f in forms):
            raise SystemExit(f"a held span ({len(span)} chars) appears unmasked in {where}")
    for pat in BANNED:
        m = re.search(pat, text)
        if m:
            raise SystemExit(f"banned copy pattern {pat!r} near {text[max(0, m.start() - 60):m.end() + 20]!r}")


PREVIEW_INLINE_MAX = 2048


def split_out(world: dict[str, Any], out: Path) -> tuple[int, int]:
    """Keep ``world.json`` small: run records go to ``runs/{runId}.json`` and artifact previews over 2 KB to
    ``artifacts/{sha}.txt``; the mock API fetches them on demand (``previewFile``), as the real API returns them."""
    import hashlib

    runs_dir, art_dir = out / "runs", out / "artifacts"
    for d in (runs_dir, art_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    runs = world.pop("runs")
    for r in runs:
        (runs_dir / f"{r['runId']}.json").write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
    n = 0
    for key, view in world["artifacts"].items():
        preview = view.get("preview")
        if preview and len(preview) > PREVIEW_INLINE_MAX:
            name = f"{hashlib.sha256(key.encode()).hexdigest()[:20]}.txt"
            (art_dir / name).write_text(preview)
            view.pop("preview")
            view["previewFile"] = f"artifacts/{name}"
            n += 1
    return len(runs), n


# Per-package shards (``packages/{packageId}.json``): what only one package's screens read. ``world.json`` keeps what
# every screen needs at start (users, packages and their documents, lane and stage records with their events, which
# the package list and status project, pipelines, run summaries and scores, the headline replay, governance, eval)
# and names the shards; the mock API fetches a shard the first time a route touches that
# package. The ontology explorer's TBox export is its own file (``knowledge.json``), fetched on first use.
SHARDED_BY_DOC = ("unitLists", "builds")  # key = docId (lanes stay in the core: every package list projects them)
SHARDED_BY_UNIT = ("units", "lineage", "regions", "regionChecks")  # key = unitId[|fieldId]
SHARDED_BY_RUN = ("filterInspections", "graphPaths", "accessDecisions")  # key = runId[|callId]
SHARDED_BY_PACKAGE = ("askChecks", "cost", "graphNeighbourhoods")  # key = packageId


def shard(world: dict[str, Any], out: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Move every per-package value into ``packages/{packageId}.json``; returns bytes per shard and, per key, the
    entries skipped because their package has no build in this world (for example the logged ask runs of the
    packages that ``make e2e-packages`` builds, when only ``make e2e-local`` has run)."""
    doc_pkg = {d["docId"]: p["package"]["packageId"] for p in world["packages"] for d in p["documents"]}
    run_pkg = {r["runId"]: r["packageId"] for r in world["runSummaries"]}
    packages = set(doc_pkg.values())
    shards: dict[str, dict[str, Any]] = {pkg: {"packageId": pkg} for pkg in packages}

    skipped: dict[str, int] = {}

    def has(pkg: str, key: str) -> bool:  # a package with no build in this world: skip and count, never fail
        if pkg in shards:
            return True
        skipped[key] = skipped.get(key, 0) + 1
        return False

    def put(pkg: str | None, key: str, k: str, v: Any) -> None:
        if pkg is None:
            raise SystemExit(f"mock world: {key}[{k!r}] belongs to no package")
        if has(pkg, key):
            shards[pkg].setdefault(key, {})[k] = v

    def unit_pkg(k: str) -> str | None:
        return doc_pkg.get(k.split(":", 1)[0])

    def key_pkg(k: str) -> str | None:  # an S3 key: a package id segment, or a run record's package
        hit = next((seg for seg in k.split("/") if seg in packages), None)
        return hit or run_pkg.get(k.removeprefix("runs/").removesuffix(".json"))

    for key in SHARDED_BY_DOC:
        for k, v in world.pop(key).items():
            put(doc_pkg.get(k), key, k, v)
    for k, v in world.pop("overlays").items():
        put(doc_pkg.get(k.split("|", 1)[0]), "overlays", k, v)
    for key in SHARDED_BY_UNIT:
        for k, v in world.pop(key).items():
            put(unit_pkg(k), key, k, v)
    for key in SHARDED_BY_RUN:
        for k, v in world.pop(key).items():
            put(run_pkg.get(k.split("|", 1)[0]), key, k, v)
    for key in SHARDED_BY_PACKAGE:
        for k, v in world.pop(key).items():
            if has(k, key):
                shards[k][key] = v
    core_artifacts = {}
    for k, v in world.pop("artifacts").items():
        pkg = key_pkg(k)
        if pkg:
            put(pkg, "artifacts", k, v)
        else:
            core_artifacts[k] = v  # config/… keys every package shares
    world["artifacts"] = core_artifacts
    for k, v in world.pop("showcasePayloads").items():
        put(k.split("|")[1], "showcasePayloads", k, v)
    for k, v in world.pop("askReplays").items():
        put(k.split("|", 1)[0], "askReplays", k, v)
    for r in world.pop("review"):
        if has(r["packageId"], "review"):
            shards[r["packageId"]].setdefault("review", []).append(r)
    pkg_dir = out / "packages"
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True)
    sizes = {}
    world["shards"] = {}
    for pkg, body in sorted(shards.items()):
        text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        (pkg_dir / f"{pkg}.json").write_text(text)
        world["shards"][pkg] = f"packages/{pkg}.json"
        sizes[pkg] = len(text)
    knowledge = world.pop("ontology")
    (out / "knowledge.json").write_text(json.dumps(knowledge, ensure_ascii=False, separators=(",", ":")))
    world["knowledge"] = "knowledge.json"
    return sizes, skipped


DEFAULT_BUILDS = ("build/e2e", "build/e2e-quarterly", "build/e2e-tradeconf", "build/e2e-lending-02")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build", action="append", default=[],
                    help="a package build with a world snapshot (repeatable; the first is the lending package). "
                         f"Default: {', '.join(DEFAULT_BUILDS)} (those that exist)")
    ap.add_argument("--runs", default="runs/local")
    ap.add_argument("--public-metrics", action="store_true",
                    help="also export the published cost and eval views (dollar amounts, per-lane rates); never for "
                         "a build whose metrics are unpublished")
    args = ap.parse_args()
    global METRIC_STATES
    METRIC_STATES = ("off", "on") if args.public_metrics else ("off",)
    runs_dir = REPO / args.runs
    builds = [REPO / b for b in args.build] or [REPO / b for b in DEFAULT_BUILDS if (REPO / b / "world").is_dir()]
    missing = [b for b in builds if not (b / "world").is_dir()]
    if not builds or missing:
        print(f"{', '.join(map(str, missing)) or 'build/e2e'}: no world snapshot; run `make e2e-local` "
              "(and `make e2e-packages`) first", file=sys.stderr)
        return 2
    world = export(builds, runs_dir, OUT)
    spans = [s for b in builds for s in held_spans(b)]
    check(json.dumps(world, ensure_ascii=False, separators=(",", ":")), spans)
    for fixture in sorted((OUT / "xray").rglob("*.json")):
        check(fixture.read_text(), spans, str(fixture.relative_to(OUT)))
    n_runs, n_previews = split_out(world, OUT)
    n_builds, n_filters, n_paths, n_payloads = (len(world[k]) for k in ("builds", "filterInspections", "graphPaths",
                                                                          "showcasePayloads"))
    n_units, n_regions, n_artifacts = len(world["units"]), len(world["regions"]), len(world["artifacts"])
    shard_sizes, skipped = shard(world, OUT)
    text = json.dumps(world, ensure_ascii=False, separators=(",", ":"))
    (OUT / "world.json").write_text(text)
    cfg = OUT / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    for pv in LANES:
        (cfg / f"v{pv}.yaml").write_text((REPO / "config" / "pipelines" / f"v{pv}.yaml").read_text())
    print(f"world.json {len(text) // 1024} KB (core) · shards " +
          ", ".join(f"{k} {v // 1024} KB" for k, v in shard_sizes.items()) +
          f" · {len(world['packages'])} packages · {n_units} units · {n_regions} regions · {n_artifacts} artifacts "
          f"({n_previews} previews in artifacts/) · {n_runs} runs in runs/ · "
          f"{len(list((OUT / 'pages').glob('*.png')))} page renders · {n_builds} builds · {n_filters} filter "
          f"inspections · {n_paths} graph paths · {n_payloads} showcase payloads")
    if skipped:
        print("skipped (their package has no build here; run `make e2e-packages` for the full world): " +
              ", ".join(f"{n} {k}" for k, n in sorted(skipped.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
