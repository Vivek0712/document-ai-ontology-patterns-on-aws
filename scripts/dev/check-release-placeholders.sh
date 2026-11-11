#!/usr/bin/env bash
# Fail if a release placeholder is still in a tracked file.
#
# Placeholders the maintainers fill in once, when the repository is published:
#   OWNER (GitHub owner in clone URLs), OWNER_HANDLE (CODEOWNERS), [INSERT CONTACT METHOD]
#   (Code of Conduct), `] - YYYY-MM-DD` (the CHANGELOG release date; a bare YYYY-MM-DD is a date format in code), and unresolved **[verify]** markers in
#   the license notices. `[verify]` in config/prices.yaml marks a list price that has not been confirmed;
#   the docs say so, so it is allowed there and in backticks in the docs.
set -euo pipefail
cd "$(dirname "$0")/../.."

self="scripts/dev/check-release-placeholders.sh"
hits="$(git grep -n -I -E 'github\.com/OWNER/|OWNER_HANDLE|INSERT CONTACT|<this-repo|\] - YYYY-MM-DD' -- . ":!${self}" || true)"
# shellcheck disable=SC2016  # literal backticks, not a command substitution
verify="$(git grep -n -I -F '[verify]' -- '*.md' 'NOTICE' ":!${self}" | grep -v -F '`[verify]`' || true)"

if [[ -n "${hits}${verify}" ]]; then
  echo "check-release-placeholders: placeholders still present:" >&2
  printf '%s\n' "${hits}" "${verify}" | sed '/^$/d' | sed 's/^/  /' >&2
  exit 1
fi
echo "check-release-placeholders: none"
