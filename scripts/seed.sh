#!/usr/bin/env bash
# Seed a deployed stack. Idempotent; safe to rerun.
#   1. build the ontology N-Quads, vocab snapshot and SPARQL templates (ontology/build.py) if missing
#   2. Cognito: groups lending-analyst / markets-analyst, users user-a@ / user-b@example.com
#      (password: $RP_DEMO_PASSWORD, else the SecureString /rp/demo/password, generated on first run)
#   3. rp-pipeline-versions rows for 1.0 and 2.3.1, pinned to the S3 versionId of the deployed configs
#   4. Neptune TBox (FIBO subset, DoCO, rp:) via Lambda rp-neptune-load, vocab re-export via rp-vocab-export
#   5. package pkg-lending-01 owned by user-a (allowedPrincipals user:<sub>, group:lending-analyst)
#   6. with --upload: put data/samples/lending/*.pdf into Bronze (starts both pipeline lanes per file)
#
# Usage: scripts/seed.sh [--upload] [--skip-ontology] [--skip-users] [--package-id ID]
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --upload|--skip-ontology|--skip-users) ARGS+=("$1") ;;
    --package-id) ARGS+=("$1" "${2:?--package-id needs a value}"); shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

start=$(date +%s)
require aws
require_venv
whoami_banner

step "Inputs: sample documents and ontology build"
if ! compgen -G "${RP_ROOT}/data/samples/lending/*.pdf" >/dev/null; then
  info "no sample PDFs yet: generating (make data)"
  make -C "${RP_ROOT}" data
fi
ok "$(find "${RP_ROOT}/data/samples/lending" -maxdepth 1 -name '*.pdf' | wc -l | tr -d ' ') sample PDFs"
if [[ ! -d "${RP_ROOT}/ontology/dist" ]] || [[ -z "$(find "${RP_ROOT}/ontology/dist" -name 'vocab.json' -print -quit)" ]]; then
  info "ontology/dist missing: building (make ontology-check)"
  make -C "${RP_ROOT}" ontology-check
fi
ok "ontology dist present (deployed to config/ontology/ by the CDK BucketDeployment)"

step "Users, pipeline versions, ontology load, demo package"
rp_ops seed "${ARGS[@]+"${ARGS[@]}"}"

step "Done in $(elapsed_since "${start}")"
info "Sign in at $(ssm_get web_url --optional || echo '<web url: deploy rp-web>') as user-a@example.com"
info "Password: aws ssm get-parameter --name /rp/demo/password --with-decryption --query Parameter.Value --output text${AWS_PROFILE:+ --profile ${AWS_PROFILE}} --region ${AWS_REGION}"
