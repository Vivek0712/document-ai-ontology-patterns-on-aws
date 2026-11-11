#!/usr/bin/env bash
# Deploy the Provenant agent plane:
#   AgentCore Gateway rp-gateway  (CUSTOM_JWT on rp-users, REQUEST interceptor rp-interceptor,
#                                  Lambda targets search-units / graph-hop / get-region / doc-status)
#   AgentCore Runtime rp_agent    (Strands agent, direct code deploy: zip in S3, PYTHON_3_12, arm64 wheels;
#                                  no Docker and no CodeBuild needed)
# Prerequisite: the CDK stacks are deployed (they write /rp/* SSM parameters and the Lambda ARNs).
# Writes /rp/agent/{gateway-id,gateway-url,runtime-arn,runtime-url} to SSM and build/agentcore/outputs.json.
#
# Usage: scripts/deploy_agentcore.sh [--identity-mode interceptor|agent_hook] [--debug-errors]
#                                    [--bundle-only] [--skip-gateway] [--skip-runtime]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${ROOT}/.venv/bin/python"
[[ -x "${PY}" ]] || PY="$(command -v python3.12 || command -v python3)"

export AWS_REGION="${AWS_REGION:-us-east-1}"
export PYTHONPATH="${ROOT}/packages/evidence-core:${ROOT}/services${PYTHONPATH:+:${PYTHONPATH}}"

if [[ ! -f "${ROOT}/contracts/generated/mcp_tools.json" ]]; then
  echo "contracts/generated/mcp_tools.json missing: run 'make contracts'" >&2
  exit 1
fi

# --profile only when AWS_PROFILE is set; otherwise boto3 uses the default credential chain.
exec "${PY}" "${ROOT}/services/agent/deploy/deploy.py" --region "${AWS_REGION}" \
  ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} "$@"
