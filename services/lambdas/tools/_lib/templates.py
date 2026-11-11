"""SPARQL template rendering for graph_hop (templates owned by A: ontology/templates/*.rq).

Template syntax (the only substitutions graph_hop performs; nothing else from the model
reaches the query text):

    {{FROM}}             FROM <graph> lines for the ACL-selected graphs (required)
    {{name}}             parameter as an escaped string literal  "..."
    {{name:iri}}         parameter as an IRI: a CURIE with a known prefix (fin:W2WagesBox1) or
                         an urn:rp: IRI, expanded and validated, written <...>
    {{name:unit}}        a unitId written as its unit IRI <urn:rp:unit:...> (unitId validated)
    {{name:year}}        four digits, written "2025"^^xsd:gYear
    {{#name}} ... {{/name}}  section kept only when the optional parameter is given

An optional parameter that is not given renders as the empty literal ``""`` in a plain
``{{name}}`` placeholder: A's templates read ``""`` as "any" (``FILTER({{borrower}} = "" || ...)``).
Placeholders on ``#`` comment lines are left as written. A parameter the template text does not
reference is rejected, so the model can never believe it filtered on something that was ignored.

A template may declare its own PREFIX lines; when it declares none the standard block
from ``iris.sparql_prefix_block()`` is prepended.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from evidence_core.ids import UNIT_ID_RE
from evidence_core.iris import PREFIXES, expand, sparql_prefix_block, unit_iri
from evidence_core.sparql import literal

from .errors import BadRequest


@dataclass(frozen=True)
class TemplateSpec:
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()


# Upper bound on what each approved template may take; the effective set is the intersection with the
# placeholders the template text actually uses (A's v1 templates: qi_sum {taxYear}, field_lookup
# {concept, borrower}).
TEMPLATE_SPECS: dict[str, TemplateSpec] = {
    "qi_sum_w2_box1_v1": TemplateSpec(optional=("taxYear", "borrower")),
    "field_lookup_v1": TemplateSpec(required=("concept",), optional=("borrower", "taxYear")),
    "concept_by_label_v1": TemplateSpec(required=("label",)),
    "unit_lineage_v1": TemplateSpec(required=("unitId",)),
    "coupon_terms_v1": TemplateSpec(required=("security",)),
    # API-only (models/tools.py API_GRAPH_TEMPLATES): rp-api's W-09/W-11 neighbourhood, never the agent's
    "neighbourhood_v1": TemplateSpec(required=("iri",), optional=("depth",)),
}

MAX_PARAM_CHARS = 200
_SECTION = re.compile(r"\{\{#(\w+)\}\}(.*?)\{\{/\1\}\}", re.S)
_PLACEHOLDER = re.compile(r"\{\{(\w+)(?::(\w+))?\}\}")
_CURIE = re.compile(r"^[A-Za-z][\w-]*:[A-Za-z0-9_./#-]+$")
_URN = re.compile(r"^urn:rp:[A-Za-z0-9_:./#-]+$")
_YEAR = re.compile(r"^\d{4}$")


def _iri(value: str) -> str:
    if _URN.match(value):
        return f"<{value}>"
    if _CURIE.match(value) and value.split(":", 1)[0] in PREFIXES:
        return f"<{expand(value)}>"
    raise BadRequest(f"{value!r} is not a known CURIE or urn:rp: IRI")


def _render_value(name: str, kind: str | None, value: str) -> str:
    if kind in (None, "lit"):
        return literal(value)
    if kind == "iri":
        return _iri(value)
    if kind == "unit":
        if not UNIT_ID_RE.match(value):
            raise BadRequest(f"{name}: invalid unitId {value!r}")
        return f"<{unit_iri(value)}>"
    if kind == "year":
        if not _YEAR.match(value):
            raise BadRequest(f"{name}: expected a four-digit year, got {value!r}")
        return f'"{value}"^^<http://www.w3.org/2001/XMLSchema#gYear>'
    raise BadRequest(f"template uses unknown placeholder kind {kind!r}")


def _strip_comments(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def template_params(text: str) -> set[str]:
    """Parameter names a template references (placeholders and sections), comments excluded."""
    body = _strip_comments(text)
    names = {m.group(1) for m in _PLACEHOLDER.finditer(body)} | {m.group(1) for m in _SECTION.finditer(body)}
    return names - {"FROM"}


def render(template_id: str, text: str, graphs: list[str], params: dict[str, str]) -> str:
    spec = TEMPLATE_SPECS.get(template_id)
    if spec is None:
        raise BadRequest(f"template {template_id!r} is not approved")
    allowed = (set(spec.required) | set(spec.optional)) & template_params(text)
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise BadRequest(f"{template_id} does not take parameters {unknown}; allowed: {sorted(allowed)}")
    missing = [p for p in spec.required if not params.get(p)]
    if missing:
        raise BadRequest(f"{template_id} needs parameters {missing}")
    for k, v in params.items():
        if len(v) > MAX_PARAM_CHARS:
            raise BadRequest(f"parameter {k} longer than {MAX_PARAM_CHARS} characters")
    given = {k: v for k, v in params.items() if v not in (None, "")}

    def section(m: re.Match[str]) -> str:
        return m.group(2) if m.group(1) in given else ""

    body = _SECTION.sub(section, text)
    from_lines = "\n".join(f"FROM <{g}>" for g in graphs)
    if "{{FROM}}" not in body:
        raise BadRequest(f"template {template_id} has no {{{{FROM}}}} clause")

    def placeholder(m: re.Match[str]) -> str:
        name, kind = m.group(1), m.group(2)
        if name == "FROM":
            return from_lines
        if name not in given:
            if name in spec.optional and kind in (None, "lit"):
                return literal("")  # "" = any (A's template rule)
            raise BadRequest(f"template {template_id} needs parameter {name!r}")
        return _render_value(name, kind, given[name])

    rendered = "\n".join(line if line.lstrip().startswith("#") else _PLACEHOLDER.sub(placeholder, line)
                          for line in body.split("\n"))
    if "PREFIX" not in rendered.upper().split("SELECT", 1)[0]:
        rendered = sparql_prefix_block() + rendered
    return rendered
