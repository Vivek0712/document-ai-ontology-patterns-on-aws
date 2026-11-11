# Pattern 5 · Resolve → retrieve → hop → assemble → cite

**Rule:** the agent resolves what the question means, retrieves units, hops the graph for typed facts, then
assembles an answer in which every figure cites a region that a tool returned in this session.

Plane: agent. Previous: [pattern 4](04-metadata-and-filters-inside-the-query.md). See also: [the provenance contract](provenance-contract.md).

---

## The problem

A typical RAG agent retrieves some text, writes an answer and adds citations. That fails in quiet ways:

- **Definitions drift.** "Total income" means one thing to the business and another to the model. The answer
  changes when the prompt or model changes.
- **Arithmetic happens in the model.** The model adds numbers it read from text, and nothing checks the operands.
- **Citations are decoration.** The model writes "(source: W-2, page 1)". Nothing checks that the figure came
  from that page, or that a tool returned that page at all.
- **Chunks cannot be opened.** Even a correct citation to "chunk c-01" does not show a person where on the page
  the number is.

In the logged local runs on the first lending package, both pipeline versions returned 169,650.00 in 10 of 10
runs. Only the evidence-unit lane could prove it. On the control lane, every claim was marked
`"resolvable": false` with the reason *"chunk has no page region"*.

## The principle

Split the work into five steps, and keep the trust boundary explicit:

| Step | What happens | Who is trusted |
|---|---|---|
| **Resolve** | Pin what the question means and where it applies: the governed definition (from the ontology), the package and the pipeline version (from the session, not the model) | Ontology, interceptor |
| **Retrieve** | Hybrid search over evidence units, with the caller's filter inside every sub-query ([pattern 4](04-metadata-and-filters-inside-the-query.md)) | `search_units` |
| **Hop** | For defined quantities and typed facts, run an **approved, parameterized** graph query. The tool computes totals as decimals and returns each operand's unit and field | `graph_hop` |
| **Assemble** | The model writes a short answer and a structured payload: total, arithmetic and one claim per figure (`unitId`, `fieldId`, value) | The model, constrained |
| **Cite** | A validator outside the model keeps only claims that a tool returned in this session, and attaches page and bounding box **from the tool results**. Unaccounted figures trigger one retry, then are reported | Citation validator |

The model chooses what to search for and how to phrase the answer. It never supplies identity, scope, page
numbers or boxes, and it never has the last word on which figures are supported.

## How it is implemented here

| Piece | File |
|---|---|
| Agent entry point on Amazon Bedrock AgentCore Runtime (re-verifies the token, checks the package ACL, streams events) | [`services/agent/main.py`](../../services/agent/main.py) |
| One ask, end to end: `run_start → (tool_start → tool_end)* → text* → answer → check? → run` | [`services/agent/rp_agent/runner.py`](../../services/agent/rp_agent/runner.py) |
| Strands agent with an MCP client to the AgentCore Gateway | [`services/agent/rp_agent/driver.py`](../../services/agent/rp_agent/driver.py) |
| Tool ledger: everything the tools returned in this session, the only source of citation detail | [`services/agent/rp_agent/ledger.py`](../../services/agent/rp_agent/ledger.py) |
| Citation validator | [`services/agent/rp_agent/citations.py`](../../services/agent/rp_agent/citations.py) |
| Finding figures in text and checking arithmetic lines | [`services/agent/rp_agent/numbers.py`](../../services/agent/rp_agent/numbers.py) |
| Answer prompt, shared by both pipeline versions (definition, how to work, output JSON) | [`prompts/answer_v1.txt`](../../prompts/answer_v1.txt) |
| `graph_hop`: approved templates only, parameters bound as escaped literals, decimal totals with operands | [`services/lambdas/tools/graph_hop/handler.py`](../../services/lambdas/tools/graph_hop/handler.py), [`services/lambdas/tools/_lib/templates.py`](../../services/lambdas/tools/_lib/templates.py) |
| SPARQL templates: `qi_sum_w2_box1_v1`, `field_lookup_v1`, `concept_by_label_v1`, `unit_lineage_v1`, `coupon_terms_v1` | [`ontology/templates/`](../../ontology/templates/) |
| `get_region`: page and box for a unit or field, scoped to the injected package and version | [`services/lambdas/tools/get_region/handler.py`](../../services/lambdas/tools/get_region/handler.py) |
| Lineage: field → unit → engine blocks → page region → page image → source object | [`services/lambdas/api/lineage.py`](../../services/lambdas/api/lineage.py) |
| Answer, claim, event and run-record models | [`packages/evidence-core/evidence_core/models/answer.py`](../../packages/evidence-core/evidence_core/models/answer.py) |
| Agent settings, as data: `maxToolCalls: 8`, `citationRetry: 1`, temperature 0 | `agent` and `generation` in [`config/pipelines/v2.3.1.yaml`](../../config/pipelines/v2.3.1.yaml) |

### What the validator does

The model must reply with one JSON object:

```json
{
  "answer": "Total qualifying income for this package is $169,650.00 for tax year 2025 ...",
  "total": "169,650.00",
  "currency": "USD",
  "arithmetic": "98,400.00 + 71,250.00 = 169,650.00",
  "claims": [
    {"text": "$98,400.00", "value": "98400.00", "unitId": "w2-2025-maya-fictus-827d37:2.3.1:eu-02", "fieldId": "f-box1"},
    {"text": "$71,250.00", "value": "71250.00", "unitId": "w2-2025-arjun-fictus-546450:2.3.1:eu-02", "fieldId": "f-box1"}
  ],
  "caveats": ["Payslips and bank deposits corroborate only and are not added."]
}
```

