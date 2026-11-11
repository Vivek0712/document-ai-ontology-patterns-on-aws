"""SPARQL templates used by the graph_hop tool, and the one way to render them.

Template conventions:
- ``{{FROM}}`` is replaced by one ``FROM <graph>`` line per graph the caller may read.
- ``{{name}}`` placeholders are replaced by escaped SPARQL string literals (``sparql.literal``) only;
  values never become IRIs or syntax by substitution. Templates convert strings to IRIs themselves.
- Optional parameters are bound as the empty string ``""``; templates treat ``""`` as "any".
- The header declares parameters: ``# params:  name  required|optional  description``.
- Templates carry their own PREFIX lines, so the rendered text runs as-is on Neptune and pyoxigraph.

graph_hop may import :func:`render` or reimplement it; ``check.py`` runs the same rendering.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from evidence_core.models.tools import API_GRAPH_TEMPLATES, GRAPH_TEMPLATES
from evidence_core.sparql import literal

# The agent's templates and the API-only ones (rp-api; not in the agent's tool schema)
TEMPLATE_IDS: tuple[str, ...] = tuple(GRAPH_TEMPLATES) + tuple(API_GRAPH_TEMPLATES)
_PARAM_LINE = re.compile(r"^#\s+(?:params:\s+)?(?P<name>[A-Za-z][A-Za-z0-9_]*)\s+(?P<kind>required|optional)\b")
_HEADER_KEY = re.compile(r"^#\s+(?P<key>[a-z]+):")
_PLACEHOLDER = re.compile(r"\{\{(?P<name>[A-Za-z][A-Za-z0-9_]*)\}\}")
_COLUMNS = re.compile(r"^#\s+columns:\s+(?P<cols>.+)$")


class TemplateError(ValueError):
    pass


@dataclass(frozen=True)
class Template:
    template_id: str
    text: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    columns: tuple[str, ...]

    @property
    def params(self) -> tuple[str, ...]:
        return self.required + self.optional


def parse_template(template_id: str, text: str) -> Template:
    required: list[str] = []
    optional: list[str] = []
    columns: list[str] = []
    in_params = in_columns = False
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        key = _HEADER_KEY.match(line)
        if key:
            in_params = key["key"] == "params"
            in_columns = key["key"] == "columns"
        if in_params and (m := _PARAM_LINE.match(line)):
            (required if m["kind"] == "required" else optional).append(m["name"])
        if in_columns:
            body = line.split("columns:", 1)[1] if "columns:" in line else line.lstrip("#")
            columns.extend(c.strip() for c in body.split(",") if c.strip())
    body = _body(text)
    used = {m["name"] for m in _PLACEHOLDER.finditer(body)} - {"FROM"}
    declared = set(required) | set(optional)
    if used != declared:
        raise TemplateError(f"{template_id}: placeholders {sorted(used)} != declared params {sorted(declared)}")
    if "{{FROM}}" not in body:
        raise TemplateError(f"{template_id}: no {{{{FROM}}}} placeholder")
    return Template(template_id, text, tuple(required), tuple(optional), tuple(columns))


def _body(text: str) -> str:
    """Query text without comment lines (placeholders in comments are documentation only)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def load_templates(directory: Path) -> dict[str, Template]:
    out = {}
    for path in sorted(directory.glob("*.rq")):
        out[path.stem] = parse_template(path.stem, path.read_text(encoding="utf-8"))
    return out


def from_clause(graphs: Iterable[str]) -> str:
    lines = []
    for g in graphs:
        if not re.fullmatch(r"(urn|https?):[^\s<>\"{}|\\^`]+", g):
            raise TemplateError(f"invalid graph IRI {g!r}")
        lines.append(f"FROM <{g}>")
    if not lines:
        raise TemplateError("at least one graph is required")
    return "\n".join(lines)


def render(template: Template, graphs: Iterable[str], params: Mapping[str, str] | None = None) -> str:
    """Bind graphs and parameters. Unknown or missing-required parameters raise TemplateError."""
    params = dict(params or {})
    unknown = set(params) - set(template.params)
    if unknown:
        raise TemplateError(f"{template.template_id}: unknown params {sorted(unknown)}")
    missing = [p for p in template.required if not str(params.get(p, "")).strip()]
    if missing:
        raise TemplateError(f"{template.template_id}: missing required params {missing}")
    values = {p: literal(str(params.get(p, ""))) for p in template.params}
    graph_lines = from_clause(graphs)
    out = []
    for line in template.text.splitlines():
        if not line.lstrip().startswith("#"):
            line = _PLACEHOLDER.sub(lambda m: graph_lines if m["name"] == "FROM" else values[m["name"]], line)
        out.append(line)
    return "\n".join(out) + "\n"
