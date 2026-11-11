"""Data-bucket IO. Every write returns the ``ArtifactRef`` the stage records, so the stage
inspector shows key, size, sha256 and the S3 versionId of exactly what was written."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from evidence_core.enums import ArtifactKind
from evidence_core.hashing import sha256_bytes
from evidence_core.models.status import ArtifactRef

from .aws import services


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


@dataclass
class DataStore:
    bucket: str
    client: Any = None

    @classmethod
    def default(cls, bucket: str | None = None) -> DataStore:
        svc = services()
        return cls(bucket or svc.settings.data_bucket, svc.s3)

    # --- writes ---------------------------------------------------------------------------
    def put_bytes(self, key: str, data: bytes, *, kind: ArtifactKind, content_type: str,
                  page: int | None = None, count: int | None = None, note: str | None = None) -> ArtifactRef:
        resp = self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return ArtifactRef(key=key, kind=kind, content_type=content_type, size_bytes=len(data),
                           sha256=sha256_bytes(data), version_id=resp.get("VersionId"), page=page, count=count,
                           note=note)

    def put_json(self, key: str, value: Any, *, kind: ArtifactKind, count: int | None = None,
                 page: int | None = None, note: str | None = None) -> ArtifactRef:
        if hasattr(value, "dump"):
            value = value.dump()
        return self.put_bytes(key, _json_bytes(value), kind=kind, content_type="application/json", page=page,
                              count=count, note=note)

    def put_jsonl(self, key: str, rows: Iterable[Any], *, kind: ArtifactKind, note: str | None = None) -> ArtifactRef:
        lines = [(_json_bytes(r.dump() if hasattr(r, "dump") else r)) for r in rows]
        data = b"\n".join(lines) + (b"\n" if lines else b"")
        return self.put_bytes(key, data, kind=kind, content_type="application/x-ndjson", count=len(lines),
                              note=note)

    def copy(self, src_key: str, dst_key: str, *, src_version_id: str | None = None) -> str | None:
        source: dict[str, str] = {"Bucket": self.bucket, "Key": src_key}
        if src_version_id:
            source["VersionId"] = src_version_id
        resp = self.client.copy_object(Bucket=self.bucket, Key=dst_key, CopySource=source)
        return resp.get("VersionId")

    # --- reads ----------------------------------------------------------------------------
    def get_bytes(self, key: str, version_id: str | None = None) -> bytes:
        kwargs: dict[str, str] = {"Bucket": self.bucket, "Key": key}
        if version_id:
            kwargs["VersionId"] = version_id
        return self.client.get_object(**kwargs)["Body"].read()

    def get_json(self, key: str, version_id: str | None = None) -> Any:
        return json.loads(self.get_bytes(key, version_id))

    def get_jsonl(self, key: str) -> list[dict[str, Any]]:
        text = self.get_bytes(key).decode("utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError as e:  # pragma: no cover - depends on error shape
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def head(self, key: str, version_id: str | None = None) -> dict[str, Any]:
        kwargs: dict[str, str] = {"Bucket": self.bucket, "Key": key}
        if version_id:
            kwargs["VersionId"] = version_id
        return self.client.head_object(**kwargs)

    def ref(self, key: str, kind: ArtifactKind, *, content_type: str = "application/json",
            version_id: str | None = None, sha256: str | None = None, size_bytes: int | None = None,
            page: int | None = None, count: int | None = None, note: str | None = None) -> ArtifactRef:
        """Reference to an existing object (stage inputs) without reading it."""
        return ArtifactRef(key=key, kind=kind, content_type=content_type, version_id=version_id, sha256=sha256,
                           size_bytes=size_bytes, page=page, count=count, note=note)
