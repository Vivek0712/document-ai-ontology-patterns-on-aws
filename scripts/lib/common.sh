# shellcheck shell=bash
# Shared prelude for scripts/*.sh. Source it; do not execute it.
#   - strict mode, AWS Region default (us-east-1), repo paths. Credentials come from the default
#     AWS credential chain; set AWS_PROFILE to use a named profile.
#   - step/info/warn/die logging, confirmation prompt, `rp_ops` (python helpers) and `aws_` wrappers
# Overrides: AWS_PROFILE, AWS_REGION, RP_EXPECTED_ACCOUNT, RP_PY, RP_YES=1 (skip prompts), and anything in
# a git-ignored .env at the repo root.

set -euo pipefail

RP_SCRIPTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RP_ROOT="$(cd "${RP_SCRIPTS_DIR}/.." && pwd)"
export RP_REPO_ROOT="${RP_ROOT}"

if [[ -f "${RP_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${RP_ROOT}/.env"
  set +a
fi

# No profile default: the AWS CLI and SDKs use the default credential chain unless AWS_PROFILE is set.
[[ -n "${AWS_PROFILE:-}" ]] || unset AWS_PROFILE
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="${AWS_REGION}"
export AWS_PAGER=""

RP_PY="${RP_PY:-${RP_ROOT}/.venv/bin/python}"
export PYTHONPATH="${RP_ROOT}/scripts/lib:${RP_ROOT}/packages/evidence-core:${RP_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

if [[ -t 2 ]]; then
  _B=$'\033[1m'; _O=$'\033[38;5;202m'; _G=$'\033[32m'; _R=$'\033[31m'; _Z=$'\033[0m'
else
  _B=""; _O=""; _G=""; _R=""; _Z=""
fi

step() { printf '\n%s==> %s%s\n' "${_B}${_O}" "$*" "${_Z}" >&2; }
info() { printf '    %s\n' "$*" >&2; }
ok()   { printf '    %sok%s %s\n' "${_G}" "${_Z}" "$*" >&2; }
warn() { printf '    %swarning:%s %s\n' "${_O}" "${_Z}" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "${_R}" "${_Z}" "$*" >&2; exit 1; }

require() {
  local cmd
  for cmd in "$@"; do
    command -v "${cmd}" >/dev/null 2>&1 || die "'${cmd}' is required but not on PATH"
  done
}

require_venv() {
  [[ -x "${RP_PY}" ]] || die "python venv missing at ${RP_PY}: run 'make venv'"
}

# aws_ <service> <command> ...: the AWS CLI with the operator's credentials and region.
aws_() { aws ${AWS_PROFILE:+--profile "${AWS_PROFILE}"} --region "${AWS_REGION}" "$@"; }

# cdk_flags: sets the CDK_FLAGS array to the region context and, when AWS_PROFILE is set, --profile
# (bash 3.2 safe: no mapfile).
cdk_flags() {
  CDK_FLAGS=(-c "rp:region=${AWS_REGION}")
  # RP_COA_CONTROL=1 keeps the opt-in COA environment control (rp-coa-control and rp-agent's grant) on every deploy.
  if [[ "${RP_COA_CONTROL:-0}" == 1 ]]; then CDK_FLAGS+=(-c "rp:coaControl=true"); fi
  if [[ -n "${AWS_PROFILE:-}" ]]; then CDK_FLAGS+=(--profile "${AWS_PROFILE}"); fi
}

# rp_ops <command> ...: python helpers in scripts/lib/rp_ops (boto3 with the same credentials and region).
rp_ops() { "${RP_PY}" -m rp_ops "$@"; }

# ssm_get <logical-or-/rp/name>: read one SSM parameter written by CDK; empty when absent with --optional.
ssm_get() { rp_ops ssm "$@"; }

# confirm "<question>" [<word to type>]: interactive guard for destructive steps; RP_YES=1 skips it.
confirm() {
  local question="$1" expected="${2:-yes}" reply
  if [[ "${RP_YES:-0}" == "1" ]]; then
    info "${question} (RP_YES=1: confirmed)"
    return 0
  fi
  [[ -t 0 ]] || die "${question}: needs a terminal to confirm (or set RP_YES=1)"
  printf '%s%s%s Type "%s" to continue: ' "${_B}" "${question}" "${_Z}" "${expected}" >&2
  read -r reply
  [[ "${reply}" == "${expected}" ]] || die "not confirmed; nothing changed"
}

# Prints the credentials source, region and (masked) account the script is about to act on.
whoami_banner() {
  local acct who="${AWS_PROFILE:-default credential chain}"
  acct="$(aws_ sts get-caller-identity --query Account --output text 2>/dev/null)" \
    || die "cannot call STS with ${who}: sign in (for example 'aws sso login${AWS_PROFILE:+ --profile ${AWS_PROFILE}}') or fix credentials"
  if [[ -n "${RP_EXPECTED_ACCOUNT:-}" && "${acct}" != "${RP_EXPECTED_ACCOUNT}" ]]; then
    die "${who} resolves to account ********${acct:8:4}, not RP_EXPECTED_ACCOUNT"
  fi
  info "${who} · region ${AWS_REGION} · account ********${acct:8:4}"
}

elapsed_since() { local start="$1"; printf '%ss' "$(( $(date +%s) - start ))"; }
