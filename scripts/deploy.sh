#!/usr/bin/env bash
# Deploy Provenant to AWS, in order. Idempotent: rerun after any change.
#   1 build      scripts/build_lambdas.sh (no Docker) + ontology/dist (make ontology-check)
#   2 bootstrap  cdk bootstrap if the account/region has no CDKToolkit stack
#   3 core       cdk deploy rp-base rp-knowledge rp-ingest      (buckets, tables, VPC, AOSS, Neptune, pipeline)
#   4 agentcore  scripts/deploy_agentcore.sh                    (Runtime, Gateway, targets, interceptor)
#   5 agent      cdk deploy rp-agent                            (Cognito, API Gateway, get_region_http, /ask)
#   6 ui         make ui-build                                  (reads /rp/* SSM for the UI config)
#   7 web        cdk deploy rp-web                              (S3 + CloudFront)
#   8 seed       scripts/seed.sh                                (users, pipeline versions, ontology, package)
# If step 4 finds no user pool yet (Cognito lives in rp-agent), rp-agent is deployed first and again after.
#
# Usage: scripts/deploy.sh [--from STEP] [--only STEP] [--skip-seed] [--upload] [--yes]
#   --upload   pass to seed.sh: also ingest data/samples/lending/*.pdf
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

STEPS=(build bootstrap core agentcore agent ui web seed)
read -r -a STACKS_CORE <<< "${RP_STACKS_CORE:-rp-base rp-knowledge rp-ingest}"
FROM="build"
ONLY=""
SKIP_SEED=0
SEED_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --from) FROM="${2:?--from needs a step}"; shift ;;
    --only) ONLY="${2:?--only needs a step}"; shift ;;
    --skip-seed) SKIP_SEED=1 ;;
    --upload) SEED_ARGS+=(--upload) ;;
    --yes) export RP_YES=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
printf '%s\n' "${STEPS[@]}" | grep -qx "${FROM}" || die "--from must be one of: ${STEPS[*]}"
[[ -z "${ONLY}" ]] || printf '%s\n' "${STEPS[@]}" | grep -qx "${ONLY}" || die "--only must be one of: ${STEPS[*]}"

started=0
should_run() {
  local s="$1"
  if [[ -n "${ONLY}" ]]; then [[ "${s}" == "${ONLY}" ]]; return; fi
  [[ "${s}" == "${FROM}" ]] && started=1
  [[ "${started}" == "1" ]]
}

cdk_() {
  cdk_flags
  (cd "${RP_ROOT}/infra" && npx --no-install cdk "${CDK_FLAGS[@]}" "$@")
}
cdk_deploy() {
  info "cdk deploy $*"
  cdk_ deploy "$@" --require-approval never --exclusively --outputs-file "${RP_ROOT}/build/cdk-outputs.json"
}

start=$(date +%s)
require aws node npx make
require_venv
[[ -d "${RP_ROOT}/infra/node_modules" ]] || (cd "${RP_ROOT}/infra" && npm ci)
mkdir -p "${RP_ROOT}/build"
whoami_banner
confirm "Deploy the Provenant stacks to ${AWS_PROFILE:-default credentials}/${AWS_REGION} (creates billable resources)?"

if should_run build; then
  step "1/8 build: Lambda code + layers, ontology dist"
  "${RP_SCRIPTS_DIR}/build_lambdas.sh"
  make -C "${RP_ROOT}" ontology-check
fi

if should_run bootstrap; then
  step "2/8 bootstrap"
  if aws_ cloudformation describe-stacks --stack-name CDKToolkit >/dev/null 2>&1; then
    ok "CDKToolkit present"
  else
    cdk_ bootstrap
  fi
fi

if should_run core; then
  step "3/8 core stacks: ${STACKS_CORE[*]}"
  cdk_deploy "${STACKS_CORE[@]}"
fi

if should_run agentcore; then
  step "4/8 AgentCore Runtime + Gateway (scripts/deploy_agentcore.sh)"
  if [[ -z "$(ssm_get user_pool_id --optional)" ]]; then
    warn "no /rp/user-pool-id yet: deploying rp-agent first for Cognito, again after AgentCore"
    cdk_deploy rp-agent
  fi
  "${RP_SCRIPTS_DIR}/deploy_agentcore.sh"
fi

if should_run agent; then
  step "5/8 rp-agent (Cognito, API Gateway, /ask integration)"
  cdk_deploy rp-agent
fi

if should_run ui; then
  step "6/8 UI build"
  make -C "${RP_ROOT}" ui-build
fi

if should_run web; then
  step "7/8 rp-web (S3 + CloudFront)"
  cdk_deploy rp-web
fi

if should_run seed; then
  if [[ "${SKIP_SEED}" == "1" ]]; then
    step "8/8 seed: skipped (--skip-seed)"
  else
    step "8/8 seed"
    "${RP_SCRIPTS_DIR}/seed.sh" "${SEED_ARGS[@]+"${SEED_ARGS[@]}"}"
  fi
fi

step "Deployed in $(elapsed_since "${start}")"
info "web  $(ssm_get web_url --optional || true)"
info "api  $(ssm_get api_url --optional || true)"
info "next: scripts/smoke.sh, then scripts/prewarm.sh before a session; scripts/pause.sh after"
