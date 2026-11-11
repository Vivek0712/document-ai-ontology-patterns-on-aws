#!/usr/bin/env bash
# COA environment from the command line (the CLI fallback of Settings → COA environment; decision 0006).
#
#   scripts/coa_env.sh status   state, build, idle deadline (or, without the control stack, COA's stacks)
#   scripts/coa_env.sh up       spin COA up (~40-60 min) and re-seed the governed-metric demo
#   scripts/coa_env.sh down     tear COA down (~20-30 min); deletes everything inside COA
#   scripts/coa_env.sh seed     re-run only the seed (scripts/coa/seed.py) against the running COA
#
# With the rp-coa-control stack deployed (CDK context rp:coaControl=true) up/down go through the same state
# machine as the UI (one build at a time, audited as caller "cli" with your role from sts get-caller-identity).
# Without it they run COA's own make targets here, in COA_DIR (default ~/coa, a checkout of the pinned commit),
# after a typed confirmation.
# Overrides: AWS_PROFILE, AWS_REGION, COA_DIR, RP_YES=1 (skip prompts).
# shellcheck source=lib/common.sh
source "$(dirname "$0")/lib/common.sh"

CMD="${1:-status}"
CFG="${RP_ROOT}/contracts/generated/coa_control.json"
[[ -f "${CFG}" ]] || die "${CFG} missing: run 'make contracts'"
cfg() { python3 -c 'import json,sys
v=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): v=v[k]
print(v)' "${CFG}" "$1"; }

FN="$(cfg function)"
PREFIX="$(cfg coa.prefix)"
STACKS="${PREFIX}-$(cfg coa.env)"
TAG="$(cfg coa.tag)"
COA_DIR="${COA_DIR:-${HOME}/coa}"

controlled() { aws_ lambda get-function --function-name "${FN}" >/dev/null 2>&1; }

invoke() {  # invoke <op> [json body]: the same entry point rp-api uses, as caller "cli"
  local out operator
  out="$(mktemp)"
  # audit text only (the function cannot verify it): the role or user name and session, never the account id
  operator="$(aws_ sts get-caller-identity --query Arn --output text 2>/dev/null | cut -d: -f6- || echo unknown)"
  aws_ lambda invoke --function-name "${FN}" --cli-binary-format raw-in-base64-out \
    --payload "{\"source\":\"cli\",\"op\":\"$1\",\"operator\":\"${operator//\"/}\",\"body\":${2:-{\}}}" \
    "${out}" >/dev/null
  python3 - "${out}" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
b = r.get("body") or {}
if r.get("status", 500) >= 400:
    print(f"refused ({r.get('status')}): {(b.get('error') or {}).get('message')}"); sys.exit(1)
build = b.get("build") or {}
print(f"state      {b.get('state')}  (since {b.get('stateSince')}{', detected' if b.get('detected') else ''})")
print(f"reason     {b.get('reason') or '-'}")
if build:
    print(f"build      {build.get('operation')} {build.get('status')}  {build.get('id')}")
    print("phases     " + " → ".join(f"{p['label']}:{p['status']}" for p in build.get("phases") or []))
    if build.get("logUrl"): print(f"log        {build['logUrl']}")
idle = b.get("idle") or {}
s = b.get("settings") or {}
print(f"idle       auto tear-down {'on' if s.get('autoTeardown') else 'off'} after {s.get('idleMinutes')} min;"
      f" last activity {idle.get('lastActivityAt') or '-'} ({idle.get('lastActivitySource') or '-'});"
      f" tear-down at {idle.get('teardownAt') or '-'}")
ep = b.get("endpoints") or {}
if ep: print(f"console    {ep.get('consoleUrl')}\nrest       {ep.get('restEndpoint')}")
if b.get("error"): print(f"error      {b['error'].get('message')}")
if b.get("outcome"): print(f"outcome    {b['outcome']}")
PY
  rm -f "${out}"
}

local_status() {
  info "rp-coa-control is not deployed: COA's stacks as CloudFormation sees them"
  aws_ cloudformation list-stacks --query "StackSummaries[?starts_with(StackName, '${STACKS}-') && StackStatus!='DELETE_COMPLETE'].[StackName,StackStatus]" --output text | sort
}

local_checkout() {
  [[ -d "${COA_DIR}/.git" ]] || die "no COA checkout at ${COA_DIR}: git clone --branch ${TAG} $(cfg coa.repo) ${COA_DIR}"
  local have
  have="$(git -C "${COA_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
  [[ "${have}" == "$(cfg coa.commit)" ]] || warn "${COA_DIR} is at ${have}, not $(cfg coa.commit) (${TAG}, the recorded snapshot's release)"
}

case "${CMD}" in
  status)
    if controlled; then invoke get; else local_status; fi
    ;;
  up)
    step "Spin COA up (${STACKS}-*): ~$(cfg lifecycle.upMinutes | tr -d '[] ' | tr ',' '-') min, about \$1.3/h while up (estimate)"
    info "A new COA Cognito user pool is created: the admin gets a new temporary-password e-mail."
    if controlled; then
      confirm "Start the spin-up build" "up"
      invoke up '{}'
    else
      local_checkout
      confirm "Run 'make deploy-dev' in ${COA_DIR} with SCL_PREFIX=${PREFIX}" "up"
      # the CDK context the earlier deploy used (COA does not persist it); SCL_SMUS_ADMIN_ARNS from your shell
      (cd "${COA_DIR}" && export SCL_PREFIX="${PREFIX}" && \
        eval "$(python3 -c 'import json,shlex,sys; print(" ".join(f"export {k}={shlex.quote(v)};" for k,v in json.load(open(sys.argv[1]))["coa"]["deployContext"].items()))' "${CFG}")" && \
        make deploy-dev)
      step "Seed the governed-metric demo"
      "${RP_PY}" "${RP_ROOT}/scripts/coa/seed.py" --root "${RP_ROOT}"
    fi
    ;;
  down)
    step "Tear COA down (${STACKS}-*): ~$(cfg lifecycle.downMinutes | tr -d '[] ' | tr ',' '-') min"
    info "Deletes everything inside COA: the uploaded ontology, the metric, the namespace and Neptune data."
    info "AgentCore can hold its network interfaces for up to $(cfg lifecycle.eniWindowHours) h; until then the VPC stays (partially down)."
    if controlled; then
      confirm "Start the tear-down build" "down"
      invoke down '{}'
    else
      local_checkout
      confirm "Run COA's destroy in ${COA_DIR} with SCL_PREFIX=${PREFIX}" "${STACKS}"
      rc=0
      info "GuardDuty's endpoint and security group in COA's VPC (if any) are deleted too, or the VPC cannot go."
      (cd "${COA_DIR}" && make generate && pnpm install --frozen-lockfile && \
        SCL_PREFIX="${PREFIX}" SCL_DESTROY_YES=1 SCL_DESTROY_DELETE_GUARDDUTY_ENDPOINT=1 \
        ./scripts/destroy.sh "$(cfg coa.env)") || rc=$?
      [[ "${rc}" == 3 ]] && warn "AgentCore still holds ENIs: run 'make coa-down' again in a few hours" && exit 0
      exit "${rc}"
    fi
    ;;
  seed)
    require_venv
    "${RP_PY}" "${RP_ROOT}/scripts/coa/seed.py" --root "${RP_ROOT}"
    ;;
  *)
    die "usage: scripts/coa_env.sh status|up|down|seed"
    ;;
esac
