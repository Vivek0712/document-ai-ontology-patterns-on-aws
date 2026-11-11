# Quickstart 6: metadata at ingestion, filters inside the query (pattern 4)

**Pattern:** every unit carries its context as metadata from the moment it is built: package, pipeline version,
document type, unit type, field concepts, period, PII flag and the principals allowed to read it. Retrieval puts
the filter **inside** both the vector query and the keyword query, never after them.

| | |
|---|---|
| **Plane** | Knowledge → agent |
| **Time** | 15 minutes |
| **Needs** | `make venv` from [Quickstart 1](01-run-locally.md) |
| **Cost** | None locally. On AWS: Amazon OpenSearch Serverless and Amazon Titan Text Embeddings V2 |

Why this pattern exists and how to adopt it without the rest of the pipeline: [methodology page](../methodology/04-metadata-and-filters-inside-the-query.md).

## The problem

Two common shortcuts break enterprise search:

- **Metadata added late, or never.** If a chunk does not know which package, borrower, period or document type it
  belongs to, a query for "Maya's 2025 wages" can match another borrower's payslip or another year's W-2.
- **Filtering after retrieval.** A post-filter takes the top *k* results and then drops the ones the caller may not
  see. The caller can get fewer results than asked for, or none, even when matching documents exist. Worse, the
  ranking was computed over documents the caller must not see. Access control has to be part of the query.

## The pattern

1. **Set metadata at ingestion.** The unit builder and the classifier set `docType`, `unitType`,
   `fieldConcepts` and `period`. The PII gate sets `piiFlag`. The package's access list (and the document's) is
   copied onto every unit as `allowedPrincipals` (`user:<sub>` and `group:<name>` entries).
2. **Index it as keywords.** The OpenSearch mapping stores these as `keyword` fields next to the text and the
   embedding (`index_mapping()` in `retrieval.py`).
3. **Build one filter from the caller's identity.** `build_filter()` takes the principals, the package and the
   pipeline version, plus optional document and unit types. It always excludes held PII units.
4. **Put that filter inside both sub-queries.** The k-NN query uses OpenSearch efficient filtering (the filter is
   inside the `knn` clause). The BM25 query has it as a `bool` filter.
5. **Fuse.** Take the top 20 of each, min-max normalize each list, then score
   `0.7 × dense + 0.3 × BM25` (a unit missing from one list scores 0 there). Ties break by unit id. Return K = 5.
6. **Never trust identity from the model.** The principals come from the verified token, injected by the gateway
   interceptor ([pattern 5](07-agent-resolve-retrieve-hop-cite.md)). Any identity value the model supplies is
   overwritten.

Fusion weights, K and the filter fields are config, in the `retrieval` block of each pipeline version:

```yaml
retrieval:
  embedModel: ${EMBED_MODEL}
  embedDimensions: 1024
  fusion: { dense: 0.7, bm25: 0.3, normalization: min_max, subQueryK: 20 }
  k: 5
  filtersInsideQuery: [allowedPrincipals, packageId, pipelineVersion]
```

## See it

Print the filter and the two queries the `search_units` tool sends:

```bash
.venv/bin/python - <<'EOF'
import json
from evidence_core.retrieval import Hit, bm25_query, build_filter, dense_query, fuse

flt = build_filter(principals=["user:demo-user-a", "group:lending-analyst"],
                   package_id="pkg-lending-01", pipeline_version="2.3.1")
print(json.dumps(flt, indent=1))

bm25 = bm25_query("total income", flt, 20)
dense = dense_query([0.0] * 1024, flt, 20)          # a real query embeds the question with Titan V2
print("BM25 filter is the same object: ", bm25["query"]["bool"]["filter"] == [flt])
print("k-NN filter is the same object: ", dense["query"]["knn"]["embedding"]["filter"] == flt)

# Fusion on toy scores: min-max inside each list, then 0.7 dense + 0.3 BM25
for h in fuse([Hit("u1", 0.82, {}), Hit("u2", 0.80, {}), Hit("u3", 0.40, {})],
              [Hit("u2", 7.1, {}), Hit("u3", 2.0, {})], k=3):
    print(h.unit_id, round(h.dense_norm, 3), round(h.bm25_norm, 3), round(h.fused, 3))
EOF
```

The output shows the same filter object in both queries: the principals, the package, the pipeline version, and
`must_not piiFlag = held`. In the fusion example, `u2` wins with 0.967 because it ranks high in both lists.

Look at the metadata on real units after `make e2e-local PII=local`:

```bash
python3 - <<'EOF'
import json
keys = ["unitId", "docType", "unitType", "fieldConcepts", "period", "piiFlag", "allowedPrincipals"]
for line in open("build/e2e/v2.3.1/w2-2025-maya-fictus-827d37.units.jsonl"):
    u = json.loads(line)
    print({k: u.get(k) for k in keys})
EOF
```

In the UI (mock mode), **Showcases → Filters inside the query** takes one logged agent run and shows, for that
exact tool call, the unit metadata with the stage that set each field, the query with the filter inside both
bodies, the fusion, and a funnel of how many units the filter admitted.

## Read the code

| What | Where |
|---|---|
| Index mapping, `build_filter`, `dense_query`, `bm25_query`, `min_max`, `fuse` | `packages/evidence-core/evidence_core/retrieval.py` |
| The `search_units` tool (embeds the question, runs both queries, fuses, returns units with boxes and fields) | `services/lambdas/tools/search_units/handler.py` |
| Indexing Gold units (embedding text, index document, delete-before-rewrite) | `services/lambdas/embed_index/` |
| OpenSearch Serverless collection and policies | `infra/lib/knowledge-stack.ts`, `infra/lib/aoss.ts` |

## Prove it

```bash
.venv/bin/python -m pytest packages/evidence-core/tests/test_models_retrieval.py services/lambdas/tools/tests/test_search_units.py
```

## On AWS

Each pipeline version has its own index (`units-v1-0`, `units-v2-3-1`) in an Amazon OpenSearch Serverless vector
collection. The `search_units` function embeds the question with Amazon Titan Text Embeddings V2 (1,024
dimensions), sends both sub-queries and fuses them in the function. The data access policy lets the indexing
function write and the search function read.

Two things worth knowing about OpenSearch Serverless:

- New documents become searchable after a refresh interval, so the `ready` stage waits until the indexed units
  are searchable before it marks a lane ready (`retrieval.readiness`).
- There is no `_delete_by_query`. To re-index a document, the pipeline records the `_id` of every document it
  wrote and deletes those first.

See [Filtering with k-NN search](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/knn.html)
in the Amazon OpenSearch Service documentation.

## Reuse it in your application

- **Decide your filter fields first,** then make sure ingestion sets every one of them on every unit. A unit
  without `allowedPrincipals` should not be indexed at all.
- **Copy `build_filter` as the single place** that turns an identity into a filter. Test that both sub-queries
  carry the same object.
- **Keep PII out twice.** Held units are never indexed, and the filter excludes `piiFlag = held` anyway.
- **Hybrid is worth it for documents.** Exact identifiers (box numbers, security ids, invoice numbers) are
  keyword matches; paraphrased questions are vector matches.

## Next

[Quickstart 7: the agent: resolve, retrieve, hop, assemble, cite](07-agent-resolve-retrieve-hop-cite.md)
