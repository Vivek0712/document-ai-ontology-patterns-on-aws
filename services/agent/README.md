# agent

The question-answering agent (pattern 5: resolve, retrieve, hop, assemble, cite). A [Strands Agents](https://strandsagents.com/)
agent that runs on Amazon Bedrock AgentCore Runtime and reaches its tools through Amazon Bedrock AgentCore Gateway
as MCP tools. The same runner also runs locally through [`local_agent`](../local_agent/README.md).

## What is in here

| File | Role |
|---|---|
| `main.py` | AgentCore Runtime entry point: `POST /invocations` with an ask request, streamed back as Server-Sent Events. Re-verifies the caller's Amazon Cognito access token, checks the package access and the pipeline version |
| `rp_agent/runner.py` | One ask end to end: `run_start → (tool_start → tool_end)* → text* → answer → check → run`; the citation retry; the run record |
| `rp_agent/driver.py` | Runs a Strands `Agent` (Amazon Bedrock model, temperature 0) with the Gateway's MCP tools, and turns its callbacks and stream events into driver events, so the runner can be tested without Amazon Bedrock |
| `rp_agent/citations.py` | Citation validator: keeps a claim only if its unit and field came back from a tool in this session and the value matches; attaches page and box |
| `rp_agent/numbers.py` | Finds figures in the answer, checks arithmetic lines and `graph_hop` totals against kept claims |
| `rp_agent/ledger.py` | What every tool returned in this session, by unit and field |
| `rp_agent/access.py`, `runs.py`, `settings.py` | Package access check, run store (S3 and DynamoDB), settings from the Runtime environment |
| `deploy/deploy.py` | Creates or updates the Gateway (custom JWT inbound, REQUEST interceptor, four Lambda targets with inline tool schemas) and the Runtime (direct code deployment: a zip with arm64 wheels in S3, Python 3.12, no Docker or CodeBuild) |

The model, prompt, tool list, tool-call limit and citation retry come from the pipeline config (`generation`,
`agent`) and `config/models.yaml`. Both pipeline versions use the same agent.

## Deploy

After the AWS CDK stacks exist (they write the SSM parameters the script reads):

```bash
scripts/deploy_agentcore.sh                     # Gateway, targets, interceptor, Runtime
scripts/deploy_agentcore.sh --bundle-only       # build the zip and tool schemas only, no AWS call
scripts/deploy_agentcore.sh --help
```

`--identity-mode interceptor` (the default) injects the caller's identity at the Gateway. `agent_hook` is a
fallback that sets it in the agent instead. The script writes `/rp/agent/{runtime-arn,runtime-url,gateway-id,gateway-url}`
to SSM.

## Reuse

`citations.py`, `numbers.py` and `ledger.py` depend only on the tool result shapes in
[`evidence-core`](../../packages/evidence-core/README.md). Copy them into any agent that returns structured
claims, and drop every claim the tools did not return.

## Tests

```bash
.venv/bin/python -m pytest services/agent
```

Guide: [Quickstart 7](../../docs/quickstarts/07-agent-resolve-retrieve-hop-cite.md).
