# REST API reference

The REST API (`rp-api`, Amazon API Gateway REST, stage `v1`) serves the web UI. Every route below is wired in the
AWS CDK app ([`infra/lib/api-routes.ts`](../../infra/lib/api-routes.ts)), and the UI client
([`usecases/provenant/ui/src/api/http.ts`](../../usecases/provenant/ui/src/api/http.ts)) calls only routes from
this table. `tests/integration/test_contract_mirror.py` reads this page and fails when the CDK routes, this table
and the UI client disagree.

## Conventions

- **Authentication.** Every route uses the Amazon Cognito authorizer on the `rp-users` user pool with the
  `aws.cognito.signin.user.admin` scope, so it needs the **access token**. An ID token gets HTTP 401.
- **Principals.** The caller's principals are `user:<sub>` plus `group:<name>` for each Cognito group. They come
  from the authorizer's claims, never from the request.
- **Access rules.** *Package ACL*: the package's `allowedPrincipals` intersect the caller's principals.
  *DocAcl*: the document ACL, copied from the package at upload and checked before any region is served.
  *Owner*: the user who created the package.
- **Bodies** are JSON in camelCase. Models live in
  [`packages/evidence-core/evidence_core/models/`](../../packages/evidence-core/evidence_core/models/); the UI's
  TypeScript mirror is [`contracts/api.ts`](../../contracts/api.ts).
- **Errors** are `ApiError {error: {code, message, requestId}}` with status 400, 401, 403, 404, 409,
  503 (`not_ready`) or 500.
- **Path parameters** are URL-encoded by the UI (unit ids contain `:`).
- **Published metrics.** Rates and dollar amounts are returned only when `RP_PUBLIC_METRICS=on`. By default the
  evaluation and cost routes return per-question outcomes and usage counts only.

## Routes

| Method | Path | Returns | Access rule |
|---|---|---|---|
| GET | `/me` | `Me` | Signed in |
| GET | `/packages` | `PackageList` | Packages the caller can read |
| POST | `/packages` | `Package` (201) | Signed in; the groups shared with must be the caller's own |
| GET | `/packages/{packageId}` | `PackageDetail` | Package ACL |
| DELETE | `/packages/{packageId}` | `DeletePackageResponse` (202): deletes the package's S3 prefixes, table rows, index documents and graphs | Owner |
| POST | `/packages/{packageId}/upload-url` | `UploadUrlResponse` (a presigned upload to Bronze) | Owner |
| GET | `/packages/{packageId}/status` | `PackageStatus` (polled by the stage board) | Package ACL |
| GET | `/documents/{docId}` | `DocumentDetail` with every lane's stage records | DocAcl |
| GET | `/documents/{docId}/lanes/{pv}/stages/{stageId}` | `StageDetail` (record, definition, events) | DocAcl |
| GET | `/documents/{docId}/lanes/{pv}/stages/{stageId}/overlay?page=` | `StageOverlay` (boxes the stage saw or produced on one page, no text) | DocAcl |
| GET | `/documents/{docId}/lanes/{pv}/events?limit=&cursor=&stageId=` | `StageEventPage` | DocAcl |
| GET | `/documents/{docId}/lanes/{pv}/units` | `UnitList` | DocAcl |
| POST | `/documents/{docId}/reprocess` | `ReprocessResponse` (202) | Owner |
| GET | `/units/{unitId}` | `UnitView` (a held unit's text is redacted) | DocAcl |
| GET | `/units/{unitId}/lineage?field=` | `Lineage`: figure → field → unit → blocks → region → page → upload → index → graph → pipeline | DocAcl |
| GET | `/region/{unitId}?field=` | `RegionResult` with a presigned page-image URL (300 s) | DocAcl, else 403; held units 404 |
| GET | `/artifacts?key=` | `ArtifactView` (preview of at most 64 KB; long digit runs masked) | The key's package ACL; never presigns Silver |
| GET | `/pipelines` | `PipelineList` | Signed in |
| GET | `/pipelines/{pv}` | `PipelineDetail` (YAML, resolved config, stages, prompt) | Signed in |
| GET | `/pipelines/diff?a=1.0&b=2.3.1` | `ConfigDiff` | Signed in |
| POST | `/ask` | A `text/event-stream` of ask events | Package ACL (the tools check again) |
| GET | `/runs?packageId=&limit=&cursor=` | `RunPage` (newest first) | Package ACL |
| GET | `/runs/{runId}` | `RunRecord` | Package ACL |
| GET | `/review?packageId=` | `ReviewList` (fields below the confidence threshold) | Package ACL; items by DocAcl |
| GET | `/documents/{docId}/lanes/{pv}/xray?page=&size=&runId=` | `XrayPage`: what the `1.0` lane's chunks covered on one page (inspection only, never an extraction) | DocAcl; tokens inside held regions are dropped |
| GET | `/documents/{docId}/lanes/{pv}/build` | `BuildReplay`: the evidence-unit build, member by member, from the build report | DocAcl; 404 for the `1.0` lane |
| GET | `/inspect/filters?runId=&callId=` | `FilterInspection`: counts for a logged `search_units` filter (counts only) | Package ACL of the run |
| GET | `/runs/{runId}/calls/{callId}/path` | `GraphNeighbourhood` from the rows a logged `graph_hop` call bound | Package ACL of the run |
| GET | `/showcases` | `ShowcaseCatalog` | Signed in |
| GET | `/showcases/{showcaseId}/payload?packageId=&…` | `ShowcasePayload` for the showcases composed on the server | Package ACL, checked before any read |
| GET | `/access/decisions?runId=\|since=` | `AccessDecisionPage`: one row per gateway tool call (argument names only, never values) | `runId`: package ACL of the run; `since`: the caller's own rows |
| GET | `/governance/definitions/{name}` | `GovernedDefinitionView`: the record `make ontology-check` writes, including the optional COA snapshot | Signed in |
| POST | `/review/{packageId}/items/{reviewKey}/decision` | `ReviewItem` (ACCEPT, CORRECT or REJECT; 409 when the item changed since the reviewer saw it) | Package ACL and the item's DocAcl |
| GET | `/eval/runs` | `EvalRunList` | Only results whose packages the caller can all read |
| GET | `/eval/runs/{evalRunId}` | `EvalRunView` (per-question outcomes; rates only when published) | The caller can read every package the result covers |
| GET | `/cost?packageId=&groupBy=docType\|stage\|lane\|service&lane=` | `CostView` (usage; dollar amounts only when published) | Package ACL; documents by DocAcl |
| GET | `/ontology/classes?module=` | `OntologyTermList` | Signed in |
| GET | `/ontology/terms/{iri}` | `OntologyTerm` (labels with their triples, hierarchy, usage counts) | Signed in; usage counts over the caller's packages only |
| GET | `/graph/neighbourhood?iri=&depth=1\|2&packageId=&pv=` | `GraphNeighbourhood` from Amazon Neptune (an API-only template, at most 150 nodes) | Package ACL |

## The ask stream

`POST /ask` takes an `AskRequest` (question, package, pipeline version, optional session) and streams events:
the tool calls as they happen, then the answer with its claims and citations, then the run record id. The answer
payload, the event types and `RunRecord` are defined in `models/answer.py`. See
[the ask path](../architecture.md#the-ask-path) for how the request reaches the AgentCore Runtime.
