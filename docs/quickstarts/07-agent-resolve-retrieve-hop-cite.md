# Quickstart 7: the agent: resolve, retrieve, hop, assemble, cite (pattern 5)

**Pattern:** an agent with a few narrow tools answers in five moves. It **resolves** what the question means
(which definition, which concepts), **retrieves** whole evidence units with filters inside the query, **hops** the
knowledge graph with an approved template, **assembles** the answer from tool results, and **cites** every figure
to a field and a page region. A validator removes any claim the tools did not return.

| | |
|---|---|
| **Plane** | Agent |
| **Time** | 20 minutes to read the logged runs; a few more to make your own |
| **Needs** | [Quickstart 1](01-run-locally.md) done. For new runs: AWS credentials with Amazon Bedrock access in `us-east-1` |
| **Cost** | Reading logged runs: none. New local runs: about USD 0.04 per question per lane with Anthropic Claude Sonnet 4.6 (our logged runs, list price) |

Why this pattern exists and how to adopt it without the rest of the pipeline: [methodology page](../methodology/05-resolve-retrieve-hop-assemble-cite.md).

## The problem

A capable model with a vector store will often produce the right number. The question is whether anyone can
check it. If the answer cites a 300-token chunk, a reviewer still has to find the figure on the page and hope it
is the one the model used. If the model added a number that no tool returned, nobody notices.

## The pattern

| Move | Tool or component | What it guarantees |
|---|---|---|
| Resolve | The answer prompt and the governed definition in the ontology | "Total income" means qualifying income, and the agent knows its operand concept |
| Retrieve | `search_units` | Whole units with fields and boxes, filtered by the caller's identity inside the query ([pattern 4](06-metadata-and-filtered-hybrid-search.md)) |
| Hop | `graph_hop` | An approved SPARQL template over the package's graph; totals are computed by the tool, not the model ([pattern 3](05-ontology-binding-and-sparql.md)) |
| Assemble | The agent (Strands on Amazon Bedrock) | Writes the answer as claims: `{text, value, unitId, fieldId}`, plus any arithmetic |
| Cite | Citation validator, `get_region` | Keeps a claim only if its unit and field came back from a tool in this session and its value matches; attaches the page and box. Any figure left without a kept claim is "uncited", and the agent gets one retry |

Two more tools complete the set: `get_region` returns the region of a unit the agent has not seen yet, and
`doc_status` says whether a document is ready.

Identity never comes from the model. On AWS, an Amazon Bedrock AgentCore Gateway **REQUEST interceptor**
re-validates the caller's token and overwrites `callerSub`, `groups`, `pipelineVersion`, `packageId` and `runId`
in every tool call. Locally, the gateway stand-in does the same.

The agent, prompt, model, retriever and K are identical for both pipeline versions. Only the index and graph the
tools read differ. Limits are config (`agent.maxToolCalls: 8`, `agent.citationRetry: 1`).

## See it: a logged run, no cost

The repository includes agent runs logged with Amazon Bedrock over the local builds (`runs/local/`). Each one is a
full run record. Look at the headline question on both lanes:

```bash
python3 - <<'EOF'
import json
for run_id, lane in (("r-99bf456c5a2c", "2.3.1"), ("r-ae7d90b38217", "1.0")):
    r = json.load(open(f"runs/local/{run_id}.json"))
    print(f"\n== {lane}: {r['question']}  (model {r['modelId']})")
    for c in r["toolCalls"]:
        print(f"  tool {c['tool']:<13} {json.dumps(c['input'])}  {c['ms']} ms")
    a = r["answer"]
    print("  answer:", a["answer"][:160], "…")
    print("  arithmetic:", a.get("arithmetic"))
    for cl in a.get("claims", []):
        where = f"p{cl['page']} bbox {cl['bbox']}" if cl.get("resolvable") else "no region"
        print(f"  claim {cl['value']:>10}  {cl.get('unitId')}#{cl.get('fieldId')}  {where}")
    print("  uncited numbers:", a.get("uncitedNumbers"), "| golden check:", r["check"])
EOF
```

On the evidence-unit lane (2.3.1) the agent calls `search_units` for W-2 units, then `graph_hop` with
`qi_sum_w2_box1_v1`. The answer is 169,650.00 = 98,400.00 + 71,250.00, and each claim resolves to
`f-box1` on page 1 of each W-2, with its box.

On the naive lane (1.0), `graph_hop` returns no rows because that lane has no graph, so the agent searches
twice more. It reaches the same number in these runs, but its claims point to whole chunks (`…:1.0:c-01`) with no
field and no page region, so no citation can be opened. That is the difference this project is about: not whether a
model can find a number, but whether you can prove where it came from.

Summarize all logged runs, offline:

```bash
make local-ask-summary
```

