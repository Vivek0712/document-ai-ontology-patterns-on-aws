#!/usr/bin/env bash
# The rp-coa-lifecycle build's steps (infra/lib/coa-control-stack.ts runs one per buildspec phase):
#
#   lifecycle.sh install   toolchain at the versions config/coa.yaml pins, each download checked against its pinned
#                          checksum (Node, Maven, uv; pnpm by its npm integrity), QEMU for COA's linux/arm64 images
#                          from an image pinned by digest; the JDK comes from the CodeBuild image
#   lifecycle.sh prepare   fetch COA at the pinned commit (refuses any other), generate the Smithy code, install its
#                          locked dependencies (uv --locked, pnpm --frozen-lockfile); point CDK at COA's own bootstrap
#   lifecycle.sh up        make deploy-dev with the CDK context the earlier deploy used
#   lifecycle.sh down      wait for COA stacks still being created or updated, then COA's destroy (SCL_DESTROY_YES=1,
#                          GuardDuty's leftovers in COA's VPC included); exit 3 = AgentCore ENIs held -> eni_pending
#   lifecycle.sh seed      scripts/coa/seed.py with COA's own locked Python environment (boto3 from COA's uv.lock)
#   lifecycle.sh check     after a tear-down: any COA stack left turns an ok result into leftovers
#   lifecycle.sh redact    stdin -> stdout with e-mail addresses masked (every deploy/destroy line goes through it)
#
# The step's outcome for rp-coa-control goes to $RP_RESULT_FILE (ok | eni_pending | seed_failed | leftovers), the
# stacks left to $RP_RESULT_FILE.leftovers; the buildspec exports them as RP_RESULT and RP_LEFTOVERS. Also runs on a
# workstation (scripts/coa_env.sh local mode) with RP_BUNDLE_DIR=<repo root>.
set -euo pipefail

STEP="${1:?usage: lifecycle.sh install|prepare|up|down|seed|check|redact}"

# COA's admin e-mail is the Cognito admin's username, so CloudFormation events and CDK errors can print it; the
# build log keeps 30 days. GNU sed (CodeBuild) streams line by line with -u.
redact() {
  if sed --version >/dev/null 2>&1; then
    sed -u -E 's/[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}/<email>/g'
  else
    sed -E 's/[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}/<email>/g'
  fi
}
if [ "$STEP" = redact ]; then redact; exit 0; fi

BUNDLE="${RP_BUNDLE_DIR:-${CODEBUILD_SRC_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}}"
WORK="${RP_COA_WORKDIR:-$HOME/coa-lifecycle}"
RESULT_FILE="${RP_RESULT_FILE:-/tmp/rp_result}"
CFG_JSON="$BUNDLE/contracts/generated/coa_control.json"
[ -f "$CFG_JSON" ] || { echo "lifecycle: $CFG_JSON missing (run make contracts)" >&2; exit 2; }

cfg() { python3 -c 'import json,sys
v=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): v=v[k]
print(v)' "$CFG_JSON" "$1"; }

PREFIX="$(cfg coa.prefix)"
COA_ENV="$(cfg coa.env)"
COMMIT="$(cfg coa.commit)"
REPO="$(cfg coa.repo)"
export AWS_REGION="${AWS_REGION:-$(cfg coa.region)}"
export AWS_DEFAULT_REGION="$AWS_REGION"
export SCL_PREFIX="$PREFIX"
COA_DIR="$WORK/context-ontology-accelerator"

log() { printf '[coa-lifecycle %s] %s\n' "$STEP" "$*"; }
result() { printf '%s' "$1" >"$RESULT_FILE"; log "result: $1"; }

# fetch <url> <dest> <algo:hex>: download, then refuse anything whose digest is not the pinned one
fetch() {
  local url="$1" dest="$2" want="$3" algo got
  algo="${want%%:*}"
  curl -fsSL --retry 3 -o "$dest" "$url"
  got="$(openssl dgst "-$algo" "$dest" | awk '{print $NF}')"
  if [ "$algo:$got" != "$want" ]; then
    log "checksum mismatch for $url ($algo $got)"; rm -f "$dest"; exit 2
  fi
}

