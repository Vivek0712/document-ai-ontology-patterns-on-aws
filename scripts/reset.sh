#!/usr/bin/env bash
# Reset one package to "nothing uploaded" so the journey can be run again (target < 2 min):
#   stop its running rp-ingest executions; delete its unit documents from units-v1-0 / units-v2-3-1
#   (search by packageId, delete by _id: AOSS has no _delete_by_query); drop its ABox named graphs via
#   rp-graph-admin; delete every object version under bronze/<owner>/<pkg>/, silver/<pkg>/, gold/<pkg>/,
#   quarantine/<pkg>/; delete its doc-status, unit-status, doc-acl, documents and review-queue rows;
#   zero the package row's lanes, counters and docCount. Runs (the logged evidence) are kept by default.
#
# Usage: scripts/reset.sh [PACKAGE_ID] [--dry-run] [--include-runs] [--drop-package] [--yes]
#   PACKAGE_ID defaults to pkg-lending-01.
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

PACKAGE_ID="pkg-lending-01"
ARGS=()
DRY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) ARGS+=("$1"); DRY=1 ;;
    --include-runs|--drop-package) ARGS+=("$1") ;;
    --yes) export RP_YES=1 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    -*) die "unknown argument: $1" ;;
    *) PACKAGE_ID="$1" ;;
  esac
  shift
done
[[ "${PACKAGE_ID}" =~ ^pkg-[a-z0-9][a-z0-9-]{0,43}$ ]] || die "not a package id: ${PACKAGE_ID}"

start=$(date +%s)
require aws
require_venv
whoami_banner
if [[ "${DRY}" == "0" ]]; then
  confirm "Delete every document, unit, index entry and graph of ${PACKAGE_ID}?" "${PACKAGE_ID}"
fi
step "Reset ${PACKAGE_ID}$([[ "${DRY}" == "1" ]] && echo ' (dry run)')"
rp_ops reset "${PACKAGE_ID}" "${ARGS[@]+"${ARGS[@]}"}"
ok "reset in $(elapsed_since "${start}")"
