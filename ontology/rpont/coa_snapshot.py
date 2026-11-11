"""Read a recorded COA session (``runs/local/coa/<date>/``) into the governance record's ``coa.snapshot``.

COA (the AWS Context Ontology Accelerator) holds a governed copy of our ``fin:`` ontology and a governed metric,
``qualifying_income``, that recomputes "total income" independently of our graph. This build never calls COA: a
recorded session is replayed. Every value below is read from a captured request/response file; none is typed here.
The files redact the caller's e-mail and COA's object ids (``manifest.json`` lists the placeholders).

Files read (names as captured):
- ``02_namespace.json``: the namespace and the day of the session
- ``03_ontology_ingest.json``, ``04_ontologies_list.json``, ``06_graph_class_qualifying_income.json``: the uploaded
  Turtle, its sha256, its ontology type (``user_uploaded``: COA has no steward review for uploaded Turtle), the
  concept's superclass and operand
- ``11_metric_get.json``: the metric card (label, description, SQL, ontology concepts, ``definedBy``,
  ``effectiveFrom`` as COA's server set them)
- ``12_query_rest_tier1.json``, ``17_mcp_query_tier1.json``: the Tier-1 recomputation (value, confidence, trace,
  repeats) over REST and over MCP
- ``18_mcp_negative_auth.json``: the token rule (only an ID token issued to COA's MCP client is accepted)
- ``19_ontology_export.json``, ``20_export_osi.json``: the export hashes
- ``21_latency_summary.json``: measured client latencies
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REDACTED = "<EMAIL>"


def _read(folder: Path, name: str) -> Any:
    return json.loads((folder / name).read_text(encoding="utf-8"))


def _trace(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"step": s["step"].split(".", 1)[-1], "status": s["status"], "tool": s.get("toolUsed"),
             "engine": (s.get("detail") or {}).get("engine"), "durationMs": s.get("durationMs")} for s in steps]


def _latency(summary: dict[str, Any], basis: str) -> dict[str, Any]:
    calls = {"rest_get_metric": "REST GET metric", "rest_query_tier1": "REST query · Tier 1 (execute)",
             "serve_internal_totalMs_tier1": "inside COA (resolver + Athena)", "mcp_list_metrics": "MCP list_metrics",
             "mcp_query_tier1": "MCP query · Tier 1"}
    return {"measured": True, "basis": basis,
            "calls": [{"call": label, "n": summary[k]["n"], "medianMs": summary[k]["median_ms"],
                       "minMs": summary[k]["min_ms"], "maxMs": summary[k]["max_ms"]}
                      for k, label in calls.items() if k in summary]}


def read_snapshot(folder: Path, concept: str) -> dict[str, Any]:
    """The ``CoaSnapshot`` (wire form) of one recorded session, for the governed ``concept``."""
    manifest = _read(folder, "manifest.json")
    ns = _read(folder, "02_namespace.json")["response"]["namespace"]
    ingest = _read(folder, "03_ontology_ingest.json")
    onto = next(o for o in _read(folder, "04_ontologies_list.json")["response"]["ontologies"]
                if o["ontologyId"] == ingest["upload_url"]["request"]["body"]["ontologyId"])
    cls = _read(folder, "06_graph_class_qualifying_income.json")["response"]
    metric = _read(folder, "11_metric_get.json")["response"]["metric"]
    rest = _read(folder, "12_query_rest_tier1.json")
    mcp = _read(folder, "17_mcp_query_tier1.json")
    refused = _read(folder, "18_mcp_negative_auth.json")
    export = _read(folder, "19_ontology_export.json")
    osi = _read(folder, "20_export_osi.json")
    latency = _read(folder, "21_latency_summary.json")

    if concept not in metric.get("ontologyConcepts", []):
        raise ValueError(f"the recorded metric {metric['name']!r} does not govern {concept}")
    result = rest["response"]["result"]
    name = metric["name"]
    value = result["resultRows"][0][name]
    repeats = rest.get("repeat_runs", [])
    dims = {d["name"]: d["value"] for d in rest["request"]["body"].get("dimensions", [])}
    mcp_values = [r["rows"][0][name] for r in mcp.get("repeat_runs", []) if r.get("rows")]
    dialect = metric["expression"]["dialects"][0]
    edges = cls.get("edges", [])
    roundtrip = export["roundtrip"]
    return {
        "capturedAt": ns["createdAt"][:10],
        "coaVersion": manifest.get("coaVersion"),
        "namespace": ns["name"],
        "region": manifest.get("region"),
        "metric": {
            "name": name, "label": metric.get("label"), "description": metric.get("description"),
            "dialect": dialect["dialect"], "sql": dialect["expression"], "unit": metric.get("unit"),
            "returnType": metric.get("returnType"), "defaultTimeGrain": metric.get("defaultTimeGrain"),
            "synonyms": (metric.get("aiContext") or {}).get("synonyms", []),
            "ontologyConcepts": metric.get("ontologyConcepts", []), "sourceTable": metric.get("sourceTable"),
            "definedBy": metric["definedBy"], "definedByRedacted": metric["definedBy"] == REDACTED,
            "effectiveFrom": metric.get("effectiveFrom"),
        },
        "ontology": {
            "concept": concept, "ontologyId": onto["ontologyId"], "ontologyType": onto["ontologyType"],
            "stewardReviewed": False,  # COA ingests uploaded Turtle directly; only induction proposals are reviewed
            "label": "uploaded, not steward-reviewed",
            "subClassOf": [e["neighbor"]["uri"] for e in edges if e["predicate_label"] == "subClassOf"],
            "operandConcepts": [e["neighbor"]["uri"] for e in edges if e["predicate"].endswith(":operandConcept")],
            "classCount": onto.get("classCount"), "axiomCount": onto.get("axiomCount"),
        },
        "crossCheck": {
            "metric": name, "tier": int(result["tier"]), "confidence": float(result["confidence"]["score"]),
            "rationale": result["confidence"].get("rationale"), "value": value, "dimensions": dims,
            "trace": _trace(result["trace"]), "runs": len(repeats) or 1,
            "identicalRuns": sum(1 for r in repeats if r["rows"] == result["resultRows"]) if repeats else 1,
            "mcpValue": mcp_values[0] if mcp_values and len(set(mcp_values)) == 1 else None,
            "mcpRuns": len(mcp_values),
        },
        "hashes": [
            {"name": "fin.ttl as uploaded", "sha256": ingest["source_file"]["sha256"],
             "detail": f"{ingest['source_file']['triples']} triples · {ingest['source_file']['bytes']} bytes"},
            {"name": "COA ontology export", "sha256": export["export_sha256"],
             "detail": f"{roundtrip['common']} of {roundtrip['source_triples']} source triples kept"},
            {"name": "OSI metric export", "sha256": osi["sha256_of_response_json_sorted"],
             "detail": "same on 2 calls" if osi.get("stable_across_2_calls") else None},
        ],
        "latency": _latency(latency, manifest.get("latencyBasis") or latency.get("note", "")),
        "access": {
            "rule": "COA's MCP server accepts only an ID token issued to its MCP client; any other token is refused.",
            "refused": [{"token": v["token"].replace("_", " "), "status": v["http_status"],
                         "message": json.loads(v["body"]).get("error", {}).get("message")} for v in refused.values()],
        },
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*.json"))},
    }
