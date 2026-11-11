#!/usr/bin/env bash
# End-to-end checks against the deployed stack, walking the user journey:
#   sign in as user-a (SRP access token) -> GET /me; an ID token gets 401 (AuthorizationScopes);
#   /pipelines and /pipelines/diff (only `preprocessing` differs); /packages, package detail, /status;
#   user-b is denied pkg-lending-01; the stage inspector routes for the first document (every stage record,
#   events, units, both lanes); the web URL. Responses are validated against the contract models.
#   --ask also asks the headline question on 1.0 and 2.3.1 (two model calls) and checks the SSE stream.
#
# Usage: scripts/smoke.sh [--ask] [--package-id ID]
set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ask) ARGS+=("$1") ;;
    --package-id) ARGS+=("$1" "${2:?--package-id needs a value}"); shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done

require aws
require_venv
whoami_banner
step "Smoke"
rp_ops smoke "${ARGS[@]+"${ARGS[@]}"}"
