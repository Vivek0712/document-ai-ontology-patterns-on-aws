# Pattern 4 · Metadata at ingestion, filters inside the query

**Rule:** write access, scope and type metadata onto every unit when you ingest it, and put the filter inside
every retrieval sub-query. Never filter after ranking.

Planes: knowledge → agent. Previous: [pattern 3](03-ontology-driven-classification.md). Next: [pattern 5](05-resolve-retrieve-hop-assemble-cite.md).

---

## The problem

Access control and scoping are often added at the end. The system retrieves the top *k* results, then drops the
ones the user may not see, or the ones from the wrong document set. This goes wrong in three ways:

- **Recall collapses.** With approximate k-NN, the top *k* are chosen before the filter runs. If most of them
  belong to someone else, the user gets one or zero results, even though relevant units they *can* read exist
  further down.
- **Information leaks.** Scores, counts, timing and "no results" responses can reveal that restricted documents
  exist and match the query.
- **Scopes blur.** Without a package or version filter, an answer can mix figures from two packages or from two
  pipeline versions of the same document.

If the metadata is not on the unit when it is indexed, you cannot filter inside the query later. You can only
post-filter.

## The principle

1. **Decide metadata at ingestion.** Every unit carries `allowedPrincipals` (users and groups), `packageId`,
   `pipelineVersion`, `docType`, `fieldConcepts`, `period`, `piiFlag` and `configSha` before it reaches the
   index.
2. **Filter inside every sub-query.** In hybrid search, the same filter goes inside the vector query and inside
   the keyword query. Then each list's top *k* contains only units the caller may read, in the requested scope.
3. **Identity is never a model argument.** The caller's identity comes from a validated token, outside the
   model's reach. The model can choose a query. It cannot choose whose documents it searches.
4. **The same rule applies to every store.** The graph query includes a package's graph only when the caller can
   read the package. The region endpoint checks the document ACL before it signs a URL.

## How it is implemented here

| Piece | File |
|---|---|
| Index mapping (keyword fields for every filter, `searchText` for BM25, a 1024-dimension HNSW vector) | `index_mapping()` in [`packages/evidence-core/evidence_core/retrieval.py`](../../packages/evidence-core/evidence_core/retrieval.py) |
| The filter, and the two sub-queries that both carry it | `build_filter()`, `dense_query()`, `bm25_query()` in the same file |
| Fusion: min-max within each list, then 0.7 · dense + 0.3 · BM25, ties broken by unit ID | `fuse()` in the same file |
| Principals: `user:<sub>` plus `group:<name>` | `principals_for()` in [`packages/evidence-core/evidence_core/ids.py`](../../packages/evidence-core/evidence_core/ids.py) |
| Metadata written onto units: ACL copied from the package and the document ACL | [`services/lambdas/upload_url/handler.py`](../../services/lambdas/upload_url/handler.py), [`services/lambdas/register/handler.py`](../../services/lambdas/register/handler.py), [`services/lambdas/eu_builder/builder.py`](../../services/lambdas/eu_builder/builder.py) |
| Indexing: one index per pipeline version (`units-v1-0`, `units-v2-3-1`); held units are never indexed | [`services/lambdas/embed_index/handler.py`](../../services/lambdas/embed_index/handler.py) |
| The search tool | [`services/lambdas/tools/search_units/handler.py`](../../services/lambdas/tools/search_units/handler.py) |
| Identity injection: the interceptor overwrites `callerSub`, `groups`, `pipelineVersion`, `packageId` and `runId` on every tool call | [`services/lambdas/interceptor/handler.py`](../../services/lambdas/interceptor/handler.py) |
| Tool wrapper: splits model arguments from injected context; model values for injected keys never reach the tool | [`services/lambdas/tools/_lib/runtime.py`](../../services/lambdas/tools/_lib/runtime.py) |
| Graph scope: the package's ABox graph is added to `FROM` only if the caller can read the package | [`services/lambdas/tools/graph_hop/handler.py`](../../services/lambdas/tools/graph_hop/handler.py) |
| Region scope: the document ACL is checked before a page image is presigned | [`services/lambdas/tools/_lib/region.py`](../../services/lambdas/tools/_lib/region.py), [`services/lambdas/get_region_http/handler.py`](../../services/lambdas/get_region_http/handler.py) |
| Retrieval settings, identical in both pipeline versions | `retrieval` in [`config/pipelines/v2.3.1.yaml`](../../config/pipelines/v2.3.1.yaml): `fusion`, `k: 5`, `filtersInsideQuery: [allowedPrincipals, packageId, pipelineVersion]` |

