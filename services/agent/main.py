"""AgentCore Runtime entrypoint (``rp_agent``): POST /invocations with an ``AskRequest`` body.

The Runtime's ``customJWTAuthorizer`` validated the Cognito access token and forwards the
``Authorization`` header (``requestHeaderAllowlist``). The handler re-verifies it to get
``sub``/``groups`` for the run record and the fallback identity hook, checks the package ACL and the
pipeline version (the streaming path reaches the Runtime without ``rp-api-ask``), then streams the ask
as Server-Sent Events (each yielded dict becomes ``data: <json>\\n\\n``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from evidence_core.config import load_pipeline
from evidence_core.models.answer import AskRequest, ErrorEvent
from evidence_core.models.golden import load_golden
from lambdas.interceptor.tokens import AccessTokenVerifier, TokenError, bearer
from pydantic import ValidationError
from rp_agent.access import PackageDenied, PackageGate
from rp_agent.driver import StrandsDriver
from rp_agent.runner import AskRunner, CallerIdentity
from rp_agent.runs import AwsRunStore
from rp_agent.settings import AgentSettings

app = BedrockAgentCoreApp()


@lru_cache(maxsize=1)
def settings() -> AgentSettings:
    return AgentSettings.from_env()


@lru_cache(maxsize=1)
def verifier() -> AccessTokenVerifier:
    s = settings().common
    if not s.user_pool_id or not s.user_pool_client_id:
        raise RuntimeError("RP_USER_POOL_ID and RP_USER_POOL_CLIENT_ID must be set")
    return AccessTokenVerifier(region=s.region, user_pool_id=s.user_pool_id, client_id=s.user_pool_client_id)


@lru_cache(maxsize=1)
def package_gate() -> PackageGate:
    return PackageGate.from_settings(settings().common)


@lru_cache(maxsize=4)
def pipeline(pv: str) -> Any:
    return load_pipeline(pv)


@lru_cache(maxsize=1)
def runner() -> AskRunner:
    s = settings()
    if not s.gateway_url:
        raise RuntimeError("RP_GATEWAY_URL must be set")
    return AskRunner(
        pipeline_loader=pipeline,
        driver_factory=StrandsDriver,
        run_store=AwsRunStore.from_settings(s.common),
        golden=load_golden(s.golden_path) if s.golden_path else [],
        gateway_url=s.gateway_url,
        identity_mode=s.identity_mode,
        region=s.common.region,
    )


def _authorization(context: Any) -> str | None:
    headers = getattr(context, "request_headers", None) or {}
    return next((v for k, v in headers.items() if k.lower() == "authorization"), None)


@app.entrypoint
async def invoke(payload: dict[str, Any], context: Any) -> AsyncIterator[dict[str, Any]]:
    try:
        request = AskRequest.load(payload or {})
    except ValidationError as e:
        yield ErrorEvent(code="bad_request", message=f"invalid ask: {e.error_count()} problem(s)").dump()
        return
    auth = _authorization(context)
    try:
        caller = verifier().verify(bearer(auth))
    except TokenError:
        yield ErrorEvent(code="forbidden", message="Sign in again: the access token was not accepted.").dump()
        return
    try:
        package_gate().check(request.package_id, request.pipeline_version, caller.sub, list(caller.groups))
    except PackageDenied as denied:
        yield ErrorEvent(code=denied.code, message=denied.message).dump()
        return
    identity = CallerIdentity(sub=caller.sub, groups=caller.groups, authorization=str(auth))
    async for event in runner().run(request, identity, session_id=getattr(context, "session_id", None)):
        yield event


if __name__ == "__main__":
    app.run()
