#!/usr/bin/env bash
# Fail if any file (or the git history) contains a string from the private denylist.
#
# The denylist holds exact strings that must never appear in this public
# repository: real AWS account ids, CLI profile names, people's names and
# handles, employer names, internal hostnames. It is kept OUTSIDE the repo so
# the strings are never committed, not even in a config file.
#
# Sources, first match wins:
#   1. $DENYLIST          newline-separated strings (used by CI from a repo secret)
#   2. $DENYLIST_FILE     path to a file with one string per line
#   3. ~/.config/doc-ai-patterns/denylist.txt
#
# Usage:
#   scripts/dev/check-denylist.sh [file ...]   # pre-commit passes staged files
#   scripts/dev/check-denylist.sh              # no args: every tracked file
#   scripts/dev/check-denylist.sh --history    # every patch, commit message and author/committer identity
#                                              # in the git history
#   scripts/dev/check-denylist.sh --require    # (with any of the above) fail when no denylist is configured,
#                                              # instead of skipping; CI uses it on main and on the schedule
#
# Allowed contexts. The maintainers' public GitHub handle may appear in exactly three places, which are masked
# before matching: owner entries in .github/CODEOWNERS (@handle), links to this repository on GitHub
# (github.com/<owner>/document-ai-ontology-patterns-on-aws), and a commit identity that is a GitHub noreply
# address (<id>+<handle>@users.noreply.github.com) whose display name is that same handle. Any other occurrence
# still fails, including a personal display name next to a noreply address.
#
# Matching is case-insensitive and literal. Output names files (or counts lines in history), never the matched
# string, so CI logs on a public repository stay clean.
set -euo pipefail

require=0
if [[ "${1:-}" == "--require" ]]; then require=1; shift; fi

tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT

if [[ -n "${DENYLIST:-}" ]]; then
  printf '%s\n' "${DENYLIST}" > "${tmp}"
elif [[ -n "${DENYLIST_FILE:-}" && -f "${DENYLIST_FILE}" ]]; then
  cp "${DENYLIST_FILE}" "${tmp}"
elif [[ -f "${HOME}/.config/doc-ai-patterns/denylist.txt" ]]; then
  cp "${HOME}/.config/doc-ai-patterns/denylist.txt" "${tmp}"
elif [[ "${require}" == 1 ]]; then
  echo "check-denylist: no private denylist configured (DENYLIST is empty); failing because --require is set" >&2
  exit 1
else
  echo "check-denylist: no private denylist found; skipping (set DENYLIST or DENYLIST_FILE)" >&2
  exit 0
fi

# Drop blank lines and comments.
grep -v -e '^[[:space:]]*$' -e '^[[:space:]]*#' "${tmp}" > "${tmp}.clean" || true
mv "${tmp}.clean" "${tmp}"
if [[ ! -s "${tmp}" ]]; then
  [[ "${require}" == 1 ]] && { echo "check-denylist: the denylist is empty; failing because --require is set" >&2; exit 1; }
  exit 0
fi

DENY="${tmp}" python3 - "$@" <<'PY'
import os
import re
import subprocess
import sys

REPO = "document-ai-ontology-patterns-on-aws"
deny = [s.strip().lower() for s in open(os.environ["DENY"], encoding="utf-8") if s.strip()]
REPO_URL = re.compile(r"(github\.com/)[A-Za-z0-9-]+(/" + re.escape(REPO) + r")", re.I)
OWNER_ENTRY = re.compile(r"@[A-Za-z0-9-]+(/[A-Za-z0-9_.-]+)?")


def masked(text: str, path: str) -> str:
    text = REPO_URL.sub(r"\1OWNER\2", text)
    if path.replace("\\", "/").endswith(".github/CODEOWNERS"):
        text = OWNER_ENTRY.sub("@OWNER", text)
    return text.lower()


def hit(text: str, path: str) -> bool:
    if not any(d in text.lower() for d in deny):
        return False
    m = masked(text, path)
    return any(d in m for d in deny)


args = sys.argv[1:]
NOREPLY = re.compile(r"^(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com$", re.I)


def identity_hit(name: str, email: str) -> bool:
    """A commit identity. Allowed: a GitHub noreply address, and a display name equal to its handle."""
    m = NOREPLY.match(email.strip())
    if m:
        email = "noreply"
        if name.strip().lower() == m.group(1).lower():
            name = "handle"
    return hit(name, "COMMIT_IDENTITY") or hit(email, "COMMIT_IDENTITY")


if args == ["--history"]:
    git = ["git", "log", "--all", "--no-color"]
    log = subprocess.run([*git, "-p", "--format="],  # noqa: S603
                         capture_output=True, text=True, errors="replace", check=True).stdout
    current, n = "", 0
    for line in log.split("\n"):
        if line.startswith("diff --git "):
            current = line.rsplit(" b/", 1)[-1]
            continue
        if hit(line, current):
            n += 1
    # Commit headers: author and committer identities, then the full message (trailers included).
    heads = subprocess.run([*git, "--format=%x1e%an%x1f%ae%x1f%cn%x1f%ce%x1f%B"],  # noqa: S603
                           capture_output=True, text=True, errors="replace", check=True).stdout
    ids = msgs = 0
    for rec in heads.split("\x1e")[1:]:
        an, ae, cn, ce, body = (rec.split("\x1f", 4) + [""] * 5)[:5]
        ids += identity_hit(an, ae) + identity_hit(cn, ce)
        msgs += sum(hit(line, "COMMIT_MESSAGE") for line in body.split("\n"))
    if n or ids or msgs:
        print(f"check-denylist: private identifiers in the git history: {n} content line(s), {ids} commit "
              f"identit(ies), {msgs} commit message line(s)", file=sys.stderr)
        sys.exit(1)
    print("check-denylist: history clean (content, commit identities and messages)")
    sys.exit(0)

if args:
    paths = args
else:
    paths = subprocess.run(["git", "ls-files", "-z"], capture_output=True, text=True,  # noqa: S603, S607
                           check=True).stdout.split("\0")
bad = []
for p in paths:
    if not p or not os.path.isfile(p):
        continue
    with open(p, "rb") as fh:
        raw = fh.read()
    if b"\0" in raw[:8192]:
        continue  # binary: scripts/dev/check-binary-metadata.py reads PDF and image metadata
    if hit(raw.decode("utf-8", errors="replace"), p):
        bad.append(p)
if bad:
    print("check-denylist: private identifiers found in:", file=sys.stderr)
    for p in bad:
        print(f"  {p}", file=sys.stderr)
    print("Replace them with placeholders or environment variables before committing.", file=sys.stderr)
    sys.exit(1)
print(f"check-denylist: {len([p for p in paths if p])} file(s) clean")
PY
