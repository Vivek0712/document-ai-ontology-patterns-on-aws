"""Cognito access-token verification (shared by the interceptor and the agent's fallback hook).

Checks: RS256 signature against the user pool JWKS, ``iss`` = the pool, ``token_use`` =
``access``, ``client_id`` = the web app client, ``exp``/``iat`` with 60 s leeway. Access
tokens carry ``client_id``, not ``aud``, so audience verification is off by design.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jwt
from evidence_core.ids import parse_groups_claim

LEEWAY_SECONDS = 60


class TokenError(Exception):
    """The bearer token is missing or not a valid access token for this app."""


@dataclass(frozen=True)
class Caller:
    sub: str
    groups: list[str]
    username: str | None = None
    claims: dict[str, Any] = field(default_factory=dict)


def bearer(value: str | None) -> str:
    if not value:
        raise TokenError("missing Authorization header")
    scheme, _, token = value.strip().partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise TokenError("Authorization is not a Bearer token")
    return token.strip()


KeyResolver = Callable[[str], Any]
"""token -> signing key (``jwt.PyJWKClient.get_signing_key_from_jwt(token).key`` in AWS)."""


@dataclass
class AccessTokenVerifier:
    region: str
    user_pool_id: str
    client_id: str
    key_resolver: KeyResolver | None = None

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    def _resolver(self) -> KeyResolver:
        if self.key_resolver is None:
            client = jwt.PyJWKClient(self.jwks_url, cache_keys=True, lifespan=3600)
            self.key_resolver = lambda token: client.get_signing_key_from_jwt(token).key
        return self.key_resolver

    def verify(self, token: str) -> Caller:
        try:
            key = self._resolver()(token)
            claims = jwt.decode(
                token,
                key=key,
                algorithms=["RS256"],
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"verify_aud": False, "require": ["exp", "iat", "sub", "iss"]},
            )
        except (jwt.PyJWTError, KeyError, ValueError) as e:
            raise TokenError(f"invalid token: {type(e).__name__}") from e
        if claims.get("token_use") != "access":
            raise TokenError("not an access token")
        if claims.get("client_id") != self.client_id:
            raise TokenError("token was issued to another app client")
        return Caller(
            sub=str(claims["sub"]),
            groups=parse_groups_claim(claims.get("cognito:groups")),
            username=claims.get("username"),
            claims=claims,
        )
