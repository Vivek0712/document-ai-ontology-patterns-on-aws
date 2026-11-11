"""scripts/coa/seed.py: the shipped data equals what the repo derives it from, and the COA calls reproduce the
recorded session (runs/local/coa/2026-11-11) step by step against a fake COA."""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "runs" / "local" / "coa" / "2026-11-11"


def _seed() -> Any:
    spec = importlib.util.spec_from_file_location("coa_seed", ROOT / "scripts" / "coa" / "seed.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules["coa_seed"] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


seed = _seed()


def test_csv_is_the_gold_w2_box1_rows() -> None:
    rows = seed.rows_from_gold(ROOT / "data" / "samples" / "lending" / "gold.jsonl")
    assert (ROOT / "data" / "coa" / "w2_box1.csv").read_text() == seed.csv_text(rows)
    assert sum(float(r["amount_box1"]) for r in rows) == 169650.00


def test_metric_is_the_recorded_definition() -> None:
    metric = json.loads((ROOT / "scripts" / "coa" / "metric_qualifying_income.json").read_text())
    recorded = json.loads((SNAPSHOT / "11_metric_get.json").read_text())["response"]["metric"]
    for key in ("name", "label", "description", "expression", "sourceTable", "unit", "returnType", "defaultTimeGrain",
                "aiContext", "ontologyConcepts"):
        assert metric[key] == recorded[key], key


def test_config_names_the_recorded_namespace_and_ontology() -> None:
    cfg = seed.load_config(ROOT)
    recorded_ns = json.loads((SNAPSHOT / "02_namespace.json").read_text())["request"]["body"]
    assert cfg["seed"]["namespace"]["name"] == recorded_ns["name"]
    assert cfg["seed"]["glueDatabase"] in json.loads((ROOT / "scripts/coa/metric_qualifying_income.json").read_text()
                                                     )["sourceTable"]
    assert cfg["coa"]["tag"] == json.loads((SNAPSHOT / "manifest.json").read_text())["coaVersion"]


def test_dry_run_makes_no_call(capsys: pytest.CaptureFixture[str]) -> None:
    assert seed.main(["--root", str(ROOT), "--dry-run"]) == 0
    assert "would seed namespace com305-lending" in capsys.readouterr().out


class FakeCoa:
    """Routes (method, path) to canned answers and records every call."""

    def __init__(self, *, have_ns: bool = False, have_metric: bool = False, total: str = "169650.00") -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.have_ns, self.have_metric, self.total = have_ns, have_metric, total
        self.source_polls = 0
        self.db_tags: dict[str, str] = {"coa:namespace": "ns-from-the-last-deploy"}  # the normal, stale case

    def owned(self) -> bool:
        """COA's ownership check (sources-stack GlueOwnershipTagRead): on create and on every scan."""
        return "ns-1" in self.db_tags.get("coa:namespace", "").split()

    def __call__(self, req: Any, timeout: float = 0) -> Any:
        path = req.full_url.split("/prod", 1)[1]
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), path, body))
        assert req.headers["Authorization"] == "Bearer tok"
        status, payload = self.answer(req.get_method(), path)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "x", {}, io.BytesIO(json.dumps(payload).encode()))
        return _Resp(status, payload)

    def answer(self, method: str, path: str) -> tuple[int, Any]:
        if (method, path) == ("GET", "/namespaces?maxResults=100"):
            return 200, {"namespaces": [{"name": "com305-lending", "namespaceId": "ns-1"}] if self.have_ns else []}
        if (method, path) == ("POST", "/namespaces"):
            return 201, {"namespace": {"namespaceId": "ns-1"}}
        if path == "/namespaces/ns-1/ontologies":
            return 200, {"ontologies": []}
        if path.endswith("/upload-url"):
            return 200, {"uploadUrl": "https://bucket.example/put", "s3Key": "k"}
        if path.endswith("/ingest-from-s3"):
            return 202, {"result": {"jobId": "j1", "status": "pending"}}
        if "/ingest-status/" in path:
            return 200, {"result": {"status": "completed", "result": {"classCount": 4}}}
        if (method, path) == ("GET", "/namespaces/ns-1/sources"):
            return 200, {"items": []}
        if (method, path) == ("POST", "/namespaces/ns-1/sources"):
            if not self.owned():
                return 403, {"message": "database com305_coa_spike_lending is not registered to namespace ns-1"}
            return 202, {"sourceId": "src-1", "status": "REGISTERED"}
        if (method, path) == ("GET", "/namespaces/ns-1/sources/src-1"):
            if not self.owned():
                return 200, {"status": "SCAN_FAILED"}
            self.source_polls += 1
            approved = any(c[1].endswith("/approve") for c in self.calls)
            return 200, {"status": "APPROVED" if approved else ("SCANNING" if self.source_polls == 1 else
                                                               "PENDING_REVIEW")}
        if path.endswith("/tables"):
            return 200, {"items": [{"tableId": "com305_coa_spike_lending.w2_box1", "reviewStatus": "PENDING_REVIEW"}]}
        if path.endswith("/review") or path.endswith("/approve"):
            return 202, {}
        if path == "/namespaces/ns-1/metrics/qualifying_income":
            return (200, {"metric": {}}) if self.have_metric else (404, {"message": "not found"})
        if path.endswith("/metrics/validate"):
            return 200, {"warnings": []}
        if path == "/namespaces/ns-1/metrics":
            return 201, {"metric": {}}
        if path == "/namespaces/ns-1/query":
            return 200, {"result": {"resultRows": [{"qualifying_income": self.total}]}}
        raise AssertionError(f"unexpected {method} {path}")


