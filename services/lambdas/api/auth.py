"""Caller identity from the Cognito authorizer claims (API Gateway REST, access token)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evidence_core.ids import parse_groups_claim, principals_for


@dataclass(frozen=True)
class Caller:
    sub: str
    username: str
    email: str | None
    groups: tuple[str, ...]
    client_id: str | None = None

    @property
    def principals(self) -> list[str]:
        return principals_for(self.sub, self.groups)

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> Caller:
        sub = str(claims.get("sub") or "")
        return cls(
            sub=sub,
            username=str(claims.get("username") or claims.get("cognito:username") or sub),
            email=claims.get("email"),
            groups=tuple(parse_groups_claim(claims.get("cognito:groups"))),
            client_id=claims.get("client_id"),
        )

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> Caller:
        claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
        return cls.from_claims(claims)

    @property
    def authenticated(self) -> bool:
        return bool(self.sub)
