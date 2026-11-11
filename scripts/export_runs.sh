#!/usr/bin/env bash
# Copy the rp-runs table and runs/<runId>.json records into the repo's runs/: replay fixtures
# for `make replay`. Also pulls eval/results/*.json from S3.
# Read-only on AWS; overwrites local files with the same content.
#
# Usage: scripts/export_runs.sh [--package-id ID] [--out DIR] [--summaries-only]
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

ARGS=()
OUT="${RP_ROOT}/runs"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --summaries-only) ARGS+=("$1") ;;
    --package-id) ARGS+=("$1" "${2:?--package-id needs a value}"); shift ;;
    --out) OUT="${2:?--out needs a value}"; shift ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

require aws
require_venv
whoami_banner
step "Runs -> ${OUT}"
rp_ops export-runs --out "${OUT}" "${ARGS[@]+"${ARGS[@]}"}"
step "Eval results -> eval/results/"
BUCKET="$(ssm_get data_bucket)"
aws_ s3 sync "s3://${BUCKET}/eval/results/" "${RP_ROOT}/eval/results/" --exclude '*' --include '*.json' --only-show-errors
ok "exported"
