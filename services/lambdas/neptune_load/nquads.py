"""Tiny N-Quads reader that turns lines into SPARQL ``INSERT DATA`` batches.

N-Triples term syntax is valid SPARQL term syntax, so a triple line minus its graph term
can be placed inside ``GRAPH <g> { ... }`` unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

_TERM = re.compile(r'<[^>]*>|_:[^\s]+|"(?:[^"\\]|\\.)*"(?:@[A-Za-z0-9-]+|\^\^<[^>]*>)?')


@dataclass(frozen=True)
class Quad:
    subject: str
    predicate: str
    obj: str
    graph: str | None  # IRI without <>

    @property
    def triple(self) -> str:
        return f"{self.subject} {self.predicate} {self.obj} ."


def parse_line(line: str) -> Quad | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if not text.endswith("."):
        raise ValueError(f"N-Quads line does not end with '.': {text[:120]}")
    terms = _TERM.findall(text[:-1])
    if len(terms) not in (3, 4):
        raise ValueError(f"expected 3 or 4 terms, got {len(terms)}: {text[:120]}")
    graph = terms[3][1:-1] if len(terms) == 4 else None
    return Quad(terms[0], terms[1], terms[2], graph)


def parse(text: str) -> Iterator[Quad]:
    for line in text.splitlines():
        q = parse_line(line)
        if q is not None:
            yield q


def insert_batches(quads: Iterable[Quad], default_graph: str, batch_size: int = 500) -> Iterator[tuple[str, int]]:
    """Yield ``(INSERT DATA update, triple count)`` per batch, grouped by graph."""
    by_graph: dict[str, list[str]] = {}
    for q in quads:
        by_graph.setdefault(q.graph or default_graph, []).append(q.triple)
    for graph, triples in by_graph.items():
        for i in range(0, len(triples), batch_size):
            chunk = triples[i : i + batch_size]
            yield "INSERT DATA { GRAPH <" + graph + "> {\n" + "\n".join(chunk) + "\n} }", len(chunk)
