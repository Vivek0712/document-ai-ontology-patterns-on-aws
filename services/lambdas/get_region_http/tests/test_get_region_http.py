from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import boto3
import pytest
from evidence_core.env import Settings
from evidence_core.testing import SAMPLE_SUB
from lambdas.get_region_http import handler as mod
from lambdas.tools._lib import stores as stores_mod
from lambdas.tools._lib.stores import s3_client
from lambdas.tools._lib.testing import BUCKET, USER_B_SUB, seed_world
from moto import mock_aws

REPO = Path(__file__).resolve().parents[4]


@pytest.fixture
def world(monkeypatch):
    for k, v in {"RP_ENV": "test", "RP_CONFIG_SOURCE": "local", "RP_REPO_ROOT": str(REPO), "RP_DATA_BUCKET": BUCKET,
                 "AWS_DEFAULT_REGION": "us-east-1", "AWS_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "testing",
                 "AWS_SECRET_ACCESS_KEY": "testing", "RP_CORS_ORIGINS": "https://web.example.com"}.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        w = seed_world(boto3.resource("dynamodb", region_name="us-east-1"),
                       s3_client("us-east-1"), Settings.from_env())
        stores_mod.use_stores(w.stores)
        yield w
        stores_mod.use_stores(None)


def api_event(unit_id: str, field: str | None, *, sub: str | None = SAMPLE_SUB,
              groups: str = "[lending-analyst]") -> dict[str, Any]:
    claims = {"sub": sub, "cognito:groups": groups} if sub else {}
    return {"pathParameters": {"unitId": quote(unit_id, safe="")},
            "queryStringParameters": {"field": field} if field else None,
            "headers": {"Origin": "https://web.example.com"},
            "requestContext": {"requestId": "req-1", "authorizer": {"claims": claims}}}


def test_user_a_gets_region_with_presigned_url(world):
    resp = mod.handler(api_event(world.w2.unit_id, "f-box1"))
    assert resp["statusCode"] == 200
    assert resp["headers"]["Access-Control-Allow-Origin"] == "https://web.example.com"
    body = json.loads(resp["body"])
    assert body["unitId"] == world.w2.unit_id and body["fieldId"] == "f-box1"
    assert body["bbox"] == {"page": 1, "x0": 50.0, "y0": 216.0, "x1": 306.0, "y1": 284.0}
    assert body["expiresIn"] == 300 and "p001.png" in body["url"] and "X-Amz-Signature" in body["url"]


def test_user_b_gets_403(world):
    resp = mod.handler(api_event(world.w2.unit_id, "f-box1", sub=USER_B_SUB, groups="markets-analyst"))
    assert resp["statusCode"] == 403
    err = json.loads(resp["body"])["error"]
    assert err == {"code": "forbidden", "message": "You don't have access to this region.", "requestId": "req-1"}


def test_other_statuses(world):
    assert mod.handler(api_event(world.w2.unit_id, None, sub=None))["statusCode"] == 401
    assert mod.handler(api_event(world.held_unit_id, None))["statusCode"] == 404
    resp = mod.handler(api_event(world.chunk.unit_id, None))
    assert resp["statusCode"] == 404 and "no page region" in json.loads(resp["body"])["error"]["message"]
    assert mod.handler(api_event("nonsense", None))["statusCode"] == 400
