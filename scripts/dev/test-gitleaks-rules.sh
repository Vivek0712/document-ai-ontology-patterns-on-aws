#!/usr/bin/env bash
# Positive and negative controls for the project rules in .gitleaks.toml.
#
# Writes synthetic samples to a temporary folder and checks that gitleaks flags
# every leak shape this project cares about, and none of the allowed shapes.
# The fake values are assembled at run time, so this script itself never
# contains a 12-digit number or a profile name that the rules would flag.
#
# Usage: scripts/dev/test-gitleaks-rules.sh [path-to-gitleaks]
set -euo pipefail

GITLEAKS="${1:-gitleaks}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CONFIG="${ROOT}/.gitleaks.toml"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

acct="9876""5432""1098"      # fake, not a placeholder
prof="my""work""profile"      # fake profile name
home="/Users/""alice""/src"   # fake home path
dashed="corp""-admin-""1"        # a profile-shaped name that looks like a recorded idempotency token
token="corp""-lending-ns-""1"    # a client token outside the recorded COA session

mkdir -p "${work}/pos" "${work}/neg"
# --- must be flagged (one shape per file) -----------------------------------
printf '{ "rp:account": "%s" }\n' "${acct}"                         > "${work}/pos/cdk.json"
printf "const env = { account: '%s', region: 'us-east-1' };\n" "${acct}" > "${work}/pos/app.ts"
printf 'Deployed to AWS account %s last week.\n' "${acct}"           > "${work}/pos/notes.md"
printf '{ "availability-zones:account=%s:region=us-east-1": [] }\n' "${acct}" > "${work}/pos/cdk.context.json"
printf 'arn:aws:iam::%s:role/x\n' "${acct}"                          > "${work}/pos/arn.txt"
printf 'profile = os.environ.get("AWS_PROFILE", "%s")\n' "${prof}"   > "${work}/pos/aws.py"
printf "const c = new Client({ profile: '%s' });\n" "${prof}"        > "${work}/pos/client.ts"
# shellcheck disable=SC2016  # the sample must contain a literal ${AWS_PROFILE:-...}
printf 'export AWS_PROFILE="${AWS_PROFILE:-%s}"\n' "${prof}"         > "${work}/pos/common.sh"
printf 'aws s3 ls --profile %s\n' "${prof}"                          > "${work}/pos/cli.md"
printf 'cat %s/file\n' "${home}"                                     > "${work}/pos/home.sh"
printf 'export AWS_PROFILE=%s\n' "${dashed}"                         > "${work}/pos/dashed-profile.sh"
printf '{ "clientToken": "%s" }\n' "${token}"                         > "${work}/pos/token.json"

# --- must not be flagged -----------------------------------------------------
printf '{ "rp:account": "000000000000" }\n'                           > "${work}/neg/cdk.json"
printf 'arn:aws:iam::123456789012:role/example\n'                    > "${work}/neg/arn.txt"
printf 'run r-ae7d90b38217 took 0.123456789012 s; id 1a2b3c4d-0000-1111-2222-%s\n' "${acct}" > "${work}/neg/ids.md"
printf 'export AWS_PROFILE=<your-profile>  # optional\n'             > "${work}/neg/readme.md"
printf "generationProfile: 'compact'\n"                              > "${work}/neg/run.ts"
printf 'sha256 %s%s%s%s%s\n' "${acct}" "${acct}" "${acct}" "${acct}" "abcdef0123456789"  > "${work}/neg/hash.txt"
# the token exemption applies only inside runs/local/coa/ (path AND shape)
mkdir -p "${work}/neg/coa/runs/local/coa"
printf '{ "clientToken": "%s" }\n' "${token}"                         > "${work}/neg/coa/runs/local/coa/02_namespace.json"
# runs/local/ is scanned for account ids like everything else
mkdir -p "${work}/pos/runs/local"
printf 'error: AWS account %s is not authorized\n' "${acct}"         > "${work}/pos/runs/local/error.txt"

fail=0
check() {  # dir expected(yes|no)
  local f report
  for f in "${work}/$1"/*; do
    report="${work}/r.json"
    "${GITLEAKS}" dir "${f}" --config "${CONFIG}" --no-banner --redact --report-format json \
      --report-path "${report}" --exit-code 0 >/dev/null 2>&1
    local n; n="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1])) or []))' "${report}")"
    if [[ "$2" == yes && "${n}" == 0 ]]; then echo "MISSED   $1/$(basename "${f}")"; fail=1; fi
    if [[ "$2" == no && "${n}" != 0 ]]; then echo "FALSE+   $1/$(basename "${f}")"; fail=1; fi
    if [[ ("$2" == yes && "${n}" != 0) || ("$2" == no && "${n}" == 0) ]]; then echo "ok       $1/$(basename "${f}")"; fi
  done
}
check pos yes
check neg no
exit "${fail}"
