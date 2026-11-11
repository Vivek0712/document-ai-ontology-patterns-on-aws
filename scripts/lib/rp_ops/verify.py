"""verify_account.sh checks, run before a deploy. ``read_only`` skips every call that runs a model or a
service on data (Bedrock invoke, Textract, Comprehend) and keeps describe/list/get calls."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from botocore.exceptions import ClientError, EndpointConnectionError, UnknownServiceError
from evidence_core.env import Settings

from .aws import Aws, mask_account, mask_arn
from .warm import ECS_CLUSTER, ECS_SERVICE, load_models, neptune_status, ping_model

Status = Literal["PASS", "WARN", "FAIL", "SKIP"]
# Fictitious text for the Comprehend probe: 900-series SSN (never issued), example.com address.
PII_PROBE = "Maya Fictus, SSN 900-12-0417, email maya.fictus@example.com, account 40917733205518."
TEXTRACT_FEATURES = ["LAYOUT", "TABLES", "FORMS"]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def _safe(name: str, fn: Callable[[], Check]) -> Check:
    try:
        return fn()
    except (ClientError, EndpointConnectionError, UnknownServiceError) as exc:
        code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
        return Check(name, "FAIL", f"{code}: {str(exc)[:200]}")


def check_identity(aws: Aws) -> Check:
    ident = aws.client("sts").get_caller_identity()
    expected = os.environ.get("RP_EXPECTED_ACCOUNT")
    acct = ident["Account"]
    arn = mask_arn(ident["Arn"])
    if expected and acct != expected:
        return Check("caller identity", "FAIL", f"account {mask_account(acct)} is not RP_EXPECTED_ACCOUNT ({arn})")
    note = "" if expected else " (set RP_EXPECTED_ACCOUNT to enforce)"
    return Check("caller identity", "PASS", f"{arn}{note}")


def _models_region() -> str:
    """The Region the model ids in config/models.yaml were chosen for (``region``, default us-east-1)."""
    from evidence_core.config import parse_models

    root = Settings.from_env().repo_root
    return parse_models((root / "config" / "models.yaml").read_text(encoding="utf-8")).region


def check_region(aws: Aws) -> Check:
    """The session Region must match the Region of the model ids in config/models.yaml."""
    r = aws.region_name
    expected = _models_region()
    detail = r if r == expected else f"{r} (config/models.yaml: {expected})"
    return Check("region", "PASS" if r == expected else "FAIL", detail)


def check_model_listed(aws: Aws, key: str, model_id: str) -> Check:
    br = aws.client("bedrock")
    if model_id.split(".", 1)[0] in ("us", "eu", "apac", "global"):
        prof = br.get_inference_profile(inferenceProfileIdentifier=model_id)
        return Check(f"model listed {key}", "PASS" if prof.get("status") == "ACTIVE" else "WARN",
                     f"{model_id} inference profile {prof.get('status')}")
    fm = br.get_foundation_model(modelIdentifier=model_id)["modelDetails"]
    status = fm.get("modelLifecycle", {}).get("status", "?")
    return Check(f"model listed {key}", "PASS" if status == "ACTIVE" else "WARN", f"{model_id} {status}")


def check_model_invoke(aws: Aws, key: str) -> Check:
    spec = load_models()[key]
    p = ping_model(aws, key, spec)
    return Check(f"model invoke {key}", "PASS" if p.ok else "FAIL", f"{p.model_id}: {p.detail} ({p.ms} ms)")


def fixture_pdf(repo_root: Path) -> Path | None:
    samples = repo_root / "data" / "samples" / "lending"
    scans = sorted(samples.glob("*scan*.pdf"))
    return scans[0] if scans else next(iter(sorted(samples.glob("*.pdf"))), None)


def check_textract(aws: Aws) -> Check:
    pdf = fixture_pdf(Settings.from_env().repo_root)
    if pdf is None:
        return Check("textract sync LAYOUT", "SKIP", "no fixture PDF yet (run `make data`)")
    t0 = time.monotonic()
    resp = aws.client("textract").analyze_document(Document={"Bytes": pdf.read_bytes()},
                                                    FeatureTypes=TEXTRACT_FEATURES)
    kinds: dict[str, int] = {}
    for b in resp.get("Blocks", []):
        kinds[b["BlockType"]] = kinds.get(b["BlockType"], 0) + 1
    layout = sum(v for k, v in kinds.items() if k.startswith("LAYOUT_"))
    ms = int((time.monotonic() - t0) * 1000)
    detail = (f"{pdf.name}: {layout} LAYOUT blocks, {kinds.get('TABLE', 0)} tables, "
              f"{kinds.get('KEY_VALUE_SET', 0)} key-value blocks, {ms} ms")
    return Check("textract sync LAYOUT", "PASS" if layout else "FAIL", detail)


def check_comprehend(aws: Aws) -> Check:
    ents = aws.client("comprehend").detect_pii_entities(Text=PII_PROBE, LanguageCode="en")["Entities"]
    types = sorted({e["Type"] for e in ents})
    ok = "SSN" in types or "BANK_ACCOUNT_NUMBER" in types
    return Check("comprehend PII", "PASS" if ok else "WARN", f"entities {types} on a fictitious fixture")


def check_aoss(aws: Aws) -> Check:
    found = aws.client("opensearchserverless").batch_get_collection(names=["rp-units"])["collectionDetails"]
    if not found:
        return Check("opensearch serverless rp-units", "SKIP", "not deployed yet")
    c = found[0]
    group = c.get("collectionGroupName")
    if not group:  # without a NextGen group the collection is Classic, with an OCU floor billed 24/7
        return Check("opensearch serverless rp-units", "WARN",
                     f"{c['status']} Classic (no collection group): idle OCU floor is billed; redeploy rp-knowledge")
    return Check("opensearch serverless rp-units", "PASS" if c["status"] == "ACTIVE" else "WARN",
                 f"{c['status']} {c.get('type', '')} group {group} {c.get('collectionEndpoint', '')}")


def check_neptune(aws: Aws) -> Check:
    status = neptune_status(aws)
    if status is None:
        return Check("neptune rp-graph", "SKIP", "not deployed yet")
    return Check("neptune rp-graph", "PASS" if status == "available" else "WARN", status)


def check_docling(aws: Aws) -> Check:
    try:
        svcs = aws.client("ecs").describe_services(cluster=ECS_CLUSTER, services=[ECS_SERVICE]).get("services", [])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ClusterNotFoundException":
            return Check("fargate rp-docling", "SKIP", "not deployed yet")
        raise
    if not svcs or svcs[0].get("status") != "ACTIVE":
        return Check("fargate rp-docling", "SKIP", "not deployed yet")
    s = svcs[0]
    return Check("fargate rp-docling", "PASS",
                 f"desired {s['desiredCount']} running {s['runningCount']} (0 is normal until prewarm)")


def check_agentcore(aws: Aws) -> Check:
    try:
        ctl = aws.client("bedrock-agentcore-control")
    except UnknownServiceError:
        return Check("agentcore control plane", "WARN", "boto3 too old for bedrock-agentcore-control")
    names: list[str] = []
    for page in ctl.get_paginator("list_agent_runtimes").paginate():
        names += [str(r.get("agentRuntimeName")) for r in page.get("agentRuntimes", [])]
    ours = "rp_agent deployed" if "rp_agent" in names else "rp_agent not deployed yet"
    return Check("agentcore control plane", "PASS", f"reachable; {ours}")


def check_quotas(aws: Aws) -> Check:
    sq = aws.client("service-quotas")
    notes: list[str] = []
    for page in sq.get_paginator("list_service_quotas").paginate(ServiceCode="apigateway"):
        for q in page["Quotas"]:
            if "timeout" in q["QuotaName"].lower():
                notes.append(f"{q['QuotaName']} = {q['Value']:g}")
    lam = sq.get_service_quota(ServiceCode="lambda", QuotaCode="L-B99A9384")["Quota"]
    notes.append(f"Lambda concurrent executions = {lam['Value']:g}")
    return Check("quotas", "PASS", "; ".join(notes) or "no API Gateway timeout quota listed")


def run_checks(aws: Aws, *, read_only: bool) -> list[Check]:
    checks = [_safe("caller identity", lambda: check_identity(aws)), check_region(aws)]
    models = load_models()
    for key, spec in models.items():
        checks.append(_safe(f"model listed {key}", lambda k=key, s=spec: check_model_listed(aws, k, s.model_id)))
    invoking = [
        ("textract sync LAYOUT", lambda: check_textract(aws)),
        ("comprehend PII", lambda: check_comprehend(aws)),
    ]
    if read_only:
        checks += [Check(f"model invoke {k}", "SKIP", "--read-only") for k in models]
        checks += [Check(name, "SKIP", "--read-only") for name, _ in invoking]
    else:
        checks += [_safe(f"model invoke {k}", lambda k=k: check_model_invoke(aws, k)) for k in models]
        checks += [_safe(name, fn) for name, fn in invoking]
    checks += [
        _safe("opensearch serverless rp-units", lambda: check_aoss(aws)),
        _safe("neptune rp-graph", lambda: check_neptune(aws)),
        _safe("fargate rp-docling", lambda: check_docling(aws)),
        _safe("agentcore control plane", lambda: check_agentcore(aws)),
        _safe("quotas", lambda: check_quotas(aws)),
    ]
    return checks


def render(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    return "\n".join(f"  {c.status:<4}  {c.name:<{width}}  {c.detail}" for c in checks)