The filter that both sub-queries carry looks like this:

```json
{"bool": {
  "filter": [
    {"terms": {"allowedPrincipals": ["user:<sub>", "group:lending-analyst"]}},
    {"term":  {"packageId": "pkg-lending-01"}},
    {"term":  {"pipelineVersion": "2.3.1"}}
  ],
  "must_not": [{"term": {"piiFlag": "held"}}]
}}
```

In the logged local runs, a user from the `markets-analyst` group asked the lending question about a package
shared only with `lending-analyst`. In all 4 runs (2 per pipeline version), the tools returned `forbidden`, and
the agent answered that it could not determine the figure from this package.

## Adopt it in your own pipeline

With Amazon OpenSearch Serverless or Amazon OpenSearch Service, put the filter inside the `knn` clause (efficient
filtering) and inside the BM25 `bool` query. You can import the helpers from this repository or copy them; they
have no AWS dependencies.

```python
from evidence_core.retrieval import build_filter, dense_query, bm25_query, fuse, Hit

def search_units(os_client, index, query_text, query_vector, caller_sub, caller_groups, package_id, pv, k=5):
    principals = [f"user:{caller_sub}"] + [f"group:{g}" for g in sorted(set(caller_groups))]
    flt = build_filter(principals=principals, package_id=package_id, pipeline_version=pv)

    dense = os_client.search(index=index, body=dense_query(query_vector, flt, k=20))
    bm25 = os_client.search(index=index, body=bm25_query(query_text, flt, k=20))

    to_hits = lambda r: [Hit(h["_source"]["unitId"], h["_score"], h["_source"]) for h in r["hits"]["hits"]]
    return fuse(to_hits(dense), to_hits(bm25), w_dense=0.7, w_bm25=0.3, k=k)
```

Three things make this safe:

- `caller_sub` and `caller_groups` come from a **validated token**, never from the model's tool arguments. With
  Amazon Bedrock AgentCore Gateway, a request interceptor can overwrite those arguments on every `tools/call`.
- Every unit was indexed **with** `allowedPrincipals`, `packageId` and `pipelineVersion`. Write them at
  ingestion, and copy the ACL from the document onto each unit.
- An empty principal list raises an error. It never means "no filter".

Do the same for other stores: add a graph to the SPARQL `FROM` clause only after an ACL check, and check the
document ACL before you presign an object URL.

## Pitfalls

- **`post_filter` with k-NN.** It looks like a filter but runs after the nearest neighbors were chosen. Use the
  filter inside the `knn` clause.
- **Filtering only one side of hybrid search.** If the BM25 query is filtered and the vector query is not, the
  fused list still contains forbidden units.
- **Normalizing scores across the filtered and unfiltered sets.** Normalize within each filtered list.
- **Letting the model pass `packageId` or `groups`.** Treat any identity or scope field in tool arguments as
  untrusted, and overwrite it.
- **ACLs that change after indexing.** If a document's ACL changes, re-index its units. The unit carries a copy.
- **Metadata you did not index as keyword fields.** A filter on an analyzed `text` field does not do exact
  matching.

## How to evaluate it

- **Access tests.** For each tool, call it as a user without access and assert `forbidden` or empty results. In
  this repository, `scripts/smoke.sh` checks that the second demo user gets 403 from the region endpoint, and that
  an ID token (instead of an access token) gets 401.
- **Recall under filtering.** Measure Recall@1 for a user who can read a small share of the corpus. Post-filtering
  shows up as a drop. Filtering inside the query should not.
- **Scope purity.** Assert that every retrieved unit has the requested `packageId` and `pipelineVersion`.
- **Injection tests.** Send tool calls where the model supplies `callerSub`, `groups` or `packageId`, and assert
  that the interceptor replaced them.

## Try it

```bash
.venv/bin/python -m pytest packages/evidence-core/tests/test_models_retrieval.py \
                           services/lambdas/tools/tests/test_search_units.py \
                           services/lambdas/interceptor/tests/
```

`test_filter_inside_both_subqueries` and `test_injects_identity_and_strips_model_values` are the two tests to
read first. For a guided, hands-on walk through this pattern, see
[Quickstart 6: metadata and filtered hybrid search](../quickstarts/06-metadata-and-filtered-hybrid-search.md).
