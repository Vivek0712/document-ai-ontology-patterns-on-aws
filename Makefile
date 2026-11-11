# Targets delegate to scripts/ and subprojects.
# AWS targets use your default AWS credential chain and us-east-1.
# Override with AWS_PROFILE=<your-profile> and AWS_REGION=<region>.

SHELL := /bin/bash
.DEFAULT_GOAL := help

PY        ?= python3.12
VENV      := .venv
VPY       := $(VENV)/bin/python
VPIP      := $(VENV)/bin/pip
export AWS_REGION  ?= us-east-1
export AWS_DEFAULT_REGION ?= $(AWS_REGION)
export RP_REPO_ROOT := $(CURDIR)
UI        := usecases/provenant/ui
PACKAGE   ?= data/samples/lending
PACKAGE_ID ?= pkg-lending-01
E2E_OUT   ?= build/e2e # e2e-local output; another folder keeps a new use case apart (then BUILD= for local-ask)
COA_MODE  ?= snapshot # governed definition: snapshot = add the recorded COA session (runs/local/coa), off = Git record only
PII       ?= aws   # e2e-local PII gate: aws = real Amazon Comprehend (recorded in runs/local/recorded), local = regex stand-in
PROFILE_FLAG := $(if $(AWS_PROFILE),--profile $(AWS_PROFILE),)
CDK       := cd infra && npx cdk $(PROFILE_FLAG) -c rp:region=$(AWS_REGION)

