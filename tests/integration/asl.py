"""A small Amazon States Language interpreter for the subset ``rp-ingest`` uses, so the deployed definition
(contracts/generated/ingest_asl.json, exported from infra/lib/ingest-asl.ts) can drive the real stage handlers
in-process. Test-only; it is not a Step Functions emulator.

Supported: Task (Lambda ARN resource without Payload wrapper; ``sqs:sendMessage.waitForTaskToken``), Choice
(StringEquals, BooleanEquals, IsPresent), Parallel, Map (ItemsPath, ItemSelector, INLINE ItemProcessor), Pass,
Succeed, Fail; Parameters / ItemSelector / ResultSelector with ``.$`` JSONPath and ``$$`` context paths,
``States.JsonMerge``; ResultPath (``null``, ``$``, ``$.a.b``); Catch on ``States.ALL``. Retry is not simulated.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_MISSING = object()
_PATH_TOKEN = re.compile(r"\.([A-Za-z_][\w-]*)|\[(\d+)\]")


def get_path(data: Any, path: str) -> Any:
    if path == "$":
        return data
    if not path.startswith("$"):
        raise ValueError(f"unsupported path {path}")
    cur = data
    for m in _PATH_TOKEN.finditer(path[1:]):
        key, idx = m.group(1), m.group(2)
        if key is not None:
            if not isinstance(cur, dict) or key not in cur:
                return _MISSING
            cur = cur[key]
        else:
            i = int(idx)
            if not isinstance(cur, list) or i >= len(cur):
                return _MISSING
            cur = cur[i]
    return cur


def set_path(data: Any, path: str | None, value: Any) -> Any:
    if path is None:
        return data
    if path == "$":
        return value
    out = copy.deepcopy(data)
    keys = [m.group(1) for m in _PATH_TOKEN.finditer(path[1:])]
    cur = out
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value
    return out


class StatesError(Exception):
    def __init__(self, error: str, cause: str) -> None:
        super().__init__(f"{error}: {cause}")
        self.error, self.cause = error, cause


@dataclass
class Context:
    execution_id: str
    state_name: str = ""
    task_token: str | None = None
    map_item: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {"Execution": {"Id": self.execution_id}, "State": {"Name": self.state_name, "RetryCount": 0},
                "Task": {"Token": self.task_token}, "Map": {"Item": {"Value": self.map_item}}}


def _intrinsic(expr: str, data: Any, ctx: Context) -> Any:
    m = re.fullmatch(r"States\.JsonMerge\((.+),\s*(.+),\s*(true|false)\)", expr)
    if not m:
        raise ValueError(f"unsupported intrinsic {expr}")
    a, b = (resolve(p.strip(), data, ctx) for p in (m.group(1), m.group(2)))
    if m.group(3) == "true":
        raise ValueError("deep JsonMerge not used by rp-ingest")
    return {**a, **b}


def resolve(path: str, data: Any, ctx: Context) -> Any:
    if path.startswith("States."):
        return _intrinsic(path, data, ctx)
    value = get_path(ctx.as_dict(), "$" + path[2:]) if path.startswith("$$") else get_path(data, path)
    if value is _MISSING:
        raise StatesError("States.Runtime", f"path {path} not found in input")
    return value


def render(template: Any, data: Any, ctx: Context) -> Any:
    if isinstance(template, dict):
        out = {}
        for k, v in template.items():
            if k.endswith(".$"):
                out[k[:-2]] = resolve(v, data, ctx)
            else:
                out[k] = render(v, data, ctx)
        return out
    if isinstance(template, list):
        return [render(v, data, ctx) for v in template]
    return template


Invoker = Callable[[str, Any], Any]
"""(resource ARN or integration, input) -> output; raise to fail the task."""


@dataclass
class Interpreter:
    invoke: Invoker
    send_with_token: Callable[[dict[str, Any], str], Any]
    """(sendMessage Parameters rendered, task token) -> task output (as SendTaskSuccess output)."""
    visited: list[str] = field(default_factory=list)

    def run(self, definition: dict[str, Any], data: Any, execution_id: str) -> tuple[str, Any]:
        """Returns (status, output-or-error): SUCCEEDED/FAILED."""
        try:
            return "SUCCEEDED", self._machine(definition, data, Context(execution_id))
        except StatesError as e:
            return "FAILED", {"Error": e.error, "Cause": e.cause}

    def _machine(self, machine: dict[str, Any], data: Any, ctx: Context) -> Any:
        name = machine["StartAt"]
        states = machine["States"]
        while True:
            st = states[name]
            self.visited.append(name)
            ctx = Context(ctx.execution_id, name, None, ctx.map_item)
            kind = st["Type"]
            if kind == "Succeed":
                return data
            if kind == "Fail":
                raise StatesError(st.get("Error", "States.Fail"), st.get("Cause", ""))
            if kind == "Choice":
                name = self._choose(st, data)
                continue
            try:
                data = self._step(st, data, ctx)
            except StatesError as e:
                catch = next((c for c in st.get("Catch", []) if "States.ALL" in c["ErrorEquals"]
                              or e.error in c["ErrorEquals"]), None)
                if catch is None:
                    raise
                data = set_path(data, catch.get("ResultPath", "$"), {"Error": e.error, "Cause": e.cause})
                name = catch["Next"]
                continue
            if st.get("End"):
                return data
            name = st["Next"]

    def _choose(self, st: dict[str, Any], data: Any) -> str:
        for rule in st["Choices"]:
            value = get_path(data, rule["Variable"])
            if "IsPresent" in rule:
                ok = (value is not _MISSING) == rule["IsPresent"]
            elif "StringEquals" in rule:
                ok = value == rule["StringEquals"]
            elif "BooleanEquals" in rule:
                ok = value is rule["BooleanEquals"]
            else:
                raise ValueError(f"unsupported choice rule {rule}")
            if ok:
                return rule["Next"]
        if "Default" not in st:
            raise StatesError("States.NoChoiceMatched", json.dumps(st["Choices"]))
        return st["Default"]

    def _step(self, st: dict[str, Any], data: Any, ctx: Context) -> Any:
        kind = st["Type"]
        if kind == "Pass":
            result = render(st["Parameters"], data, ctx) if "Parameters" in st else st.get("Result", data)
        elif kind == "Task":
            params = render(st["Parameters"], data, ctx) if "Parameters" in st else data
            resource = st["Resource"]
            if resource.endswith(":sqs:sendMessage.waitForTaskToken"):
                token = f"token-{ctx.execution_id[-12:]}-{len(self.visited)}"
                ctx = Context(ctx.execution_id, ctx.state_name, token, ctx.map_item)
                params = render(st["Parameters"], data, ctx)
                result = self.send_with_token(params, token)
            else:
                try:
                    result = self.invoke(resource, json.loads(json.dumps(params)))
                except StatesError:
                    raise
                except Exception as exc:  # the Lambda error becomes the task error
                    raise StatesError(type(exc).__name__, json.dumps({"errorMessage": str(exc)})) from exc
        elif kind == "Parallel":
            result = [self._machine(b, copy.deepcopy(data), ctx) for b in st["Branches"]]
        elif kind == "Map":
            items = resolve(st["ItemsPath"], data, ctx)
            result = []
            for item in items:
                ictx = Context(ctx.execution_id, ctx.state_name, None, item)
                item_input = render(st["ItemSelector"], data, ictx) if "ItemSelector" in st else item
                result.append(self._machine(st["ItemProcessor"], item_input, ictx))
        else:
            raise ValueError(f"unsupported state type {kind}")
        if "ResultSelector" in st:
            result = render(st["ResultSelector"], result, ctx)
        return set_path(data, st.get("ResultPath", "$"), result)
