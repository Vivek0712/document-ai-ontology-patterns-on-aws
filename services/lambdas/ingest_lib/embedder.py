"""Text embeddings: Amazon Titan Text Embeddings V2 (deployed) and a deterministic local
embedder for tests and ``make units`` without AWS (always labelled as such in outputs)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

TITAN_MAX_CHARS = 40_000  # Titan V2 accepts 8,192 tokens / 50,000 characters per input


class Embedder(Protocol):
    model_id: str
    dimensions: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


@dataclass
class TitanEmbedder:
    """``bedrock-runtime:InvokeModel`` on ``amazon.titan-embed-text-v2:0`` (one input per call)."""

    client: Any
    model_id: str = "amazon.titan-embed-text-v2:0"
    dimensions: int = 1024
    normalize: bool = True
    input_tokens: int = field(default=0, init=False)
    calls: int = field(default=0, init=False)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            body = json.dumps({"inputText": (text or " ")[:TITAN_MAX_CHARS], "dimensions": self.dimensions,
                               "normalize": self.normalize})
            resp = self.client.invoke_model(modelId=self.model_id, body=body, contentType="application/json",
                                            accept="application/json")
            payload = json.loads(resp["body"].read())
            self.calls += 1
            self.input_tokens += int(payload.get("inputTextTokenCount") or 0)
            out.append([float(x) for x in payload["embedding"]])
        return out


_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass
class HashEmbedder:
    """Feature-hashed word unigrams + character trigrams, L2-normalised. Deterministic, offline.

    Not a semantic model: it exists so Phase B and tests run without Bedrock. Every artifact
    built with it records ``model_id = "local-hash-embedder"``.
    """

    model_id: str = "local-hash-embedder"
    dimensions: int = 1024

    def _features(self, text: str) -> list[str]:
        t = text.lower()
        words = _TOKEN.findall(t)
        grams = [t[i:i + 3] for i in range(max(0, len(t) - 2)) if not t[i:i + 3].isspace()]
        return [f"w:{w}" for w in words] + [f"g:{g}" for g in grams]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dimensions
            for f in self._features(text or ""):
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
                vec[h % self.dimensions] += 1.0 if (h >> 63) & 1 else -1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
