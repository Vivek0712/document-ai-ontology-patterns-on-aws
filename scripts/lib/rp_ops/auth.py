"""Cognito access tokens for the operator scripts and the eval harness.

Order: ``RP_ACCESS_TOKEN`` env (already signed in) -> SRP (``pycognito``, same flow the UI uses)
-> ``ADMIN_USER_PASSWORD_AUTH`` (only if the app client allows it). The API only accepts access
tokens, so this never returns an ID token unless asked for the 401 smoke check.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from .aws import Aws, log

DEMO_USERS: dict[str, str] = {
    "user-a": "user-a@example.com",  # lending-analyst, owner of pkg-lending-01
    "user-b": "user-b@example.com",  # markets-analyst
}
DEMO_GROUPS: dict[str, str] = {"user-a": "lending-analyst", "user-b": "markets-analyst"}


@dataclass(frozen=True)
class Tokens:
    access_token: str
    id_token: str | None
    method: str


def demo_password(aws: Aws) -> str:
    pw = os.environ.get("RP_DEMO_PASSWORD") or aws.ssm("demo_password", required=False, decrypt=True)
    if not pw:
        raise SystemExit("no demo password: set RP_DEMO_PASSWORD or run scripts/seed.sh (writes /rp/demo/password)")
    return pw


def sign_in(aws: Aws, user: str = "user-a", password: str | None = None) -> Tokens:
    env_token = os.environ.get("RP_ACCESS_TOKEN")
    if env_token:
        return Tokens(access_token=env_token, id_token=None, method="env")
    username = DEMO_USERS.get(user, user)
    pw = password or demo_password(aws)
    pool_id = aws.ssm("user_pool_id")
    client_id = aws.ssm("user_pool_client_id")
    assert pool_id and client_id
    try:
        from pycognito.aws_srp import AWSSRP  # type: ignore[import-untyped]

        srp = AWSSRP(username=username, password=pw, pool_id=pool_id, client_id=client_id,
                     client=aws.client("cognito-idp"))
        res = srp.authenticate_user()["AuthenticationResult"]
        return Tokens(access_token=res["AccessToken"], id_token=res.get("IdToken"), method="srp")
    except ImportError:
        log("pycognito not installed (pip install -r eval/requirements.txt); trying ADMIN_USER_PASSWORD_AUTH")
    res = aws.client("cognito-idp").admin_initiate_auth(
        UserPoolId=pool_id,
        ClientId=client_id,
        AuthFlow="ADMIN_USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": pw},
    )["AuthenticationResult"]
    return Tokens(access_token=res["AccessToken"], id_token=res.get("IdToken"), method="admin")


def bearer(tokens: Tokens, kind: Literal["access", "id"] = "access") -> str:
    tok = tokens.access_token if kind == "access" else tokens.id_token
    if not tok:
        raise SystemExit(f"no {kind} token available (method {tokens.method})")
    return f"Bearer {tok}"