Then the validator:

1. Keeps a claim only if its `unitId` (and `fieldId`) appeared in a tool result **in this session**, and its value
   matches what the tool returned for that field.
2. Attaches `docId`, `fileName`, `page`, `bbox` (the field's box, else the unit's), `unitBbox` and `label` from
   the tool ledger. It never reads them from model text.
3. Accounts for every figure in `answer`, `arithmetic` and `total`. A figure is accounted for when it is a kept
   claim's value, the result of an arithmetic line whose operands are all kept claims, or a `graph_hop` total
   whose operands are all kept claims.
4. If figures are left over, asks the model once more (`citationRetry: 1`). Anything still left goes to
   `uncitedNumbers` in the answer, visible to the user.
5. Marks claims on fixed chunks `resolvable: false` with a reason, because a chunk has no region.

## Adopt it in your own pipeline

You can keep your own agent framework. What matters is that tools return structured results with IDs and
geometry, that a ledger records them, and that a validator outside the model has the final say.

```python
import re
from decimal import Decimal

FIGURE = re.compile(r"(?<![\w\-:.,/])\$?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{3,})(?![\w\-:])")

def dec(s: str) -> Decimal:
    return Decimal(re.sub(r"[^\d.\-]", "", s))

class Ledger:
    """Everything tools returned in this session, keyed by (unitId, fieldId)."""
    def __init__(self):
        self.fields = {}       # (unit_id, field_id) -> {"value": Decimal, "page": int, "bbox": {...}, "label": str}
        self.totals = []       # [{"total": Decimal, "operands": {(unit_id, field_id), ...}}] from graph queries

    def record_field(self, unit_id, field_id, value, page, bbox, label):
        self.fields[(unit_id, field_id)] = {"value": dec(value), "page": page, "bbox": bbox, "label": label}

def validate(answer: dict, ledger: Ledger) -> dict:
    kept, dropped = [], []
    for c in answer.get("claims", []):
        seen = ledger.fields.get((c["unitId"], c.get("fieldId")))
        if seen and seen["value"] == dec(c["value"]):
            kept.append({**c, "page": seen["page"], "bbox": seen["bbox"], "label": seen["label"]})  # geometry from the tool
        else:
            dropped.append(c)
    cited = {dec(c["value"]) for c in kept}
    cited |= {t["total"] for t in ledger.totals
              if t["operands"] <= {(c["unitId"], c.get("fieldId")) for c in kept}}
    # The repository's validator also accepts the result of an arithmetic line whose operands are all kept claims.
    text = " ".join(filter(None, [answer.get("answer"), answer.get("arithmetic"), answer.get("total")]))
    uncited = sorted({m for m in FIGURE.findall(text) if dec(m) not in cited})
    return {**answer, "claims": kept, "droppedClaims": dropped, "uncitedNumbers": uncited}
```

Wire it up like this:

- **Tools return IDs and geometry.** A search result carries `unitId`, field IDs, values, page and box. A graph
  result carries the operand field IRIs and a total that the tool computed, not the model.
- **Graph queries are templates.** Register an allow-list of SPARQL templates. Bind parameters as escaped
  literals, and never let the model write SPARQL.
- **Identity is injected.** With Amazon Bedrock AgentCore Gateway, a request interceptor sets the caller and scope
  on every tool call ([pattern 4](04-metadata-and-filters-inside-the-query.md)).
- **If figures are uncited, retry once, then show them.** Do not hide uncited figures. Show them as uncited.
- **Record the run.** Model ID, prompt hash, config hashes, corpus hash, every tool call and its results, and token
  usage. See [the provenance contract](provenance-contract.md).

## Pitfalls

- **Taking page numbers from the model.** Models produce plausible page numbers. Take geometry only from tool
  results.
- **Validating presence, not value.** A claim can cite a real field with the wrong number. Compare values as
  decimals.
- **Letting the model add.** Compute defined quantities in the graph or in code, return operands, and let the
  validator check the arithmetic line.
- **Unbounded tool loops.** Cap tool calls (8 here), and stop with an error event rather than a guess.
- **Treating "correct" as "proven".** Report answer correctness and citation validity separately. They answer
  different questions.
- **Hiding refusals.** "I cannot answer from this package" is a correct outcome when the caller lacks access or the
  data is missing. Count it as such in evaluation.

## How to evaluate it

- **Answer correctness** against a golden set: numeric answers compare normalized totals, and text answers
  match an answer pattern.
- **Citation validity:** at least one claim, every claim resolvable to a region, and no uncited figures after the
  retry. Also record whether a cited box covers the gold box.
- **Per lane and per question type**, never one aggregate number. See [evaluation](evaluation.md).
- **Repeat runs.** Ask the same question several times (`make local-ask QID=q-lend-01 REPEAT=10 BUDGET=0.5`) and
  report N of N, with agent errors counted separately.

## Try it

```bash
.venv/bin/python -m pytest services/lambdas/tools/tests/test_graph_hop.py \
                           services/agent/tests/test_citations.py \
                           services/agent/tests/test_runner.py \
                           services/lambdas/api/tests/test_api_units_lineage.py
```

To ask questions locally against your local build, you need AWS credentials that can call Amazon Bedrock. The
batch stops before its estimated spend passes `BUDGET` (US dollars):

```bash
make local-ask QID=q-lend-01 REPEAT=3 BUDGET=0.5
make local-ask-summary                     # N-of-N table over runs/local, no model calls
```

For a guided, hands-on walk through this pattern, see
[Quickstart 7: the agent](../quickstarts/07-agent-resolve-retrieve-hop-cite.md).
