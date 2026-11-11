#!/usr/bin/env bash
# One-time setup before the first deploy of rp-coa-control (Quickstart 11, decision 0006 "The executor's permissions"):
# COA gets its own CDK bootstrap, so the rp-coa-lifecycle build never touches the bootstrap Provenant's stacks use.
#
#   1. stack rp-coa-guard must be deployed first: it holds the permissions boundary (config bootstrap.boundaryPolicy)
#        (cd infra && npx cdk deploy rp-coa-guard -c rp:coaControl=true)
#   2. this script: `cdk bootstrap` with qualifier bootstrap.qualifier, its own toolkit stack, and that boundary on
#      the CloudFormation execution role (which then cannot create a role without the same boundary)
#   3. deploy everything with -c rp:coaControl=true: rp-coa-control attaches bootstrap.deployGuardPolicy to the new
#      deploy role (it fails if step 2 has not run, so the executor never exists without its guards)
#
# Needs credentials that may create IAM roles (an account admin). Asks before changing anything (RP_YES=1 skips).
# Overrides: AWS_PROFILE, AWS_REGION. Usage: scripts/coa/bootstrap.sh [--print]   (--print: the command only)
# shellcheck source=../lib/common.sh
source "$(dirname "$0")/../lib/common.sh"

CFG="${RP_ROOT}/contracts/generated/coa_control.json"
[[ -f "${CFG}" ]] || die "${CFG} missing: run 'make contracts'"
cfg() { python3 -c 'import json,sys
v=json.load(open(sys.argv[1]))
for k in sys.argv[2].split("."): v=v[k]
print(v)' "${CFG}" "$1"; }

QUALIFIER="$(cfg bootstrap.qualifier)"
TOOLKIT="$(cfg bootstrap.toolkitStackName)"
BOUNDARY="$(cfg bootstrap.boundaryPolicy)"

if [[ "${1:-}" == "--print" ]]; then
  echo "npx cdk bootstrap aws://<account>/${AWS_REGION} --qualifier ${QUALIFIER} --toolkit-stack-name ${TOOLKIT}" \
    "--cloudformation-execution-policies arn:aws:iam::aws:policy/AdministratorAccess" \
    "--custom-permissions-boundary ${BOUNDARY}"
  exit 0
fi

step "COA's own CDK bootstrap (qualifier ${QUALIFIER}, toolkit stack ${TOOLKIT})"
whoami_banner
ACCOUNT="$(aws_ sts get-caller-identity --query Account --output text)"
aws_ iam get-policy --policy-arn "arn:aws:iam::${ACCOUNT}:policy/${BOUNDARY}" >/dev/null 2>&1 \
  || die "policy ${BOUNDARY} not found: deploy rp-coa-guard first (cd infra && npx cdk deploy rp-coa-guard -c rp:coaControl=true)"
ok "permissions boundary ${BOUNDARY} exists"
info "The execution role gets AdministratorAccess bounded by ${BOUNDARY}: Provenant's stacks, roles, user pool,"
info "data and the default CDK bootstrap stay out of reach of anything COA deploys."
confirm "Create or update stack ${TOOLKIT}" "${QUALIFIER}"
cdk_flags
(cd "${RP_ROOT}/infra" && npx cdk bootstrap "aws://${ACCOUNT}/${AWS_REGION}" "${CDK_FLAGS[@]}" \
  --qualifier "${QUALIFIER}" --toolkit-stack-name "${TOOLKIT}" \
  --cloudformation-execution-policies "arn:aws:iam::aws:policy/AdministratorAccess" \
  --custom-permissions-boundary "${BOUNDARY}")
ok "bootstrapped; now deploy with -c rp:coaControl=true (docs/quickstarts/11-coa-environment.md)"
