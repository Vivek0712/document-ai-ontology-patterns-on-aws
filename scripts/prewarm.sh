#!/usr/bin/env bash
# Warm the stack before a session or a take (target < 5 min):
#   Neptune available; docling-serve Fargate service desired 1 (scaling floor 1) and healthy;
#   provisioned concurrency 1 on rp-search-units, rp-graph-hop, rp-get-region, rp-get-region-http;
#   one search per index (units-v1-0, units-v2-3-1); one SPARQL template; one call per model key.
#
# Usage: scripts/prewarm.sh [--package-id ID] [--skip-docling] [--include-frontier]
# Undo with scripts/pause.sh at the end of the day.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-docling|--include-frontier) ARGS+=("$1") ;;
    --package-id) ARGS+=("$1" "${2:?--package-id needs a value}"); shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

start=$(date +%s)
require aws
require_venv
whoami_banner
step "Prewarm"
rp_ops prewarm "${ARGS[@]+"${ARGS[@]}"}"
step "Warm in $(elapsed_since "${start}"). Run scripts/pause.sh when you are done (Fargate and Neptune bill while up)."
