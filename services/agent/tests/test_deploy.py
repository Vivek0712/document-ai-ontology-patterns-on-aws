"""Request bodies of deploy.py validated against the bedrock-agentcore-control service model."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import boto3
from botocore.validate import ParamValidator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
import deploy  # noqa: E402

MODEL = boto3.client("bedrock-agentcore-control", region_name="us-east-1").meta.service_model
ACCOUNT = "000000000000"
FN = f"arn:aws:lambda:us-east-1:{ACCOUNT}:function:rp-search-units"


def valid(op: str, params: dict) -> None:
    report = ParamValidator().validate(params, MODEL.operation_model(op).input_shape)
    assert not report.has_errors(), report.generate_report()


def tools() -> dict:
    return deploy.tool_definitions(json.loads((deploy.REPO / "contracts/generated/mcp_tools.json").read_text()))


def test_gateway_schema_only_uses_supported_keys():
    t = tools()
    assert set(t) == {"search_units", "graph_hop", "get_region", "doc_status"}
    assert t["search_units"]["target"] == "search-units"

    def keys(node: dict) -> set[str]:
        out = set(node)
        for v in node.get("properties", {}).values():
            out |= keys(v)
        if "items" in node:
            out |= keys(node["items"])
        return out

    for spec in t.values():
        assert keys(spec["definition"]["inputSchema"]) <= set(deploy.SCHEMA_KEYS)
    tmpl = t["graph_hop"]["definition"]["inputSchema"]["properties"]["template"]["description"]
    assert "qi_sum_w2_box1_v1" in tmpl
    for injected in ("callerSub", "packageId", "pipelineVersion"):
        assert injected not in t["search_units"]["definition"]["inputSchema"]["properties"]


def test_requests_match_service_model():
    auth = deploy.jwt_authorizer("us-east-1", "us-east-1_ABC", "client123")
    assert auth["customJWTAuthorizer"]["discoveryUrl"].endswith("/us-east-1_ABC/.well-known/openid-configuration")
    valid("CreateGateway", {"name": deploy.GATEWAY_NAME, "roleArn": f"arn:aws:iam::{ACCOUNT}:role/x",
                            "protocolType": "MCP", "authorizerType": "CUSTOM_JWT", "authorizerConfiguration": auth,
                            "interceptorConfigurations": deploy.interceptor_config(FN)})
    for spec in tools().values():
        valid("CreateGatewayTarget", {
            "gatewayIdentifier": "rp-gateway-abcdefghij", "name": spec["target"],
            "targetConfiguration": {"mcp": {"lambda": {"lambdaArn": FN,
                                                       "toolSchema": {"inlinePayload": [spec["definition"]]}}}},
            "credentialProviderConfigurations": [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]})
    env = deploy.runtime_env(bucket="b", region="us-east-1", pool="p", client="c", gateway_url="https://g",
                             identity_mode="interceptor", code_sha="abc")
    valid("CreateAgentRuntime", {
        "agentRuntimeName": deploy.RUNTIME_NAME,
        "agentRuntimeArtifact": {"codeConfiguration": {"code": {"s3": {"bucket": "b", "prefix": "k.zip"}},
                                                       "runtime": "PYTHON_3_12", "entryPoint": ["main.py"]}},
        "roleArn": f"arn:aws:iam::{ACCOUNT}:role/x", "networkConfiguration": {"networkMode": "PUBLIC"},
        "authorizerConfiguration": auth, "requestHeaderConfiguration": {"requestHeaderAllowlist": ["Authorization"]},
        "protocolConfiguration": {"serverProtocol": "HTTP"}, "environmentVariables": env})
    url = deploy.runtime_url("us-east-1", f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/rp_agent-x")
    assert url.startswith("https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3A")
    assert url.endswith("/invocations?qualifier=DEFAULT")


def test_bundle_layout(tmp_path):
    zp = deploy.build_bundle(tmp_path, install=False)
    names = set(zipfile.ZipFile(zp).namelist())
    for required in ("main.py", "rp_agent/runner.py", "rp_agent/citations.py", "evidence_core/config.py",
                     "lambdas/__init__.py", "lambdas/interceptor/tokens.py"):
        assert required in names
    assert not any("/tests/" in n or n.startswith("tests/") for n in names)


def test_policies():
    pol = deploy.runtime_policy(account=ACCOUNT, region="us-east-1", bucket="b", kms_key="arn:aws:kms:k")
    actions = {a for s in pol["Statement"] for a in ([s["Action"]] if isinstance(s["Action"], str) else s["Action"])}
    assert {"bedrock:InvokeModel", "s3:PutObject", "dynamodb:PutItem", "kms:Decrypt"} <= actions
    assert deploy.gateway_policy([FN])["Statement"][0]["Resource"] == [FN]
