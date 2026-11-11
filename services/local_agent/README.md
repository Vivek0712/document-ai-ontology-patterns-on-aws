# local_agent

Asks golden questions of a local pipeline build with the real agent runner, prompt, model and tool code. The
tools' services are local (the build's world snapshot in moto, an in-memory index, pyoxigraph for the graph), and
the Amazon Bedrock AgentCore Gateway interceptor is played by `gateway.py`, which drops any identity the model supplies and injects
the caller's. **The model call is real: it goes to Amazon Bedrock and costs money.**

```bash
make local-ask QID=q-lend-01 REPEAT=3 BUDGET=0.5     # needs build/e2e (make e2e-local PII=local)
make local-ask-summary                               # N-of-N table and lane parity, no model calls
PYTHONPATH=services .venv/bin/python -m local_agent ask --help
```

| Option | Meaning |
|---|---|
| `--qid` (repeatable) or `--package` | Which golden questions from `eval/golden.jsonl` |
| `--repeat` | Runs per question and lane |
| `--lanes` | `1.0,2.3.1` by default |
| `--build` | The local build to ask against (`build/e2e`, or `build/e2e-<package>` after `make e2e-packages`) |
| `--budget-usd` | Stops the batch before its own model spend, at list price, would pass this |
| `--generation-profile` | `default`, `compact` or `frontier`: swap the answer model for these runs only |
| `--as` | `a` (package owner) or `b` (another group, to see access denied) |

Every run appends to `runs/local/`: a run record (`r-*.json`), its event stream (`events/*.jsonl`) and
`index.json` (scores, cost, the representative run per question and lane). The query embedding uses Amazon Titan
Text Embeddings V2 through Amazon Bedrock, recorded locally in `runs/local/recorded/titan-v2.json` (that file is not
part of the repository).

Credentials: the standard AWS credential chain, or `AWS_PROFILE`. Region `us-east-1`.

## Tests

```bash
.venv/bin/python -m pytest services/local_agent
```

Guide: [Quickstart 7](../../docs/quickstarts/07-agent-resolve-retrieve-hop-cite.md).