install_toolchain() {
  local node pnpm maven uv binfmt integrity got
  node="$(cfg executor.toolchain.node)"; pnpm="$(cfg executor.toolchain.pnpm)"
  maven="$(cfg executor.toolchain.maven)"; uv="$(cfg executor.toolchain.uv)"; binfmt="$(cfg executor.binfmtImage)"
  mkdir -p "$WORK/tools" "$WORK/dl"
  log "node $node"
  fetch "https://nodejs.org/dist/v${node}/node-v${node}-linux-x64.tar.xz" "$WORK/dl/node.tar.xz" \
    "$(cfg executor.checksums.node)"
  tar -xJf "$WORK/dl/node.tar.xz" -C "$WORK/tools"
  export PATH="$WORK/tools/node-v${node}-linux-x64/bin:$PATH"
  log "pnpm $pnpm"
  curl -fsSL --retry 3 -o "$WORK/dl/pnpm.tgz" "https://registry.npmjs.org/pnpm/-/pnpm-${pnpm}.tgz"
  integrity="$(cfg executor.checksums.pnpm)"
  got="sha512-$(openssl dgst -sha512 -binary "$WORK/dl/pnpm.tgz" | base64 | tr -d '\n')"
  [ "$got" = "$integrity" ] || { log "checksum mismatch for pnpm $pnpm"; exit 2; }
  npm install -g --ignore-scripts "$WORK/dl/pnpm.tgz" >/dev/null
  log "maven $maven"
  fetch "https://archive.apache.org/dist/maven/maven-3/${maven}/binaries/apache-maven-${maven}-bin.tar.gz" \
    "$WORK/dl/maven.tar.gz" "$(cfg executor.checksums.maven)"
  tar -xzf "$WORK/dl/maven.tar.gz" -C "$WORK/tools"
  log "uv $uv"
  fetch "https://github.com/astral-sh/uv/releases/download/${uv}/uv-x86_64-unknown-linux-gnu.tar.gz" \
    "$WORK/dl/uv.tar.gz" "$(cfg executor.checksums.uv)"
  tar -xzf "$WORK/dl/uv.tar.gz" -C "$WORK/tools"
  log "QEMU binfmt for linux/arm64 image builds (${binfmt%%@*}, pinned by digest)"
  docker run --privileged --rm "$binfmt" --install arm64 >/dev/null
  java -version 2>&1 | head -1
  # later phases run in new shells: hand the PATH on
  # shellcheck disable=SC2016 # $PATH expands when env.sh is sourced, not now
  printf 'export PATH="%s:%s:%s:$PATH"\n' "$WORK/tools/node-v${node}-linux-x64/bin" \
    "$WORK/tools/apache-maven-${maven}/bin" "$WORK/tools/uv-x86_64-unknown-linux-gnu" >"$WORK/env.sh"
}

# shellcheck disable=SC1091 # written by the install step
load_env() { [ -f "$WORK/env.sh" ] && . "$WORK/env.sh"; return 0; }

# COA's CDK apps deploy through COA's own bootstrap (qualifier, toolkit stack, permissions boundary on every role
# they create): the user-level ~/.cdk.json is read by every `cdk` COA's scripts run, and leaves COA's tree as fetched.
use_coa_bootstrap() {
  local account qualifier
  [ -n "${CODEBUILD_BUILD_ID:-}" ] || [ "${RP_COA_BOOTSTRAP:-}" = 1 ] || return 0
  account="$(aws sts get-caller-identity --query Account --output text)"
  qualifier="$(cfg bootstrap.qualifier)"
  python3 - "$HOME/.cdk.json" "$qualifier" "$(cfg bootstrap.toolkitStackName)" "$(cfg bootstrap.boundaryPolicy)" \
    "cdk-${qualifier}-cfn-exec-role-${account}-${AWS_REGION}" <<'PY'
import json, sys
path, qualifier, toolkit, boundary, exec_role = sys.argv[1:6]
json.dump({"toolkitStackName": toolkit, "context": {
    "@aws-cdk/core:bootstrapQualifier": qualifier,
    "@aws-cdk/core:permissionsBoundary": {"name": boundary},
    "cdk_cfn_exec_role_name": exec_role}}, open(path, "w"), indent=2)
PY
  log "CDK bootstrap qualifier $qualifier, permissions boundary $(cfg bootstrap.boundaryPolicy)"
}

prepare() {
  load_env
  if [ ! -d "$COA_DIR/.git" ]; then
    log "fetch $REPO at $COMMIT ($(cfg coa.tag))"
    git init --quiet "$COA_DIR"
    git -C "$COA_DIR" remote add origin "$REPO"
    git -C "$COA_DIR" fetch --quiet --depth 1 origin "$COMMIT"
    git -C "$COA_DIR" checkout --quiet --detach FETCH_HEAD
  fi
  cd "$COA_DIR"
  [ "$(git rev-parse HEAD)" = "$COMMIT" ] || { log "checkout is $(git rev-parse HEAD), not $COMMIT"; exit 2; }
  log "smithy generate, uv sync --locked, pnpm install --frozen-lockfile"
  make generate
  uv sync --all-packages --locked --quiet
  pnpm install --frozen-lockfile
  use_coa_bootstrap
}

deploy_context() {
  local key
  for key in $(python3 -c 'import json,sys; print(" ".join(json.load(open(sys.argv[1]))["coa"]["deployContext"]))' "$CFG_JSON"); do
    export "$key=$(cfg "coa.deployContext.$key")"
    log "$key=$(cfg "coa.deployContext.$key")"
  done
  # an account-specific ARN: from SSM when the operator set it, else COA falls back to the IAM role Admin
  local param arns
  param="$(cfg coa.smusAdminArnsParameter)"
  arns="$(aws ssm get-parameter --name "$param" --query Parameter.Value --output text 2>/dev/null || true)"
  if [ -n "$arns" ] && [ "$arns" != "None" ]; then
    export SCL_SMUS_ADMIN_ARNS="$arns"
    log "SCL_SMUS_ADMIN_ARNS from $param"
  fi
}

