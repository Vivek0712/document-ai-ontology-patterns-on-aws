#!/usr/bin/env bash
# Start the Neptune cluster after scripts/pause.sh (about 10 minutes) and wait until it is available.
# Fargate and provisioned concurrency come back with scripts/prewarm.sh.
#
# Usage: scripts/resume.sh [--no-wait]
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

ARGS=()
for arg in "$@"; do
  case "${arg}" in
    --no-wait) ARGS+=(--no-wait) ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) die "unknown argument: ${arg}" ;;
  esac
done

start=$(date +%s)
require aws
require_venv
whoami_banner
step "Resume: Neptune start"
rp_ops resume "${ARGS[@]+"${ARGS[@]}"}"
ok "resumed in $(elapsed_since "${start}"); next: scripts/prewarm.sh"
