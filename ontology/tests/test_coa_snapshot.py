"""The governance record's COA slot: ``off`` keeps the Git record only; ``snapshot`` replays a recorded COA session.

Honesty: every COA value on the record is read from the captured files under ``runs/local/coa/<date>/``. The tests
compare the record with the files directly, and edit a copy of the files to show the record follows them.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ONT = Path(__file__).resolve().parents[1]
REPO = ONT.parent
if str(ONT) not in sys.path:
    sys.path.insert(0, str(ONT))

from evidence_core.models.governance import GovernedDefinitionView  # noqa: E402
from rpont import governance  # noqa: E402
from rpont.build_lib import DIST_DIR, Manifest  # noqa: E402

SNAPSHOTS = REPO / "runs" / "local" / "coa"
IRI = "urn:rp:ont:fin:QualifyingIncome"


def _session() -> Path:
    return sorted(p for p in SNAPSHOTS.iterdir() if (p / "manifest.json").exists())[-1]


def _file(name: str) -> dict:
    return json.loads((_session() / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dist() -> Path:
    """The built ontology (``make ontology-check``); the record names repo-relative paths, so it must be in the repo."""
    out = DIST_DIR / Manifest.load().ontology_version
    if not (out / "nquads" / "rp.nq").exists():
        pytest.skip("ontology not built: run make ontology-check")
    return out


def test_coa_off_is_the_git_fallback(dist) -> None:
    rec = governance.record(dist, "qualifying_income", coa_mode="off")
    view = GovernedDefinitionView.load(rec)
    assert view.source == "git" and view.coa.mode == "off" and view.coa.state == "not-captured"
    assert view.coa.snapshot is None and "snapshot" not in view.coa.dump()


def test_coa_snapshot_values_come_from_the_recorded_files(dist) -> None:
    view = GovernedDefinitionView.load(governance.record(dist, "qualifying_income", coa_mode="snapshot"))
    c = view.coa
    assert c.mode == "snapshot" and c.state == "captured" and c.snapshot is not None
    s = c.snapshot
    assert c.snapshot_at == s.captured_at == _file("02_namespace.json")["response"]["namespace"]["createdAt"][:10]

    # the cross-check value is the recorded Tier-1 result row, verbatim
    query = _file("12_query_rest_tier1.json")
    assert s.cross_check.value == query["response"]["result"]["resultRows"][0]["qualifying_income"] == "169650.00"
    assert (s.cross_check.tier, s.cross_check.confidence) == (1, 1.0)
    assert [t.step for t in s.cross_check.trace] == ["metric_match", "authorize", "firewall", "execute"]
    assert [t.tool for t in s.cross_check.trace][1] == "cedar" and s.cross_check.trace[-1].engine == "athena"
    assert s.cross_check.runs == len(query["repeat_runs"]) == s.cross_check.identical_runs
    assert s.cross_check.dimensions == {"package_id": "pkg-lending-01"}

    # the metric card as COA's server returned it, including the fields only the server sets
    metric = _file("11_metric_get.json")["response"]["metric"]
    assert s.metric.sql == metric["expression"]["dialects"][0]["expression"] and s.metric.dialect == "TRINO"
    assert s.metric.ontology_concepts == [IRI] and s.metric.label == metric["label"]
    assert s.metric.effective_from == metric["effectiveFrom"] and s.metric.defined_by == metric["definedBy"]
    assert s.metric.defined_by_redacted  # the snapshot never carries the principal's e-mail

    # uploaded Turtle is never presented as steward-reviewed
    assert s.ontology.label == "uploaded, not steward-reviewed" and s.ontology.steward_reviewed is False
    assert s.ontology.ontology_type == "user_uploaded" and s.ontology.matches_git_source is True

    hashes = {h.name: h.sha256 for h in s.hashes}
    assert hashes["fin.ttl as uploaded"] == _file("03_ontology_ingest.json")["source_file"]["sha256"]
    assert hashes["COA ontology export"] == _file("19_ontology_export.json")["export_sha256"]
    assert hashes["OSI metric export"] == _file("20_export_osi.json")["sha256_of_response_json_sorted"]

    lat = {x.call: x for x in s.latency.calls}
    assert s.latency.measured and "laptop" in s.latency.basis
    recorded = _file("21_latency_summary.json")["rest_query_tier1"]["median_ms"]
    assert lat["REST query · Tier 1 (execute)"].median_ms == recorded
    assert s.access is not None and {r.status for r in s.access.refused} == {401}
    assert set(s.files) >= {"11_metric_get.json", "12_query_rest_tier1.json", "manifest.json"}


def test_the_record_follows_the_files_not_this_code(dist, tmp_path) -> None:
    """Edit a copy of the recorded result: the record shows the edited value, so nothing is typed in."""
    session = tmp_path / "coa" / _session().name
    shutil.copytree(_session(), session)
    path = session / "12_query_rest_tier1.json"
    body = json.loads(path.read_text())
    body["response"]["result"]["resultRows"][0]["qualifying_income"] = "1.00"
    path.write_text(json.dumps(body))
    coa = governance._coa("snapshot", IRI, "x" * 64, snapshots=tmp_path / "coa")
    assert coa["snapshot"]["crossCheck"]["value"] == "1.00"
    assert coa["snapshot"]["ontology"]["matchesGitSource"] is False
    assert coa["snapshot"]["crossCheck"]["identicalRuns"] == 0  # the repeats no longer match the edited row


def test_snapshot_mode_without_a_recording_fails_loudly(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        governance._coa("snapshot", IRI, "x" * 64, snapshots=tmp_path / "none")
    with pytest.raises(ValueError):
        governance._coa("live", IRI, "x" * 64)
