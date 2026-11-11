#!/usr/bin/env bash
# Package Lambda code and dependency layers without Docker.
#
#   build/lambda/code/                 one code asset for every function
#     lambdas/                         copy of services/lambdas (tests, caches, requirements excluded)
#     evidence_core/              copy of packages/evidence-core/evidence_core
#     BUILD_INFO.json                  {codeSha, builtAt}
#   build/layers/<name>/python/        pip --platform manylinux{2014,_2_28,_2_34}_x86_64 --only-binary=:all:
#     core    = packages/evidence-core dependencies (pydantic, PyYAML)
#     ingest  = services/lambdas/requirements/ingest.txt
#     agent   = services/lambdas/requirements/agent.txt
#     api     = services/lambdas/requirements/api.txt
#   build/lambda/manifest.json         {codeSha, layers: {name: {requirementsSha, path}}}; read by infra/
#
# A layer is rebuilt only when its requirements change (sha stamp). A requirements file that lists no
# packages produces no layer. Runtime: python3.12, x86_64 (matches infra/lib/functions.ts).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="${RP_BUILD_DIR:-$ROOT/build}"
CODE="$BUILD/lambda/code"
LAYERS="$BUILD/layers"
PY_VERSION="3.12"
# python3.12 Lambda runs on Amazon Linux 2023 (glibc 2.34): manylinux2014 (glibc 2.17) and the newer
# glibc 2.28 / 2.34 wheels all load there; newer packages (e.g. pillow 13) only ship the latter.
PLATFORMS=(manylinux2014_x86_64 manylinux_2_28_x86_64 manylinux_2_34_x86_64)
PLATFORM_ARGS=()
for p in "${PLATFORMS[@]}"; do PLATFORM_ARGS+=(--platform "$p"); done

if [[ -x "$ROOT/.venv/bin/python" ]]; then PY="$ROOT/.venv/bin/python"; else PY="${PY:-python3.12}"; fi

log() { printf '[build_lambdas] %s\n' "$*"; }

sha_of_file() { "$PY" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }

has_requirements() {  # true when the file lists at least one requirement line
  grep -Eqv '^[[:space:]]*(#|$)' "$1"
}

# --- code asset -----------------------------------------------------------------------------------
log "code -> $CODE"
rm -rf "$CODE"
mkdir -p "$CODE"
rsync -a --delete \
  --exclude '__pycache__/' --exclude '*.pyc' --exclude 'tests/' --exclude 'requirements/' \
  --exclude '.pytest_cache/' --exclude 'CONTRACT_REQUEST.md' \
  "$ROOT/services/lambdas/" "$CODE/lambdas/"
rsync -a --delete --exclude '__pycache__/' --exclude '*.pyc' --exclude 'tests/' \
  "$ROOT/packages/evidence-core/evidence_core/" "$CODE/evidence_core/"

CODE_SHA="$("$PY" - "$CODE" <<'PYEOF'
import hashlib, os, sys
root = sys.argv[1]
h = hashlib.sha256()
for dirpath, dirnames, filenames in os.walk(root):
    dirnames.sort()
    for name in sorted(filenames):
        if name == "BUILD_INFO.json":
            continue
        path = os.path.join(dirpath, name)
        h.update(os.path.relpath(path, root).encode() + b"\0")
        with open(path, "rb") as fh:
            h.update(fh.read())
print(h.hexdigest())
PYEOF
)"
printf '{"codeSha": "%s", "builtAt": "%s"}\n' "$CODE_SHA" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$CODE/BUILD_INFO.json"
log "codeSha ${CODE_SHA:0:12}"

# --- layers -----------------------------------------------------------------------------------------
mkdir -p "$LAYERS"
CORE_REQ="$BUILD/lambda/core-requirements.txt"
"$PY" - "$ROOT/packages/evidence-core/pyproject.toml" > "$CORE_REQ" <<'PYEOF'
import sys, tomllib
deps = tomllib.load(open(sys.argv[1], "rb"))["project"]["dependencies"]
print("\n".join(deps))
PYEOF

declare -a LAYER_NAMES=(core ingest agent api)
layer_req() {
  case "$1" in
    core) echo "$CORE_REQ" ;;
    *) echo "$ROOT/services/lambdas/requirements/$1.txt" ;;
  esac
}

MANIFEST_LAYERS=""
for name in "${LAYER_NAMES[@]}"; do
  req="$(layer_req "$name")"
  dest="$LAYERS/$name"
  if [[ ! -f "$req" ]] || ! has_requirements "$req"; then
    log "layer $name: no requirements, skipped"
    rm -rf "$dest"
    continue
  fi
  want="$(sha_of_file "$req")"
  have="$(cat "$dest/.requirements.sha" 2>/dev/null || true)"
  if [[ "$want" == "$have" && -d "$dest/python" ]]; then
    log "layer $name: up to date"
  else
    log "layer $name: pip install (${PLATFORMS[*]}, cp$PY_VERSION)"
    rm -rf "$dest"
    mkdir -p "$dest/python"
    # boto3/botocore come with the Lambda runtime; installing them would only bloat the layer.
    "$PY" -m pip install --quiet --disable-pip-version-check \
      "${PLATFORM_ARGS[@]}" --implementation cp --python-version "$PY_VERSION" \
      --only-binary=:all: --upgrade --target "$dest/python" -r "$req"
    rm -rf "$dest/python/boto3" "$dest/python/botocore" "$dest/python/s3transfer" \
      "$dest/python"/boto3-*.dist-info "$dest/python"/botocore-*.dist-info "$dest/python"/s3transfer-*.dist-info
    find "$dest/python" -type d -name '__pycache__' -prune -exec rm -rf {} +
    echo "$want" > "$dest/.requirements.sha"
  fi
  size="$(du -sm "$dest/python" | cut -f1)"
  if (( size > 240 )); then
    echo "layer $name is ${size} MB unzipped; Lambda allows 250 MB for function + layers" >&2
    exit 1
  fi
  MANIFEST_LAYERS+="\"$name\": {\"requirementsSha\": \"$want\", \"path\": \"layers/$name\", \"sizeMb\": $size},"
done

printf '{"codeSha": "%s", "codePath": "lambda/code", "layers": {%s}}\n' \
  "$CODE_SHA" "${MANIFEST_LAYERS%,}" > "$BUILD/lambda/manifest.json"
log "manifest -> $BUILD/lambda/manifest.json"