This prints the N-of-N table per question and lane (how many runs were correct, how many had valid citations,
which totals appeared), the lane parity check (both lanes ran with the same prompt, model and tool specs) and the
total model spend.

In the UI (mock mode), **Workbench → Ask** replays these runs, and **Showcases → Resolve, retrieve, hop, cite**
plays one run to scale with every tool call at its logged time.

## Make your own runs locally (Amazon Bedrock)

The local harness runs the same agent runner, prompt, model and tool code against your local build. The tools'
services are local: the world snapshot in moto, an in-memory index re-embedded with Amazon Titan Text Embeddings V2 (recorded), and
pyoxigraph for the graph. The model call is real.

```bash
export AWS_REGION=us-east-1           # and AWS_PROFILE if you use one
make local-ask QID=q-lend-02 REPEAT=2 BUDGET=0.5
```

- `QID` is a golden question id from `eval/golden.jsonl`. `REPEAT` runs it that many times on each lane.
- `BUDGET` (USD) stops the batch before its own model spend would pass it, at list price.
- `BUILD=build/e2e-quarterly` asks against another package build (run `make e2e-packages` first).
- Runs are appended to `runs/local/` (a run record, its event stream, and `index.json`). These files are tracked
  in git, so commit them only if you mean to.

For more options, such as asking as user B (another group, to see access denied) or using the compact or frontier
model profile:

```bash
PYTHONPATH=services .venv/bin/python -m local_agent ask --help
```

## On AWS

After [Quickstart 2](02-deploy-to-aws.md):

- **AgentCore Runtime** hosts the Strands agent (`services/agent/main.py`). The deploy script uploads a code zip
  with arm64 wheels, so no Docker or CodeBuild is needed. The Runtime validates the caller's Amazon Cognito access
  token with a custom JWT authorizer.
- **AgentCore Gateway** exposes the four tools as MCP tools, with the AWS Lambda functions as targets and the
  REQUEST interceptor in front.
- **Ask** from the UI, or `scripts/smoke.sh --ask`, or the evaluation harness in `ask` mode. Every run writes
  `runs/{runId}.json` to the data bucket and a summary to the `rp-runs` table.

Redeploy only the agent plane after a change:

```bash
make deploy-agent          # scripts/deploy_agentcore.sh
scripts/deploy_agentcore.sh --help
```

By default, `POST /ask` goes through an API Gateway Lambda integration, which has a 29-second timeout. To stream
directly from the Runtime instead, deploy `rp-agent` with the Runtime ARN in context:

```bash
cd infra
npx cdk deploy rp-agent -c rp:agentRuntimeArn="$(aws ssm get-parameter --name /rp/agent/runtime-arn --query Parameter.Value --output text)"
```

## Read the code

| What | Where |
|---|---|
| The answer prompt (shared by both lanes) | `prompts/answer_v1.txt` |
| One ask, end to end: events, tools, retry, run record | `services/agent/rp_agent/runner.py` |
| Citation validator | `services/agent/rp_agent/citations.py` |
| Figures and arithmetic checks | `services/agent/rp_agent/numbers.py` |
| What each tool returned in this session | `services/agent/rp_agent/ledger.py` |
| AgentCore Runtime entry point | `services/agent/main.py` |
| AgentCore deploy (Gateway, targets, interceptor, Runtime) | `services/agent/deploy/deploy.py`, `scripts/deploy_agentcore.sh` |
| The tools | `services/lambdas/tools/{search_units,graph_hop,get_region,doc_status}/` |
| Gateway REQUEST interceptor and token verification | `services/lambdas/interceptor/handler.py`, `tokens.py` |
| Local gateway stand-in and harness | `services/local_agent/gateway.py`, `harness.py` |
| Tool schemas (MCP), generated from the models | `contracts/generated/mcp_tools.json` (`make contracts`) |

## Prove it

```bash
.venv/bin/python -m pytest services/agent/tests services/lambdas/tools/tests \
  services/lambdas/interceptor/tests services/lambdas/api/tests/test_api_units_lineage.py
```

## Reuse it in your application

- **Make the model cite structured ids,** not quotes. A claim of `{unitId, fieldId, value}` can be checked
  mechanically; a quoted sentence cannot.
- **Validate after generation.** Copy `citations.py`, `numbers.py` and `ledger.py`. They depend only on the tool
  result shapes. Drop any claim the tools did not return, and retry once with the list of uncited figures.
- **Let tools do arithmetic that matters.** A governed total belongs in a template, with its operands returned
  as rows.
- **Inject identity at the gateway.** The interceptor pattern works for any MCP tool behind AgentCore Gateway: the
  model never sees or sets who the caller is.
- **Log a full run record.** Model id, prompt hash, config hash, corpus hash, every tool call with input, output and
  timing. It is what makes N-of-N evaluation and audits possible.

## Next

[Quickstart 8: the PII gate](08-pii-gate.md)