# Builder requirement files, installed when present.
REQ_FILES := $(wildcard data/requirements.txt ontology/requirements.txt services/lambdas/requirements/*.txt \
               services/local_pipeline/requirements.txt services/agent/requirements.txt eval/requirements.txt)

.PHONY: help venv contracts test test-common lint data units ontology-check build-lambdas synth ui-build ui-dev \
        e2e-local e2e-packages xray-fixtures local-ask local-ask-summary eval-logged mock-world replay deploy deploy-agent seed prewarm reset pause resume teardown verify smoke export-runs eval clean \
        coa-status coa-up coa-down coa-seed

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-16s %s\n", $$1, $$2}'

# ---------- local ----------

venv: ## Create .venv (python3.12) and install dev + builder requirements
	@test -x $(VPY) || $(PY) -m venv $(VENV)
	$(VPIP) install -q --upgrade pip
	$(VPIP) install -q -r requirements-dev.txt
	@for f in $(REQ_FILES); do echo "pip install -r $$f"; $(VPIP) install -q -r $$f || exit 1; done

contracts: ## Regenerate contracts/generated/*.json from evidence_core
	$(VPY) -m evidence_core.export .

test: ## Run every Python test from the repo root (+ UI and infra tests when present)
	$(VPY) -m pytest
	@if [ -d $(UI)/node_modules ]; then (cd $(UI) && npm test --silent); fi
	@if [ -d infra/node_modules ]; then (cd infra && npm test --silent); fi

test-common: ## Shared contracts package only
	$(VPY) -m pytest packages/evidence-core

lint: ## ruff (Python) + shellcheck (scripts) + tsc (ui, infra)
	$(VENV)/bin/ruff check .
	@if [ -x $(VENV)/bin/shellcheck ]; then $(VENV)/bin/shellcheck -x -P SCRIPTDIR scripts/*.sh scripts/lib/*.sh scripts/coa/*.sh; fi
	@if [ -d $(UI)/node_modules ]; then (cd $(UI) && npm run lint --silent); fi
	@if [ -d infra/node_modules ]; then (cd infra && npm run lint --silent); fi

data: ## Generate the fictitious document packages and gold regions
	$(VPY) data/generate.py --out data/samples

units: ## Dev mode: v1.0 chunks and v2.3.1 units for PACKAGE as JSONL + diff
	PYTHONPATH=services $(VPY) -m local_pipeline.run --package $(PACKAGE) --out build/units

e2e-local: ontology-check ## Both lanes through `ready` (+ graph_hop on pyoxigraph, world snapshot); PII=aws|local
	PYTHONPATH=services $(VPY) -m local_pipeline.run --package $(PACKAGE) --out $(strip $(E2E_OUT)) --until ready --pii $(strip $(PII))

# The other packages, one local build each (Titan V2 Phase B and real Comprehend, both recorded, so a rebuild with
# unchanged inputs makes no AWS call). The lending build above keeps its v1 settings (hash embedder in Phase B).
E2E_PACKAGES := quarterly tradeconf lending-02
e2e-packages: ontology-check ## quarterly, tradeconf and lending-02 through `ready` -> build/e2e-<package> (recorded Comprehend; calls Amazon Bedrock for embeddings on a fresh clone)
	@for p in $(E2E_PACKAGES); do \
	  PYTHONPATH=services $(VPY) -m local_pipeline.run --package data/samples/$$p --out build/e2e-$$p --until ready \
	    --pii $(strip $(PII)) --bedrock || exit 1; done

xray-fixtures: ## X-ray of every lending page (v1.0 chunks on the page, what-if sizes) + masked renders -> ui mock fixtures
	@test -d build/e2e/v1.0 || $(MAKE) e2e-local
	PYTHONPATH=services:packages/evidence-core $(VPY) scripts/xray_fixtures.py --package $(PACKAGE) --build build/e2e

ontology-check: ## Build ontology N-Quads/vocab/templates/TBox export, run the checks, record the governed definition (COA_MODE=snapshot|off)
	$(VPY) ontology/build.py
	$(VPY) ontology/check.py
	cd ontology && PYTHONPATH=.:../packages/evidence-core ../$(VPY) -m rpont.governance --coa-mode $(strip $(COA_MODE))

replay: ## UI in replay mode over runs/*.json
	cd $(UI) && npm run replay

# ---------- build ----------

build-lambdas: ## Package Lambda code + layers without Docker
	scripts/build_lambdas.sh

synth: build-lambdas ## cdk synth all stacks
	$(CDK) synth --all

ui-build: ## Production UI build into $(UI)/dist
	cd $(UI) && npm ci && npm run build

ui-dev: ## UI dev server against the deployed API
	cd $(UI) && npm run dev

# ---------- AWS ----------

deploy: build-lambdas ui-build ontology-check ## Deploy all stacks, then AgentCore Runtime/Gateway
	$(CDK) deploy --all --require-approval never
	scripts/deploy_agentcore.sh

deploy-agent: ## Redeploy only the AgentCore Runtime/Gateway/interceptor
	scripts/deploy_agentcore.sh

seed: ## Load ontology, vocab snapshot, users, PipelineVersions, demo package
	scripts/seed.sh

prewarm: ## Docling task to 1, warm AOSS/Neptune/models, provisioned concurrency
	scripts/prewarm.sh

reset: ## Delete PACKAGE_ID objects, rows, index docs and ABox graphs
	scripts/reset.sh $(PACKAGE_ID)

pause: ## Docling desired 0, stop Neptune, drop provisioned concurrency
	scripts/pause.sh

resume: ## Start Neptune
	scripts/resume.sh

verify: ## Account/model/service checks
	scripts/verify_account.sh

smoke: ## End-to-end checks against the deployed stack
	scripts/smoke.sh

export-runs: ## Copy Runs table + runs/ to the repo for replay
	scripts/export_runs.sh

eval: ## Golden-set harness
	$(VPY) eval/run.py

teardown: ## Export runs, then destroy all stacks
	scripts/teardown.sh

# ---------- COA environment (Settings → COA environment; docs/decisions/0006) ----------

coa-status: ## COA environment: state, build, idle deadline (rp-coa-control), else COA's stacks
	scripts/coa_env.sh status

coa-up: ## Spin COA up (~40-60 min) and re-seed the governed-metric demo (asks first)
	scripts/coa_env.sh up

coa-down: ## Tear COA down (~20-30 min; deletes everything inside COA) (asks first)
	scripts/coa_env.sh down

coa-seed: ## Re-run only the governed-metric seed against the running COA
	scripts/coa_env.sh seed

clean: ## Remove local build outputs
	rm -rf build infra/cdk.out $(UI)/dist .pytest_cache .ruff_cache

# ---------- local asks, logged runs, UI mock world ----------

QID     ?= q-lend-01
REPEAT  ?= 10
BUILD   ?= build/e2e
BUDGET  ?= 0.5
LANES   ?= 1.0,2.3.1
GEN     ?= default   # generation profile: default, frontier (MODEL_FRONTIER) or compact (MODEL_COMPACT)
CALLER  ?= a         # a = the package owner; b = a user in another group
local-ask: ## Ask golden QID REPEAT times per lane against BUILD with the v1 agent on Bedrock -> runs/local (GEN, CALLER)
	@test -d $(BUILD)/world || $(MAKE) e2e-local
	PYTHONPATH=services:packages/evidence-core $(VPY) -m local_agent ask --qid $(QID) --repeat $(REPEAT) \
	  --build $(BUILD) --budget-usd $(BUDGET) --lanes $(LANES) --generation-profile $(strip $(GEN)) --as $(strip $(CALLER))

eval-logged: ## Re-score the logged runs (runs/local) into eval/results/e-logged-*.json (no model calls)
	PYTHONPATH=services:packages/evidence-core $(VPY) eval/run.py run --mode logged

local-ask-summary: ## N-of-N table of the logged local runs (runs/local/index.json)
	PYTHONPATH=services:packages/evidence-core $(VPY) -m local_agent summary

mock-world: ## UI mock world from the real local runs: rp-api responses over every build/e2e*/world + runs/local -> $(UI)/mock-public/mock
	@test -d build/e2e/world || $(MAKE) e2e-local
	PYTHONPATH=services:packages/evidence-core $(VPY) scripts/mock_world.py --runs runs/local \
	  $(if $(filter on,$(PUBLIC_METRICS)),--public-metrics)
