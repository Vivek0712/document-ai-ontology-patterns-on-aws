#!/usr/bin/env bash
# Remove the whole stack. Asks for confirmation twice: once for the stacks, once for the data.
#   1 export runs and eval results into the repo (scripts/export_runs.sh) unless --skip-export
#   2 delete AgentCore Gateway targets, Gateway, Runtime and their /rp/agent/* SSM parameters
#   3 cdk destroy --all (rp-web, rp-agent, rp-ingest, rp-knowledge, rp-base)
#   4 empty and delete the retained data and UI buckets (every object version)
#   5 list anything rp-* left (tables, log groups) and delete /rp/demo/password
#
# Usage: scripts/teardown.sh [--skip-export] [--keep-buckets] [--yes]
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

SKIP_EXPORT=0
KEEP_BUCKETS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-export) SKIP_EXPORT=1 ;;
    --keep-buckets) KEEP_BUCKETS=1 ;;
    --yes) export RP_YES=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

start=$(date +%s)
require aws npx
require_venv
whoami_banner
confirm "Destroy every Provenant stack in ${AWS_PROFILE:-default credentials}/${AWS_REGION}?" "teardown"

# Read bucket names before the stacks (and their SSM parameters) go away.
DATA_BUCKET="$(ssm_get data_bucket --optional || true)"
UI_BUCKET="$(ssm_get ui_bucket --optional || true)"

if [[ "${SKIP_EXPORT}" == "0" ]]; then
  step "1/5 export runs (the logged evidence) before anything is deleted"
  if [[ -n "${DATA_BUCKET}" ]]; then
    "${RP_SCRIPTS_DIR}/export_runs.sh"
  else
    warn "no data bucket in SSM; nothing to export"
  fi
else
  step "1/5 export: skipped (--skip-export)"
fi

step "2/5 AgentCore resources (outside CDK)"
rp_ops agentcore-delete

step "3/5 cdk destroy --all"
cdk_flags
(cd "${RP_ROOT}/infra" && npx --no-install cdk "${CDK_FLAGS[@]}" destroy --all --force)

step "4/5 retained buckets"
if [[ "${KEEP_BUCKETS}" == "1" ]]; then
  info "kept (--keep-buckets): ${DATA_BUCKET:-?} ${UI_BUCKET:-?}"
else
  for b in ${DATA_BUCKET} ${UI_BUCKET}; do
    if aws_ s3api head-bucket --bucket "${b}" >/dev/null 2>&1; then
      rp_ops empty-bucket "${b}" --dry-run
      confirm "Empty and delete bucket ${b} (all versions, not recoverable)?" "${b}"
      rp_ops empty-bucket "${b}"
      aws_ s3api delete-bucket --bucket "${b}"
      ok "deleted ${b}"
    else
      info "${b}: already gone"
    fi
  done
fi

step "5/5 leftovers"
aws_ ssm delete-parameter --name /rp/demo/password >/dev/null 2>&1 && ok "deleted /rp/demo/password" || true
# shellcheck disable=SC2016  # JMESPath literal, not a shell expansion
left_tables="$(aws_ dynamodb list-tables --query 'TableNames[?starts_with(@, `rp-`)]' --output text || true)"
left_logs="$(aws_ logs describe-log-groups --log-group-name-prefix /aws/lambda/rp- --query 'logGroups[].logGroupName' --output text || true)"
[[ -z "${left_tables}" ]] || warn "tables still present (RETAIN policy?): ${left_tables}"
[[ -z "${left_logs}" ]] || info "log groups kept for later reading: ${left_logs}"
step "Torn down in $(elapsed_since "${start}")"
