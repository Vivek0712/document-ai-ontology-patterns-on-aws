"""Real AWS inference calls for local runs, recorded so a rebuild is reproducible offline.

``RecordedComprehend`` wraps ``comprehend:DetectPiiEntities`` and ``RecordedTitan`` wraps Titan Text
Embeddings V2 (``bedrock-runtime:InvokeModel``). Each response is stored in a JSON file keyed by the
sha256 of the request (text + parameters), so a second build with the same inputs makes no AWS call.
The files hold entity types, scores and offsets, or vectors: never the input text.

A miss calls AWS through ``client_factory`` (created lazily, so an offline replay needs no
credentials). ``offline=True`` turns a miss into an error instead of a call.
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RECORDED_DIR = Path(__file__).resolve().parents[2] / "runs" / "local" / "recorded"


class RecordingMiss(RuntimeError):
    """An offline replay needed a response that was never recorded."""


def request_key(*parts: Any) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@dataclass
class Recording:
    """One JSON file of recorded responses: ``{"engine", "recordedAt", "responses": {key: response}}``."""

    path: Path
    engine: str
    responses: dict[str, Any] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0
    _dirty: bool = False

    @classmethod
    def open(cls, path: Path, engine: str) -> Recording:
        rec = cls(path=path, engine=engine)
        if path.exists():
            rec.responses = json.loads(path.read_text(encoding="utf-8")).get("responses", {})
        return rec

    def get(self, key: str) -> Any | None:
        hit = self.responses.get(key)
        if hit is not None:
            self.hits += 1
        return hit

    def put(self, key: str, response: Any) -> None:
        self.misses += 1
        self.responses[key] = response
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        body = {"engine": self.engine, "recordedAt": datetime.now(UTC).isoformat(timespec="seconds"),
                "note": "real AWS responses keyed by sha256(request); no input text is stored",
                "responses": dict(sorted(self.responses.items()))}
        self.path.write_text(json.dumps(body, separators=(",", ":")) + "\n", encoding="utf-8")
        self._dirty = False


@dataclass
class RecordedComprehend:
    """``detect_pii_entities`` with the boto3 surface the PII gate uses."""

    recording: Recording
    client_factory: Callable[[], Any]
    offline: bool = False
    _client: Any = None

    LABEL = "Amazon Comprehend DetectPiiEntities (real; responses recorded)"

    def detect_pii_entities(self, Text: str, LanguageCode: str = "en") -> dict[str, Any]:  # noqa: N803
        key = request_key("DetectPiiEntities", LanguageCode, Text)
        hit = self.recording.get(key)
        if hit is None:
            if self.offline:
                raise RecordingMiss(f"no recorded Comprehend response for a {len(Text)}-character text")
            self._client = self._client or self.client_factory()
            resp = self._client.detect_pii_entities(Text=Text, LanguageCode=LanguageCode)
            hit = {"Entities": [{k: e[k] for k in ("Type", "Score", "BeginOffset", "EndOffset")}
                                for e in resp.get("Entities", [])]}
            self.recording.put(key, hit)
        return {"Entities": [dict(e) for e in hit["Entities"]]}


@dataclass
class RecordedTitan:
    """A bedrock-runtime stand-in whose ``invoke_model`` replays recorded Titan V2 embeddings.

    Only Titan embedding requests are served; anything else goes to the real client unrecorded.
    """

    recording: Recording
    client_factory: Callable[[], Any]
    offline: bool = False
    input_tokens: int = 0
    _client: Any = None

    LABEL = "amazon.titan-embed-text-v2:0 (real Bedrock; responses recorded)"

    def _real(self) -> Any:
        self._client = self._client or self.client_factory()
        return self._client

    def invoke_model(self, *, modelId: str, body: str | bytes, **kwargs: Any) -> dict[str, Any]:  # noqa: N803
        if not modelId.startswith("amazon.titan-embed"):
            return self._real().invoke_model(modelId=modelId, body=body, **kwargs)
        req = json.loads(body)
        key = request_key(modelId, req.get("inputText", ""), req.get("dimensions"), req.get("normalize"))
        hit = self.recording.get(key)
        if hit is None:
            if self.offline:
                raise RecordingMiss(f"no recorded {modelId} embedding for a {len(req.get('inputText', ''))}-char text")
            resp = self._real().invoke_model(modelId=modelId, body=body, **kwargs)
            payload = json.loads(resp["body"].read())
            if "embedding" not in payload:
                raise RuntimeError(f"{modelId} returned no embedding (is bedrock-runtime mocked? "
                                   f"keys: {sorted(payload)})")
            hit = {"embedding": [round(float(x), 7) for x in payload["embedding"]],
                   "inputTextTokenCount": payload.get("inputTextTokenCount")}
            self.input_tokens += int(payload.get("inputTextTokenCount") or 0)
            self.recording.put(key, hit)
        return {"body": io.BytesIO(json.dumps(hit).encode("utf-8")), "contentType": "application/json"}


def aws_client_factory(service: str, profile: str | None, region: str = "us-east-1") -> Callable[[], Any]:
    """A factory for a real client on the developer's profile (only built on a recording miss)."""

    def make() -> Any:
        import boto3

        return boto3.Session(profile_name=profile or None, region_name=region).client(service, region_name=region)

    return make
