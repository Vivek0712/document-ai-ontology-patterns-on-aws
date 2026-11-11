"""Query embedding with Amazon Titan Text Embeddings V2 (same model and settings as embed_index)."""

from __future__ import annotations

import json
from typing import Any


def embed_query(text: str, *, model_id: str, dimensions: int = 1024, normalize: bool = True,
                client: Any = None) -> list[float]:
    if client is None:
        import boto3

        client = boto3.client("bedrock-runtime")
    body = {"inputText": text, "dimensions": dimensions, "normalize": normalize}
    resp = client.invoke_model(modelId=model_id, body=json.dumps(body), contentType="application/json",
                               accept="application/json")
    payload = json.loads(resp["body"].read())
    vector = payload["embedding"]
    if len(vector) != dimensions:
        raise ValueError(f"{model_id} returned {len(vector)} dims, expected {dimensions}")
    return [float(v) for v in vector]
