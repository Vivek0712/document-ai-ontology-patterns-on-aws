"""Packaging integration checks: every Lambda declared in infra/lib/functions.ts resolves to a real handler
module, and every third-party module it imports at load time is provided by the Lambda runtime or by one of
the layers the function is given.

The layer contents are read from build/layers/<name>/python (scripts/build_lambdas.sh); when the layers have not
been built the import check is skipped, the handler check still runs.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAMBDAS = ROOT / "services" / "lambdas"
COMMON = ROOT / "packages" / "evidence-core" / "evidence_core"
FUNCTIONS_TS = ROOT / "infra" / "lib" / "functions.ts"
LAYERS = ROOT / "build" / "layers"

# Provided by the python3.12 Lambda runtime.
RUNTIME = {"boto3", "botocore", "s3transfer", "jmespath", "dateutil", "urllib3", "six", "awslambdaric"}
LAYER_ALIASES = {"ING": ["core", "ingest"], "AGT": ["core", "agent"], "API": ["core", "api"]}


def _specs() -> list[dict[str, object]]:
    text = FUNCTIONS_TS.read_text()
    out = []
    for m in re.finditer(r"\{\s*dir: '([^']+)'(.*?)\n  \},", text, re.S):
        body = m.group(2)
        lm = re.search(r"layers: (\w+|\[[^\]]*\])", body)
        assert lm, f"no layers for {m.group(1)}"
        raw = lm.group(1)
        layers = LAYER_ALIASES[raw] if raw in LAYER_ALIASES else re.findall(r"'(\w+)'", raw)
        hm = re.search(r"handler: '([^']+)'", body)
        out.append({"dir": m.group(1), "layers": layers, "handler": hm.group(1) if hm else None})
    return out


SPECS = _specs()


def _module_path(module: str) -> Path | None:
    parts = module.split(".")
    if parts[0] == "lambdas":
        base = LAMBDAS.joinpath(*parts[1:])
    elif parts[0] == "evidence_core":
        base = COMMON.joinpath(*parts[1:])
    else:
        return None
    if base.with_suffix(".py").exists():
        return base.with_suffix(".py")
    if (base / "__init__.py").exists():
        return base / "__init__.py"
    return None


def _top_level_imports(path: Path, module: str) -> set[str]:
    """Module-level imports (incl. inside module-level try/if), resolved to absolute names."""
    tree = ast.parse(path.read_text())
    pkg = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    found: set[str] = set()

    def visit(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                found.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = pkg.split(".")
                    base = base[: len(base) - (node.level - 1)]
                    target = ".".join(base + ([node.module] if node.module else []))
                else:
                    target = node.module or ""
                found.add(target)
                for a in node.names:  # `from pkg import submodule`
                    found.add(f"{target}.{a.name}")
            elif isinstance(node, ast.If | ast.Try):
                visit(node.body)
                visit(getattr(node, "orelse", []))
                for h in getattr(node, "handlers", []):
                    visit(h.body)
                visit(getattr(node, "finalbody", []))

    visit(tree.body)
    return found


def _closure(module: str) -> set[str]:
    """Third-party top-level package names imported (transitively) when ``module`` loads."""
    seen: set[str] = set()
    third: set[str] = set()
    stack = [module]
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        # importing a.b.c imports a and a.b first
        parts = mod.split(".")
        for i in range(1, len(parts)):
            stack.append(".".join(parts[:i]))
        top = parts[0]
        if top in ("lambdas", "evidence_core"):
            path = _module_path(mod)
            if path is not None:
                stack.extend(_top_level_imports(path, mod))
            continue
        if top == "__future__" or top in sys.stdlib_module_names:
            continue
        third.add(top)
    return third


def _layer_modules(name: str) -> set[str] | None:
    d = LAYERS / name / "python"
    if not d.is_dir():
        return None
    mods = set()
    for p in d.iterdir():
        if p.name.endswith((".dist-info", ".egg-info")) or p.name in ("bin", "__pycache__"):
            continue
        mods.add(p.name.split(".")[0] if p.is_file() else p.name)
    return mods


def test_specs_parsed() -> None:
    assert len(SPECS) >= 27
    assert {"tools/graph_hop", "eu_builder", "api", "api/ask"} <= {s["dir"] for s in SPECS}


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s["dir"])
def test_handler_module_exists(spec: dict[str, object]) -> None:
    handler = spec["handler"] or f"lambdas.{str(spec['dir']).replace('/', '.')}.handler.handler"
    module, func = str(handler).rsplit(".", 1)
    path = _module_path(module)
    assert path is not None, f"{handler}: module file missing"
    tree = ast.parse(path.read_text())
    names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}
    names |= {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    assert func in names, f"{handler}: no top-level `{func}` in {path}"


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s["dir"])
def test_layers_cover_imports(spec: dict[str, object]) -> None:
    available = set(RUNTIME)
    for layer in spec["layers"]:  # type: ignore[union-attr]
        mods = _layer_modules(layer)
        if mods is None:
            if layer == "api":  # no packages listed -> no layer by design
                continue
            pytest.skip(f"layer {layer} not built (scripts/build_lambdas.sh)")
        available |= mods
    module = f"lambdas.{str(spec['dir']).replace('/', '.')}.handler"
    missing = sorted(_closure(module) - available)
    assert not missing, f"{spec['dir']} imports {missing} at load time; layers {spec['layers']} do not provide them"
