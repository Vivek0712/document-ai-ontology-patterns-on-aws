"""Build the TBox named graphs (N-Quads), the vocab snapshot and the template bundle.

Output layout (LocalConfigSource maps ``config/ontology/`` onto ``ontology/dist/``)::

    dist/{ontologyVersion}/nquads/{fibo,doco,rp}.nq   one named graph each (iris.TBOX_GRAPHS)
    dist/{ontologyVersion}/vocab.json                  units.VocabSnapshot (source "build")
    dist/{ontologyVersion}/templates/{templateId}.rq   SPARQL templates used by graph_hop
    dist/{ontologyVersion}/manifest.json               sources, triple counts, sha256 per file

Output is deterministic: blank nodes get canonical labels (RGDA1, rdflib.compare) and lines are sorted,
so a rebuild without source changes is byte-identical.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evidence_core.enums import CanonicalRole
from evidence_core.hashing import sha256_bytes
from evidence_core.iris import PREFIXES, TBOX_GRAPHS
from evidence_core.models.units import VocabDocType, VocabFieldConcept, VocabRole, VocabSnapshot
from rdflib import RDF, Graph, Literal, Namespace, URIRef
from rdflib.compare import to_canonical_graph
from rdflib.namespace import OWL, RDFS, SKOS

from .templates import TEMPLATE_IDS, load_templates

ONT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ONT_DIR / "src"
DIST_DIR = ONT_DIR / "dist"
TEMPLATES_DIR = ONT_DIR / "templates"

DOC = Namespace(PREFIXES["doc"])
FIN = Namespace(PREFIXES["fin"])
RP = Namespace(PREFIXES["rp"])
DOCO = PREFIXES["doco"]


@dataclass
class Manifest:
    ontology_version: str
    release_date: str
    graphs: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, src_dir: Path = SRC_DIR) -> Manifest:
        data = json.loads((src_dir / "manifest.json").read_text(encoding="utf-8"))
        if set(data["graphs"]) != set(TBOX_GRAPHS):
            raise ValueError(f"manifest graphs {sorted(data['graphs'])} != {sorted(TBOX_GRAPHS)}")
        return cls(data["ontologyVersion"], data["releaseDate"], data["graphs"])


@dataclass
class BuildResult:
    ontology_version: str
    out_dir: Path
    triples: dict[str, int] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)  # relative path -> sha256
    vocab: VocabSnapshot | None = None


def _format_for(path: Path) -> str:
    return {".ttl": "turtle", ".rdf": "xml", ".owl": "xml", ".nt": "nt"}[path.suffix]


def load_source_graph(key: str, manifest: Manifest, src_dir: Path = SRC_DIR) -> Graph:
    g = Graph()
    for rel in manifest.graphs[key]["files"]:
        path = src_dir / rel
        g.parse(path, format=_format_for(path))
    return g


def stamp_version(g: Graph, key: str, manifest: Manifest) -> None:
    """Version node per TBox graph: ``<graph IRI> a owl:Ontology ; owl:versionInfo "<ontologyVersion>"``.

    The project's own ontology nodes in the rp graph get the same owl:versionInfo.
    """
    node = URIRef(TBOX_GRAPHS[key])
    meta = manifest.graphs[key]
    ver = Literal(manifest.ontology_version)
    g.add((node, RDF.type, OWL.Ontology))
    g.add((node, OWL.versionInfo, ver))
    g.add((node, RP.ontologyVersion, ver))
    g.add((node, RDFS.label, Literal(meta["label"])))
    g.add((node, RP.pinnedSource, Literal(meta["pinnedSource"])))
    if key == "rp":
        for ont in ("urn:rp:ont:doc", "urn:rp:ont:fin"):
            g.set((URIRef(ont), OWL.versionInfo, ver))


def to_nquads(g: Graph, graph_iri: str) -> str:
    """Canonical blank-node labels, sorted N-Quads lines in one named graph."""
    canon = to_canonical_graph(g)
    nt = canon.serialize(format="nt", encoding="utf-8").decode("utf-8")
    lines = []
    for line in nt.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.endswith(" ."):
            raise ValueError(f"unexpected N-Triples line: {line[:120]}")
        lines.append(f"{line[:-2]} <{graph_iri}> .")
    lines.sort()
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- vocab snapshot


def curie(iri: URIRef | str) -> str:
    s = str(iri)
    best = ""
    for prefix, ns in PREFIXES.items():
        if s.startswith(ns) and len(ns) > len(best):
            best = ns
            name = prefix
    return f"{name}:{s[len(best):]}" if best else s


def _literal(g: Graph, s: URIRef, p: URIRef) -> str | None:
    vals = sorted(str(o) for o in g.objects(s, p))
    return vals[0] if vals else None


def _literals(g: Graph, s: URIRef, p: URIRef) -> list[str]:
    return sorted(str(o) for o in g.objects(s, p))


def build_vocab(rp_graph: Graph, manifest: Manifest) -> VocabSnapshot:
    g = rp_graph
    engine_schemes = {s: str(e) for s, e in g.subject_objects(RP.engine)}
    roles: list[VocabRole] = []
    for concept in sorted(g.subjects(SKOS.inScheme, DOC.RoleScheme)):
        code = _literal(g, concept, RP.canonicalRole)
        if code is None:
            raise ValueError(f"role {concept} has no rp:canonicalRole")
        engine_labels: dict[str, list[str]] = {}
        for scheme, engine in sorted(engine_schemes.items()):
            notations = [
                str(n)
                for c in g.subjects(SKOS.exactMatch, concept)
                if (c, SKOS.inScheme, scheme) in g
                for n in g.objects(c, SKOS.notation)
            ]
            engine_labels[engine] = sorted(notations)
        doco = [o for p in (SKOS.exactMatch, SKOS.closeMatch) for o in g.objects(concept, p)
                if str(o).startswith(DOCO)]
        roles.append(VocabRole(
            role=CanonicalRole(code), concept=curie(concept), pref_label=_literal(g, concept, SKOS.prefLabel) or code,
            doco=curie(doco[0]) if doco else None, engine_labels=engine_labels,
            derived=(concept, RP.derived, Literal(True)) in g,
        ))
    doc_types = [
        VocabDocType(
            concept=curie(c), pref_label=_literal(g, c, SKOS.prefLabel) or curie(c),
            short_label=_literal(g, c, RP.shortLabel) or curie(c), alt_labels=_literals(g, c, SKOS.altLabel),
            indicators=_literals(g, c, RP.indicator), period_kind=_literal(g, c, RP.periodKind) or "none",
        )
        for c in sorted(g.subjects(SKOS.inScheme, DOC.DocTypeScheme))
    ]
    fields = []
    for c in sorted(g.subjects(SKOS.inScheme, FIN.FieldConceptScheme)):
        dt = g.value(c, RP.docType)
        if dt is None:
            raise ValueError(f"field concept {c} has no rp:docType")
        fields.append(VocabFieldConcept(
            concept=curie(c), pref_label=_literal(g, c, SKOS.prefLabel) or curie(c),
            alt_labels=_literals(g, c, SKOS.altLabel), notation=_literal(g, c, SKOS.notation), doc_type=curie(dt),
            datatype=_literal(g, c, RP.datatype) or "decimal", unit_group=_literal(g, c, RP.unitGroup),
        ))
    return VocabSnapshot(ontology_version=manifest.ontology_version, generated_at=manifest.release_date,
                         source="build", roles=roles, doc_types=doc_types, field_concepts=fields)


# --------------------------------------------------------------------------- build


def build(src_dir: Path = SRC_DIR, dist_dir: Path = DIST_DIR, templates_dir: Path = TEMPLATES_DIR) -> BuildResult:
    manifest = Manifest.load(src_dir)
    out = dist_dir / manifest.ontology_version
    if out.exists():
        shutil.rmtree(out)
    (out / "nquads").mkdir(parents=True)
    (out / "templates").mkdir()
    result = BuildResult(ontology_version=manifest.ontology_version, out_dir=out)

    rp_graph: Graph | None = None
    for key, graph_iri in TBOX_GRAPHS.items():
        g = load_source_graph(key, manifest, src_dir)
        stamp_version(g, key, manifest)
        if key == "rp":
            rp_graph = g
        text = to_nquads(g, graph_iri)
        path = out / "nquads" / f"{key}.nq"
        path.write_text(text, encoding="utf-8")
        result.triples[key] = text.count("\n")
    assert rp_graph is not None

    vocab = build_vocab(rp_graph, manifest)
    (out / "vocab.json").write_text(json.dumps(vocab.dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    result.vocab = vocab

    templates = load_templates(templates_dir)
    missing = set(TEMPLATE_IDS) - set(templates)
    if missing:
        raise ValueError(f"missing templates: {sorted(missing)}")
    for tid, tpl in sorted(templates.items()):
        (out / "templates" / f"{tid}.rq").write_text(tpl.text, encoding="utf-8")

    for path in sorted(p for p in out.rglob("*") if p.is_file()):
        result.files[str(path.relative_to(out))] = sha256_bytes(path.read_bytes())
    build_manifest = {
        "ontologyVersion": manifest.ontology_version,
        "generatedAt": manifest.release_date,
        "graphs": {k: {"graphIri": TBOX_GRAPHS[k], "triples": result.triples[k], **manifest.graphs[k]}
                   for k in TBOX_GRAPHS},
        "templates": sorted(templates),
        "files": result.files,
    }
    (out / "manifest.json").write_text(json.dumps(build_manifest, indent=2) + "\n", encoding="utf-8")
    # the explorer's TBox export, read back from the N-Quads just written (W-09; not in the manifest's file list,
    # which hashes what the graph loads)
    from .tbox_export import write as write_tbox

    write_tbox(out)
    return result
