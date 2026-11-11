"""The Git-governed definition record (SC-8 fallback card): ``dist/{ver}/governance/{name}.json``.

A Lambda cannot run Git or the ontology checks, so the build records them: ``make ontology-check`` writes this file
after ``check.py``, ``make seed`` publishes it with the rest of the bundle (``config/ontology/{ver}/``), and
``GET /governance/definitions/{name}`` serves it (``GovernedDefinitionView``). Every value is read, none typed:
- the term from the **built** TBox (``nquads/rp.nq``, what Neptune loads): label, ``rdfs:comment``, superclasses,
  alt labels, operand concepts, ``rp:computedBy``;
- the Turtle source and the template file, by sha256;
- Git: the last commit that touched ``fin.ttl`` (None when there is none yet);
- the review record: None until the change is merged through a reviewed pull request (``RP_GOVERNANCE_REVIEW``
  names the reviewer and PR URL when it is);
- ``make ontology-check``: pass/fail per check, run here on the same dist;
- COA (``coaMode``): ``off`` leaves the COA slot ``not-captured``; ``snapshot`` fills it from a recorded COA session
  (``runs/local/coa/<date>/``, read by ``coa_snapshot.py``), labelled with its capture date. Nothing calls COA.

    cd ontology && python -m rpont.governance [dist/1.2.0] [--name qualifying_income] [--coa-mode off|snapshot]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyoxigraph as ox

from .build_lib import DIST_DIR, SRC_DIR, Manifest

REPO = SRC_DIR.parents[1]
FIN = "urn:rp:ont:fin:"
RP = "urn:rp:ont:rp:"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
METRICS = {"qualifying_income": f"{FIN}QualifyingIncome"}  # metric name -> the fin: class that defines it
COA_SNAPSHOTS = REPO / "runs" / "local" / "coa"
COA_MODES = ("off", "snapshot")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_last_commit(path: Path) -> dict[str, str] | None:
    git = shutil.which("git")
    if git is None:
        return None
    out = subprocess.run([git, "log", "-1", "--format=%H|%an|%cI", "--", str(path)],  # noqa: S603 (fixed argv)
                         cwd=REPO, capture_output=True, text=True, check=False)
    parts = out.stdout.strip().split("|") if out.returncode == 0 and out.stdout.strip() else None
    return {"commit": parts[0], "author": parts[1], "committedAt": parts[2]} if parts else None


def _review() -> dict[str, str] | None:
    raw = os.environ.get("RP_GOVERNANCE_REVIEW")  # "reviewer|https://…/pull/12|2026-12-01T10:00:00Z"
    if not raw:
        return None
    reviewer, _, rest = raw.partition("|")
    url, _, at = rest.partition("|")
    return {"reviewer": reviewer, "url": url or None, "approvedAt": at or None}


def _coa(mode: str, iri: str, source_sha: str, snapshots: Path = COA_SNAPSHOTS) -> dict[str, Any]:
    if mode == "off":
        return {"mode": "off", "state": "not-captured",
                "note": "COA is off in this build: the Git record governs the definition."}
    if mode != "snapshot":
        raise ValueError(f"coaMode {mode!r}: use one of {COA_MODES}")
    from .coa_snapshot import read_snapshot

    folders = sorted(p for p in snapshots.iterdir() if (p / "manifest.json").exists()) if snapshots.is_dir() else []
    if not folders:
        raise FileNotFoundError(f"coaMode snapshot: no recorded COA session under {snapshots}")
    folder = folders[-1]  # the latest session
    snap = read_snapshot(folder, iri)
    snap["ontology"]["matchesGitSource"] = any(h["sha256"] == source_sha for h in snap["hashes"][:1])
    return {"mode": "snapshot", "state": "captured", "snapshotId": f"coa-{folder.name}",
            "snapshotAt": snap["capturedAt"], "snapshot": snap,
            "note": f"Recorded COA session of {snap['capturedAt']} (runs/local/coa/{folder.name}); replayed, not live."}


def record(dist: Path, name: str = "qualifying_income", coa_mode: str = "off") -> dict[str, Any]:
    dist = dist.resolve()
    from .checks import run_all

    iri = METRICS[name]
    store = ox.Store()
    store.load(path=str(dist / "nquads" / "rp.nq"), format=ox.RdfFormat.N_QUADS)

    def objects(pred: str) -> list[str]:
        return sorted(q.object.value for q in store.quads_for_pattern(ox.NamedNode(iri), ox.NamedNode(pred), None))

    def one(pred: str) -> str | None:
        vals = objects(pred)
        return vals[0] if vals else None

    ttl = SRC_DIR / "rp" / "fin.ttl"
    computed_by = one(f"{RP}computedBy")
    template = dist / "templates" / f"{computed_by}.rq" if computed_by else None
    graph = next((q.graph_name.value for q in store.quads_for_pattern(ox.NamedNode(iri), None, None)), None)
    checks = [{"name": n, "ok": bool(ok), "detail": str(detail)[:200] if detail else None}
              for n, ok, detail in run_all(dist, REPO / "data" / "samples", REPO)]
    version = dist.name
    return {
        "source": "git",
        "definition": {"iri": iri, "name": name, "label": one(f"{RDFS}label"), "description": one(f"{RDFS}comment"),
                       "subClassOf": objects(f"{RDFS}subClassOf"), "altLabels": objects(f"{SKOS}altLabel"),
                       "operandConcepts": objects(f"{FIN}operandConcept"), "computedBy": computed_by,
                       "graph": graph},
        "ontology": {"ontologyId": "urn:rp:ont:fin", "version": version,
                     "nquads": f"ontology/dist/{version}/nquads/rp.nq", "source": str(ttl.relative_to(REPO)),
                     "sourceSha256": _sha(ttl)},
        "template": ({"name": computed_by, "path": str(template.relative_to(REPO)), "sha256": _sha(template)}
                     if template and template.exists() else None),
        "git": _git_last_commit(ttl),
        "review": _review(),
        "check": {"command": "make ontology-check", "passed": sum(c["ok"] for c in checks), "total": len(checks),
                  "checks": checks, "inputsSha256": _sha(dist / "nquads" / "rp.nq")},
        "coa": _coa(coa_mode, iri, _sha(ttl)),
        # dated by its input (the built rp.nq), so rebuilding unchanged sources gives an identical file
        "recordedAt": datetime.fromtimestamp((dist / "nquads" / "rp.nq").stat().st_mtime, UTC)
        .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "recordedBy": "ontology/rpont/governance.py",
    }


def write(dist: Path, name: str = "qualifying_income", coa_mode: str = "off") -> Path:
    out = dist / "governance" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record(dist, name, coa_mode), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dist", type=Path, nargs="?", help="default: ontology/dist/<the manifest's ontologyVersion>")
    ap.add_argument("--name", default="qualifying_income", choices=sorted(METRICS))
    ap.add_argument("--coa-mode", default=os.environ.get("RP_COA_MODE", "off"), choices=COA_MODES,
                    help="off: Git record only; snapshot: add the recorded COA session (default: $RP_COA_MODE or off)")
    a = ap.parse_args()
    path = write(a.dist or DIST_DIR / Manifest.load().ontology_version, a.name, a.coa_mode)
    body = json.loads(path.read_text())
    print(f"{path}: check {body['check']['passed']}/{body['check']['total']}, "
          f"git {(body['git'] or {}).get('commit', 'none')[:12]}, review {body['review']}, coa {body['coa']['mode']}")
