#!/usr/bin/env bash
# Day-1 account check: caller identity and region, each model key listed and invoked once,
# Textract sync AnalyzeDocument with LAYOUT on a fixture page, Comprehend PII on a fictitious string,
# OpenSearch Serverless / Neptune / Fargate / AgentCore reachability, API Gateway and Lambda quotas,
# and the local toolchain. Changes nothing.
#
# Usage: scripts/verify_account.sh [--read-only]
#   --read-only   only describe/list/get calls (no model invocation, no Textract/Comprehend call)
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

READ_ONLY=()
for arg in "$@"; do
  case "${arg}" in
    --read-only) READ_ONLY=(--read-only) ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) die "unknown argument: ${arg}" ;;
  esac
done

start=$(date +%s)
step "Toolchain"
require aws
require_venv
info "$("${RP_PY}" --version) · $(aws --version 2>&1 | cut -d' ' -f1)"
if command -v node >/dev/null; then info "node $(node --version)"; else warn "node not found (CDK and UI need node 22)"; fi
if command -v npx >/dev/null && [[ -d "${RP_ROOT}/infra/node_modules" ]]; then
  info "cdk $(cd "${RP_ROOT}/infra" && npx --no-install cdk --version 2>/dev/null || echo 'not installed in infra/')"
fi
command -v docker >/dev/null && warn "docker found but not used: Lambdas build without Docker, docling uses the upstream image"

step "Account"
whoami_banner

step "Services and models${READ_ONLY:+ (read-only)}"
rp_ops verify "${READ_ONLY[@]+"${READ_ONLY[@]}"}"
info "done in $(elapsed_since "${start}")"
