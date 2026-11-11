"""contracts/api.ts mirrors the Python models (code wins, api.ts mirrors it for the UI), and the
REST routes agree across CDK (infra/lib/api-routes.ts), the UI client (usecases/provenant/ui/src/api/http.ts)
and the documented route table (docs/reference/rest-api.md)."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from pathlib import Path

import evidence_core.models as models_pkg
import pytest
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[2]
TS = (ROOT / "contracts" / "api.ts").read_text()


def _ts_interfaces() -> dict[str, tuple[set[str], list[str]]]:
    out = {}
    for m in re.finditer(r"export interface (\w+)(?: extends ([\w, <>]+))? \{(.*?)\n\}", TS, re.S):
        fields = set(re.findall(r"^  (\w+)\??:", m.group(3), re.M))
        out[m.group(1)] = (fields, re.findall(r"\w+", m.group(2) or ""))
    return out


IFACES = _ts_interfaces()


def _ts_fields(name: str) -> set[str]:
    fields, parents = IFACES[name]
    out = set(fields)
    for p in parents:
        if p in IFACES:
            out |= _ts_fields(p)
    return out


def _models() -> dict[str, type[BaseModel]]:
    found: dict[str, type[BaseModel]] = {}
    for info in pkgutil.iter_modules(models_pkg.__path__):
        mod = importlib.import_module(f"evidence_core.models.{info.name}")
        for name, cls in inspect.getmembers(mod, inspect.isclass):
            if issubclass(cls, BaseModel) and cls.__module__ == mod.__name__:
                found[name] = cls
    return found


MIRRORED = sorted(n for n in _models() if n in IFACES and n != "ApiError")  # ApiError body is inline in TS


def test_ui_facing_models_are_mirrored() -> None:
    for name in ("PackageStatus", "StageRecord", "StageDetail", "Lineage", "RegionResult", "RunRecord", "AskRequest",
                 "AnswerPayload", "UnitView", "ArtifactView", "UploadUrlResponse"):
        assert name in MIRRORED, name


@pytest.mark.parametrize("name", MIRRORED)
def test_fields_match(name: str) -> None:
    cls = _models()[name]
    py = {f.alias or k for k, f in cls.model_fields.items()}
    assert py == _ts_fields(name), f"{name}: python-only {sorted(py - _ts_fields(name))}, " \
                                   f"ts-only {sorted(_ts_fields(name) - py)}"


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}|\$\{[^}]+\}", "{}", path.split("?")[0].split("${qs")[0])


def test_rest_routes_agree() -> None:
    cdk = {(m, _norm(p)) for m, p in re.findall(r"method: '(\w+)', path: '([^']+)'",
                                                  (ROOT / "infra/lib/api-routes.ts").read_text())}
    contract = {(m, _norm(p)) for m, p in re.findall(r"^\| (GET|POST|PUT|DELETE) \| `([^`]+)`",
                                                      (ROOT / "docs/reference/rest-api.md").read_text(), re.M)}
    ui_src = (ROOT / "usecases/provenant/ui/src/api/http.ts").read_text()
    ui = {(m, _norm(p)) for m, p in re.findall(r'request<\w+>\("(\w+)", [`"]([^`"]+)[`"]', ui_src)}
    ui.add(("POST", "/ask"))
    # v2 routes whose handler exists but whose CDK target is not wired yet ("v2" rows)
    pending = {(m, _norm(p)) for m, p in re.findall(r"^\| v2 \| (GET|POST|DELETE) \| `([^`]+)`",
                                                     (ROOT / "docs/reference/rest-api.md").read_text(), re.M)}
    assert cdk == contract, (sorted(cdk - contract), sorted(contract - cdk))
    assert not pending & cdk, f"wired in CDK: move these rows into the v1 table {sorted(pending & cdk)}"
    assert ui <= cdk | pending, sorted(ui - cdk - pending)
