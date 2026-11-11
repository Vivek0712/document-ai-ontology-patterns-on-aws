#!/usr/bin/env bash
# Stop paying for idle capacity (end of every build day):
#   docling-serve desired 0 and scaling floor 0; provisioned concurrency removed; Neptune cluster stopped.
# OpenSearch Serverless (NextGen group rp-units-group, min 0 OCU), DynamoDB, S3, Lambda and Step Functions
# cost ~nothing idle and stay up. `make verify` warns if the collection is Classic (24/7 OCU floor).
# Neptune restarts automatically after 7 days stopped; rerun this script if the break is longer.
#
# Usage: scripts/pause.sh [--wait]     (--wait blocks until Neptune reports stopped)
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

ARGS=()
for arg in "$@"; do
  case "${arg}" in
    --wait) ARGS+=(--wait) ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) die "unknown argument: ${arg}" ;;
  esac
done

require aws
require_venv
whoami_banner
step "Pause: docling to 0, provisioned concurrency off, Neptune stop"
rp_ops pause "${ARGS[@]+"${ARGS[@]}"}"
ok "paused; scripts/resume.sh then scripts/prewarm.sh to come back"
