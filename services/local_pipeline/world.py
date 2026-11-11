"""The in-process AWS world of a local run, saved to disk and loaded back (dev and tests only).

``make e2e-local`` runs every stage handler against moto (S3, DynamoDB) and in-memory stand-ins for
OpenSearch Serverless and Neptune. ``dump_world`` writes what those services held at the end of the
run to ``{out}/world/``:

    s3.json               one row per object: key, versionId, contentType, lastModified, size, sha256
    s3/<key>              the object bytes (Bronze PDFs, Silver units, PII decisions, Gold units, page PNGs, ...)
    ddb/<table>.json      every item of every table (packages, documents, doc_status, unit_status, ...)
    aoss/<index>.json     the index documents (``embedding`` dropped: the local run uses a hash embedder)

``open_world`` loads that snapshot into a fresh moto session with the **same** S3 version ids and
timestamps, so the real API, tool and agent code can read it exactly as the run left it: the local
ask harness (``services/local_agent``) and the UI mock-world export (``scripts/mock_world.py``) both
start here. Graph state is the N-Quads file the run already writes (``{out}/graph/abox.nq``).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

WORLD_DIR = "world"
BUCKET = "rp-data-local"


def _safe_rel(key: str) -> Path:
    rel = Path(key)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe S3 key {key!r}")
    return rel


def dump_world(out: Path, *, s3: Any, dynamodb: Any, bucket: str, aoss_docs: dict[str, dict[str, dict[str, Any]]],
               meta: dict[str, Any]) -> Path:
    """Write the S3 bucket, every DynamoDB table and the AOSS documents under ``{out}/world``."""
    from evidence_core.ddb import TABLE_SCHEMA, from_ddb

    root = out / WORLD_DIR
    if root.exists():
        import shutil

        shutil.rmtree(root)
    (root / "s3").mkdir(parents=True)
    objects = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for item in page.get("Contents", []):
            key = item["Key"]
            obj = s3.get_object(Bucket=bucket, Key=key)
            body = obj["Body"].read()
            dest = root / "s3" / _safe_rel(key)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            objects.append({"key": key, "versionId": obj.get("VersionId"), "contentType": obj.get("ContentType"),
                            "lastModified": obj["LastModified"].isoformat(), "size": len(body),
                            "sha256": hashlib.sha256(body).hexdigest(), "metadata": obj.get("Metadata") or {}})
    (root / "s3.json").write_text(json.dumps({"bucket": bucket, "objects": objects}, indent=1))
    (root / "ddb").mkdir()
    for logical, spec in TABLE_SCHEMA.items():
        table = dynamodb.Table(spec["name"])
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {}
        while True:
            resp = table.scan(**kwargs)
            items.extend(from_ddb(i) for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        items.sort(key=lambda i: tuple(str(i.get(k, "")) for k in (spec["pk"], spec["sk"] or "")))
        (root / "ddb" / f"{logical}.json").write_text(json.dumps(items, indent=1, default=str))
    (root / "aoss").mkdir()
    for index, docs in aoss_docs.items():
        rows = [{"_id": i} | {k: v for k, v in d.items() if k != "embedding"} for i, d in docs.items()]
        (root / "aoss" / f"{index}.json").write_text(json.dumps(rows, indent=1))
    (root / "meta.json").write_text(json.dumps(meta, indent=1))
    return root


@dataclass
class LocalWorld:
    """A loaded snapshot: live moto clients plus what the stand-ins held."""

    root: Path
    bucket: str
    s3: Any
    dynamodb: Any
    meta: dict[str, Any]
    aoss_docs: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)  # key -> s3.json row

    def table_items(self, logical: str) -> list[dict[str, Any]]:
        return json.loads((self.root / "ddb" / f"{logical}.json").read_text())

    def read_bytes(self, key: str) -> bytes:
        return (self.root / "s3" / _safe_rel(key)).read_bytes()

    def read_json(self, key: str) -> Any:
        return json.loads(self.read_bytes(key))


def _pin_version(bucket: str, key: str, version_id: str | None, last_modified: str) -> None:
    """Give the object just written the version id and timestamp it had in the recorded run.

    moto draws a random version id on every PUT; replaying the snapshot keeps the ids that the stage
    records, unit provenance and run records already reference."""
    from moto.core import DEFAULT_ACCOUNT_ID
    from moto.s3.models import s3_backends

    backends = s3_backends[DEFAULT_ACCOUNT_ID]
    backend = backends["aws"] if "aws" in backends else backends["global"]
    fake = backend.buckets[bucket].keys.getlist(key)[-1]
    if version_id:
        fake._version_id = version_id
    fake.last_modified = datetime.fromisoformat(last_modified)


def local_env(bucket: str = BUCKET) -> dict[str, str]:
    """Environment the handlers expect in a local world (same as ``local_pipeline.run``)."""
    repo = Path(__file__).resolve().parents[2]
    return {"RP_ENV": "local", "RP_CONFIG_SOURCE": "local", "RP_REPO_ROOT": str(repo), "RP_DATA_BUCKET": bucket,
            "RP_HEARTBEAT_SECONDS": "0", "RP_CODE_SHA": "local", "AWS_DEFAULT_REGION": "us-east-1",
            "AWS_REGION": "us-east-1"}


@contextmanager
def open_world(build: Path, *, passthrough: tuple[str, ...] = ()) -> Iterator[LocalWorld]:
    """Load ``{build}/world`` into moto. ``passthrough`` lists services that reach real AWS (for example
    ``bedrock-runtime`` for the local ask harness); everything else stays in-process."""
    root = build / WORLD_DIR
    if not (root / "s3.json").exists():
        raise FileNotFoundError(f"{root} has no snapshot: run `make e2e-local` first")
    index = json.loads((root / "s3.json").read_text())
    bucket = index["bucket"]
    saved = {k: os.environ.get(k) for k in [*local_env(bucket), "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]}
    os.environ.update(local_env(bucket))
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "local")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "local")
    from moto import mock_aws

    # moto mocks some services (bedrock-runtime among them) even when listed by name, so pass through by URL
    urls = [rf"https://{svc}\.[a-z0-9-]+\.amazonaws\.com/.*" for svc in passthrough]
    config = {"core": {"passthrough": {"urls": urls}}} if passthrough else None
    try:
        with mock_aws(config=config) if config else mock_aws():
            import boto3
            from evidence_core.ddb import TABLE_SCHEMA, create_tables_for_tests, to_ddb

            ddb = boto3.resource("dynamodb", region_name="us-east-1")
            create_tables_for_tests(ddb)
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket=bucket)
            s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
            objects = {}
            for row in index["objects"]:
                body = (root / "s3" / _safe_rel(row["key"])).read_bytes()
                extra = {"ContentType": row["contentType"]} if row.get("contentType") else {}
                if row.get("metadata"):
                    extra["Metadata"] = row["metadata"]
                s3.put_object(Bucket=bucket, Key=row["key"], Body=body, **extra)
                _pin_version(bucket, row["key"], row.get("versionId"), row["lastModified"])
                objects[row["key"]] = row
            for logical, spec in TABLE_SCHEMA.items():
                path = root / "ddb" / f"{logical}.json"
                if not path.exists():
                    continue
                with ddb.Table(spec["name"]).batch_writer() as batch:
                    for item in json.loads(path.read_text()):
                        batch.put_item(Item=to_ddb(item))
            aoss = {p.stem: json.loads(p.read_text()) for p in sorted((root / "aoss").glob("*.json"))}
            meta = json.loads((root / "meta.json").read_text()) if (root / "meta.json").exists() else {}
            yield LocalWorld(root=root, bucket=bucket, s3=s3, dynamodb=ddb, meta=meta, aoss_docs=aoss,
                             objects=objects)
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