class _Resp:
    def __init__(self, status: int, payload: Any) -> None:
        self.status, self._body = status, json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: Any) -> None:
        return None


class FakeGlue:
    def __init__(self, fake: FakeCoa) -> None:
        self.fake, self.calls = fake, []

    def tag_resource(self, ResourceArn: str, TagsToAdd: dict[str, str]) -> None:  # noqa: N803 - boto3 names
        self.calls.append((ResourceArn, TagsToAdd))
        self.fake.db_tags.update(TagsToAdd)


def _run(fake: FakeCoa, *, tag: bool = True) -> list[tuple[str, str, Any]]:
    coa = seed.Coa("https://api.example/prod/", "tok", opener=fake)
    metric = json.loads((ROOT / "scripts/coa/metric_qualifying_income.json").read_text())
    puts: list[tuple[str, int]] = []
    ns = seed.ensure_namespace(coa, {"name": "com305-lending", "displayName": "d", "description": "x"}, "seed@x")
    if tag:
        glue = FakeGlue(fake)
        seed.tag_database(glue, "000000000000", "us-east-1", "com305_coa_spike_lending",
                          seed.namespace_tag_key("coa"), ns)
        assert glue.calls == [("arn:aws:glue:us-east-1:000000000000:database/com305_coa_spike_lending",
                               {"coa:namespace": "ns-1"})]
    seed.ensure_ontology(coa, ns, "urn:rp:ont:fin", b"@prefix fin: <urn:rp:ont:fin:> .", "a3c6901e45f4" + "0" * 52,
                         lambda url, body: puts.append((url, len(body))), sleep=lambda s: None)
    src = seed.ensure_source(coa, ns, "000000000000", "us-east-1", "com305_coa_spike_lending", sleep=lambda s: None)
    seed.ensure_metric(coa, ns, metric, src)
    seed.check_total(coa, ns, metric, "pkg-lending-01", "169650.00")
    assert puts == [("https://bucket.example/put", 32)]
    return fake.calls


def test_fresh_coa_is_seeded_in_the_recorded_order() -> None:
    calls = _run(FakeCoa())
    writes = [(m, p) for m, p, _ in calls if m != "GET"]
    assert writes == [
        ("POST", "/namespaces"),
        ("POST", "/namespaces/ns-1/ontologies/upload-url"),
        ("POST", "/namespaces/ns-1/ontologies/urn%3Arp%3Aont%3Afin/ingest-from-s3"),
        ("POST", "/namespaces/ns-1/sources"),
        ("PUT", "/namespaces/ns-1/sources/src-1/tables/com305_coa_spike_lending.w2_box1/review"),
        ("POST", "/namespaces/ns-1/sources/src-1/approve"),
        ("POST", "/namespaces/ns-1/metrics/validate"),
        ("POST", "/namespaces/ns-1/metrics"),
        ("POST", "/namespaces/ns-1/query"),
    ]
    by_path = {p: b for m, p, b in calls if m == "POST"}
    assert by_path["/namespaces/ns-1/metrics"]["dataSourceId"] == "src-1"
    assert by_path["/namespaces/ns-1/ontologies/urn%3Arp%3Aont%3Afin/ingest-from-s3"]["ontologyType"] == "user_uploaded"
    assert by_path["/namespaces/ns-1/query"]["dimensions"] == [{"name": "package_id", "value": "pkg-lending-01"}]
    assert by_path["/namespaces/ns-1/query"]["tierOverride"] == 1


def test_rerun_skips_what_exists() -> None:
    calls = _run(FakeCoa(have_ns=True, have_metric=True))
    assert ("POST", "/namespaces") not in [(m, p) for m, p, _ in calls]
    assert ("POST", "/namespaces/ns-1/metrics") not in [(m, p) for m, p, _ in calls]


def test_an_untagged_database_is_refused_by_coa() -> None:
    """Without the namespace tag (or with the previous deploy's id) COA refuses the source: the seed must tag."""
    with pytest.raises(seed.CoaError, match="403"):
        _run(FakeCoa(), tag=False)


def test_main_tags_the_database_after_the_namespace_and_before_the_source() -> None:
    src = (ROOT / "scripts" / "coa" / "seed.py").read_text()
    body = src[src.index("def main("):]
    assert body.index("ensure_namespace(") < body.index("tag_database(") < body.index("ensure_source(")


def test_a_wrong_total_fails_the_seed() -> None:
    with pytest.raises(seed.CoaError, match="expected 169650.00"):
        _run(FakeCoa(total="98400.00"))


def test_only_https() -> None:
    with pytest.raises(seed.CoaError):
        seed.Coa("http://api.example", "tok").call("GET", "/health")
