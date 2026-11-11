"""A's built ontology through C's knowledge-plane Lambdas (pyoxigraph stands in for Neptune; moto for S3):
rp-neptune-load loads the TBox N-Quads exactly, and rp-vocab-export rebuilds the same vocab snapshot that
ontology/build.py wrote, so the TBox predicates C's export relies on are the ones A emits."""

from __future__ import annotations

import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "ontology" / "dist" / "1.2.0"
pytest.importorskip("pyoxigraph")
pytestmark = pytest.mark.skipif(not (DIST / "vocab.json").exists(), reason="run `make ontology-check`")


@pytest.fixture
def loaded(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    from lambdas.neptune_load import handler as neptune_load
    from lambdas.tools._lib import stores as stores_mod
    from local_pipeline.graph_check import OxiSparql

    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "us-east-1",
                 "AWS_REGION": "us-east-1", "RP_ENV": "test", "RP_DATA_BUCKET": "rp-data-kp",
                 "RP_CONFIG_SOURCE": "s3"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="rp-data-kp")
        for p in (DIST / "nquads").glob("*.nq"):  # what CDK BucketDeployment puts under config/ontology/
            s3.put_object(Bucket="rp-data-kp", Key=f"config/ontology/1.2.0/nquads/{p.name}", Body=p.read_bytes())
        stores_mod.use_stores(None)
        client = OxiSparql()
        neptune_load.use_client(client)
        report = neptune_load.handler({"ontologyVersion": "1.2.0"})
        yield client, report
        neptune_load.use_client(None)
        stores_mod.use_stores(None)


def test_neptune_load_inserts_every_tbox_quad(loaded) -> None:  # noqa: ANN001
    import pyoxigraph as ox

    _, report = loaded
    for name in ("fibo", "doco", "rp"):
        ref = ox.Store()
        ref.load((DIST / "nquads" / f"{name}.nq").read_bytes(), format=ox.RdfFormat.N_QUADS)
        assert report["graphs"][name]["triples"] == len(ref) > 0, name


def test_vocab_export_matches_the_built_snapshot(loaded) -> None:  # noqa: ANN001
    from lambdas.vocab_export.handler import build_snapshot

    client, _ = loaded
    exported = build_snapshot(client, "1.2.0").dump()
    built = json.loads((DIST / "vocab.json").read_text())

    def norm(snapshot: dict, key: str, ident: str) -> dict:
        out = {}
        for item in snapshot[key]:
            out[item[ident]] = {k: sorted(v) if isinstance(v, list) else v for k, v in item.items()}
        return out

    for key, ident in (("roles", "role"), ("docTypes", "concept"), ("fieldConcepts", "concept")):
        assert norm(exported, key, ident) == norm(built, key, ident), key