up() {
  load_env
  deploy_context
  cd "$COA_DIR"
  log "make deploy-dev (SCL_PREFIX=$PREFIX)"
  make deploy-dev 2>&1 | redact
  result ok
}

# A spin-up stopped mid-way (forced tear-down) leaves CloudFormation creating or updating COA's stacks; COA's destroy
# against stacks that are still changing fails, so wait (bounded) until none is *_IN_PROGRESS.
settle() {
  local deadline busy
  deadline=$(( $(date +%s) + $(cfg lifecycle.settleMinutes) * 60 ))
  while :; do
    busy="$(aws cloudformation list-stacks --stack-status-filter CREATE_IN_PROGRESS UPDATE_IN_PROGRESS \
      UPDATE_COMPLETE_CLEANUP_IN_PROGRESS ROLLBACK_IN_PROGRESS UPDATE_ROLLBACK_IN_PROGRESS \
      UPDATE_ROLLBACK_COMPLETE_CLEANUP_IN_PROGRESS REVIEW_IN_PROGRESS IMPORT_IN_PROGRESS \
      --query "StackSummaries[?starts_with(StackName, '${PREFIX}-${COA_ENV}-')].StackName" --output text)"
    if [ -z "$busy" ] || [ "$busy" = None ]; then return 0; fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      log "still in progress after $(cfg lifecycle.settleMinutes) min: $busy; destroying anyway"; return 0
    fi
    log "waiting for stacks still in progress: $busy"
    sleep "${RP_SETTLE_POLL_SECONDS:-30}"
  done
}

down() {
  load_env
  settle
  cd "$COA_DIR"
  log "destroy (SCL_PREFIX=$PREFIX, SCL_DESTROY_YES=1, SCL_DESTROY_DELETE_GUARDDUTY_ENDPOINT=1)"
  # COA's destroy-dev target is `make generate; pnpm install; ./scripts/destroy.sh dev` (both done in prepare).
  # Called directly: make reports destroy.sh's exit 3 (ENIs pending) as 2, like any other failure.
  # GuardDuty Runtime Monitoring puts an endpoint and a security group in COA's VPC; destroy.sh deletes them (only in
  # COA's own VPC, after checking nothing else uses it) when asked, or the VPC can never go.
  local rc=0
  SCL_DESTROY_YES=1 SCL_DESTROY_DELETE_GUARDDUTY_ENDPOINT=1 ./scripts/destroy.sh "$COA_ENV" 2>&1 | redact || rc=$?
  case "$rc" in
    0) result ok ;;
    3) result eni_pending; log "AgentCore still holds runtime ENIs; rp-coa-control retries the destroy" ;;
    *) log "destroy.sh exited $rc"; exit "$rc" ;;
  esac
}

seed() {
  load_env
  # COA's own environment (uv sync --locked): boto3 at the version and hashes COA's uv.lock pins
  local py="$COA_DIR/.venv/bin/python"
  [ -x "$py" ] || py=python3
  if ! "$py" -c 'import boto3' 2>/dev/null; then
    log "no boto3 for $py: the seed cannot run"
    result seed_failed
    return 0
  fi
  log "seed governed-metric demo ($py)"
  if "$py" -I "$BUNDLE/scripts/coa/seed.py" --root "$BUNDLE" 2>&1 | redact; then
    result ok
  else
    result seed_failed  # COA is up; the panel shows the seed error and keeps COA running
  fi
}

check() {
  local left
  left="$(aws cloudformation list-stacks --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE DELETE_FAILED \
    DELETE_IN_PROGRESS UPDATE_ROLLBACK_COMPLETE ROLLBACK_COMPLETE CREATE_FAILED UPDATE_ROLLBACK_FAILED \
    --query "StackSummaries[?starts_with(StackName, '${PREFIX}-${COA_ENV}-')].[StackName,StackStatus]" \
    --output text | tr '\t' ' ' | paste -sd ',' - || true)"
  [ "$left" = None ] && left=""
  if [ -z "$left" ]; then log "no ${PREFIX}-${COA_ENV}-* stack left"; return 0; fi
  log "left: $left"
  printf '%s' "${left:0:900}" >"$RESULT_FILE.leftovers"
  if [ "$(cat "$RESULT_FILE" 2>/dev/null || true)" = ok ]; then result leftovers; fi
}

case "$STEP" in
  install) install_toolchain ;;
  prepare) prepare ;;
  up) up ;;
  down) down ;;
  seed) seed ;;
  check) check ;;
  *) echo "unknown step $STEP" >&2; exit 2 ;;
esac
