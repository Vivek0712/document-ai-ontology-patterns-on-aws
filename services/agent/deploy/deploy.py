"""Deploy the agent plane: AgentCore Gateway (+ REQUEST interceptor, 4 Lambda targets) and
the AgentCore Runtime ``rp_agent`` by direct code deployment (zip in S3, no Docker, no CodeBuild).

Run through ``scripts/deploy_agentcore.sh``. Reads what CDK wrote to SSM (bucket, user pool,
Lambda ARNs), is idempotent (finds resources by name and updates them), and writes
``/rp/agent/{runtime-arn,runtime-url,gateway-id,gateway-url}`` to SSM plus
``build/agentcore/outputs.json`` for the API stack.

APIs (boto3 ``bedrock-agentcore-control``): CreateGateway/UpdateGateway (CUSTOM_JWT inbound,
``interceptorConfigurations`` REQUEST with ``passRequestHeaders``), CreateGatewayTarget
(``mcp.lambda`` with inline tool schema, ``GATEWAY_IAM_ROLE``), CreateAgentRuntime /
UpdateAgentRuntime (``codeConfiguration`` S3 zip, ``PYTHON_3_12``, ``entryPoint: [main.py]``,
``customJWTAuthorizer.allowedClients``, ``requestHeaderAllowlist: [Authorization]``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

REPO = Path(__file__).resolve().parents[3]
BUILD = REPO / "build" / "agentcore"
GATEWAY_NAME = "rp-gateway"
RUNTIME_NAME = "rp_agent"
INTERCEPTOR_FUNCTION = "rp-interceptor"
TOOL_FUNCTIONS = {"search_units": "rp-search-units", "graph_hop": "rp-graph-hop", "get_region": "rp-get-region",
                  "doc_status": "rp-doc-status"}
GATEWAY_ROLE = "rp-agentcore-gateway"
RUNTIME_ROLE = "rp-agentcore-runtime"
BUNDLE_PREFIX = "deploy/agentcore"
SCHEMA_KEYS = ("type", "description", "properties", "required", "items")


# --- pure helpers (unit-tested) --------------------------------------------------------------

def gateway_schema(node: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema -> AgentCore Gateway SchemaDefinition (type/description/properties/required/items).
    Enums and bounds the Gateway cannot express are folded into the description."""
    hints = []
    if "enum" in node:
        hints.append("one of: " + ", ".join(map(str, node["enum"])))
    for key, label in (("minimum", "min"), ("maximum", "max"), ("minLength", "min length"),
                       ("maxLength", "max length")):
        if key in node:
            hints.append(f"{label} {node[key]}")
    if isinstance(node.get("additionalProperties"), dict):
        hints.append(f"map of {node['additionalProperties'].get('type', 'value')} values")
    out: dict[str, Any] = {"type": node.get("type", "string")}
    desc = " ".join([node.get("description", "")] + ([f"({'; '.join(hints)})"] if hints else [])).strip()
    if desc:
        out["description"] = desc
    if "properties" in node:
        out["properties"] = {k: gateway_schema(v) for k, v in node["properties"].items()}
    if node.get("required"):
        out["required"] = list(node["required"])
    if "items" in node:
        out["items"] = gateway_schema(node["items"])
    return out


def tool_definitions(mcp_tools: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """contracts/generated/mcp_tools.json -> {tool: {target, definition}}."""
    return {
        t["name"]: {"target": t["target"], "definition": {"name": t["name"], "description": t["description"],
                                                          "inputSchema": gateway_schema(t["inputSchema"])}}
        for t in mcp_tools["tools"]
    }


def discovery_url(region: str, user_pool_id: str) -> str:
    return f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"


def jwt_authorizer(region: str, user_pool_id: str, client_id: str) -> dict[str, Any]:
    # Access tokens carry client_id (not aud): allowedClients, not allowedAudience.
    return {"customJWTAuthorizer": {"discoveryUrl": discovery_url(region, user_pool_id),
                                    "allowedClients": [client_id]}}


def interceptor_config(interceptor_arn: str) -> list[dict[str, Any]]:
    return [{"interceptor": {"lambda": {"arn": interceptor_arn}}, "interceptionPoints": ["REQUEST"],
             "inputConfiguration": {"passRequestHeaders": True}}]


def runtime_url(region: str, runtime_arn: str) -> str:
    return (f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{quote(runtime_arn, safe='')}"
            "/invocations?qualifier=DEFAULT")


def runtime_env(*, bucket: str, region: str, pool: str, client: str, gateway_url: str, identity_mode: str,
                code_sha: str) -> dict[str, str]:
    return {"RP_ENV": "demo", "RP_CONFIG_SOURCE": "s3", "RP_DATA_BUCKET": bucket, "AWS_REGION": region,
            "RP_USER_POOL_ID": pool, "RP_USER_POOL_CLIENT_ID": client, "RP_GATEWAY_URL": gateway_url,
            "RP_IDENTITY_INJECTION": identity_mode, "RP_GOLDEN_PATH": "golden.jsonl", "RP_CODE_SHA": code_sha,
            "RP_LOG_LEVEL": "INFO", "RP_TABLE_ACCESS_LOG": "rp-access-log"}


def trust_policy() -> dict[str, Any]:
    return {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
                                                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                                                    "Action": "sts:AssumeRole"}]}


def gateway_policy(function_arns: list[str]) -> dict[str, Any]:
    return {"Version": "2012-10-17", "Statement": [
        {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": function_arns}]}


def runtime_policy(*, account: str, region: str, bucket: str, kms_key: str | None) -> dict[str, Any]:
    tables = [f"arn:aws:dynamodb:{region}:{account}:table/{t}" for t in ("rp-runs", "rp-documents")]
    statements: list[dict[str, Any]] = [
        {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
         "Resource": ["arn:aws:bedrock:*::foundation-model/*",
                      f"arn:aws:bedrock:{region}:{account}:inference-profile/*"]},
        {"Effect": "Allow", "Action": "s3:GetObject",
         "Resource": [f"arn:aws:s3:::{bucket}/config/*", f"arn:aws:s3:::{bucket}/{BUNDLE_PREFIX}/*"]},
        {"Effect": "Allow", "Action": "s3:PutObject", "Resource": f"arn:aws:s3:::{bucket}/runs/*"},
        {"Effect": "Allow", "Action": ["dynamodb:PutItem", "dynamodb:Query", "dynamodb:GetItem"], "Resource": tables},
        {"Effect": "Allow", "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                                       "logs:DescribeLogStreams", "logs:DescribeLogGroups"],
         "Resource": f"arn:aws:logs:{region}:{account}:log-group:/aws/bedrock-agentcore/runtimes/*"},
        {"Effect": "Allow", "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords",
                                       "cloudwatch:PutMetricData"], "Resource": "*"},
        {"Effect": "Allow", "Action": ["bedrock-agentcore:GetWorkloadAccessToken",
                                       "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                                       "bedrock-agentcore:GetWorkloadAccessTokenForUserId"],
         "Resource": f"arn:aws:bedrock-agentcore:{region}:{account}:workload-identity-directory/default*"},
    ]
    if kms_key:
        statements.append({"Effect": "Allow", "Action": ["kms:Decrypt", "kms:GenerateDataKey"], "Resource": kms_key})
    return {"Version": "2012-10-17", "Statement": statements}


# --- bundle ----------------------------------------------------------------------------------

def build_bundle(out_dir: Path = BUILD, *, install: bool = True) -> Path:
    """Agent code + evidence_core + the token verifier + golden set + linux/arm64 wheels -> zip."""
    src = out_dir / "bundle"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    agent = REPO / "services" / "agent"
    shutil.copy2(agent / "main.py", src / "main.py")
    ignore = shutil.ignore_patterns("__pycache__", "tests", "*.pyc")
    shutil.copytree(agent / "rp_agent", src / "rp_agent", ignore=ignore)
    shutil.copytree(REPO / "packages" / "evidence-core" / "evidence_core", src / "evidence_core", ignore=ignore)
    (src / "lambdas" / "interceptor").mkdir(parents=True)
    shutil.copy2(REPO / "services" / "lambdas" / "__init__.py", src / "lambdas" / "__init__.py")
    for name in ("__init__.py", "tokens.py"):
        shutil.copy2(REPO / "services" / "lambdas" / "interceptor" / name, src / "lambdas" / "interceptor" / name)
    golden = REPO / "eval" / "golden.jsonl"
    if golden.is_file():
        shutil.copy2(golden, src / "golden.jsonl")
    else:
        print("warning: eval/golden.jsonl missing; the golden-set check will be off", file=sys.stderr)
    if install:
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pip", "install", "-q", "-r", str(agent / "requirements.txt"),
             "--platform", "manylinux2014_aarch64", "--only-binary=:all:", "--python-version", "3.12",
             "--implementation", "cp", "--target", str(src), "--upgrade"],
            check=True,
        )
    zip_path = out_dir / "rp_agent.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                info = zipfile.ZipInfo(str(path.relative_to(src)), date_time=(2026, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, path.read_bytes())
    return zip_path


# --- AWS --------------------------------------------------------------------------------------

@dataclass
class Aws:
    session: Any
    region: str

    def client(self, name: str) -> Any:
        return self.session.client(name, region_name=self.region)


def ssm_get(aws: Aws, name: str, required: bool = True) -> str | None:
    try:
        return aws.client("ssm").get_parameter(Name=name)["Parameter"]["Value"]
    except aws.client("ssm").exceptions.ParameterNotFound:
        if required:
            raise SystemExit(f"SSM parameter {name} not found; deploy the CDK stacks first") from None
        return None


def ssm_put(aws: Aws, name: str, value: str) -> None:
    aws.client("ssm").put_parameter(Name=name, Value=value, Type="String", Overwrite=True)


def ensure_role(aws: Aws, name: str, policy: dict[str, Any]) -> str:
    iam = aws.client("iam")
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
    except iam.exceptions.NoSuchEntityException:
        created = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust_policy()),
                                  Description="Regionproof AgentCore", Tags=[{"Key": "app", "Value": "regionproof"}])
        arn = created["Role"]["Arn"]
        time.sleep(10)  # IAM propagation before AgentCore assumes it
    iam.put_role_policy(RoleName=name, PolicyName=f"{name}-inline", PolicyDocument=json.dumps(policy))
    return arn


def wait_ready(get: Any, what: str, timeout: int = 600) -> dict[str, Any]:
    deadline = time.time() + timeout
    while True:
        resp = get()
        status = resp.get("status")
        if status == "READY":
            return resp
        if status in ("FAILED", "UPDATE_UNSUCCESSFUL", "CREATE_FAILED", "UPDATE_FAILED"):
            raise SystemExit(f"{what} is {status}: {resp.get('statusReasons') or resp.get('failureReason')}")
        if time.time() > deadline:
            raise SystemExit(f"{what} not READY after {timeout}s (status {status})")
        time.sleep(5)


def find_gateway(ctl: Any, name: str) -> str | None:
    token = None
    while True:
        resp = ctl.list_gateways(**({"nextToken": token} if token else {}))
        for g in resp.get("items", []):
            if g["name"] == name:
                return g["gatewayId"]
        token = resp.get("nextToken")
        if not token:
            return None


def find_runtime(ctl: Any, name: str) -> str | None:
    token = None
    while True:
        resp = ctl.list_agent_runtimes(**({"nextToken": token} if token else {}))
        for r in resp.get("agentRuntimes", []):
            if r["agentRuntimeName"] == name:
                return r["agentRuntimeId"]
        token = resp.get("nextToken")
        if not token:
            return None


def deploy_gateway(aws: Aws, *, role_arn: str, authorizer: dict[str, Any], interceptor_arn: str,
                   debug: bool) -> dict[str, Any]:
    ctl = aws.client("bedrock-agentcore-control")
    common: dict[str, Any] = {
        "name": GATEWAY_NAME, "roleArn": role_arn, "protocolType": "MCP", "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": authorizer, "interceptorConfigurations": interceptor_config(interceptor_arn),
        "description": "Regionproof tools (search_units, graph_hop, get_region, doc_status)",
    }
    if debug:
        common["exceptionLevel"] = "DEBUG"
    gid = find_gateway(ctl, GATEWAY_NAME)
    if gid is None:
        gid = ctl.create_gateway(**common)["gatewayId"]
    else:
        ctl.update_gateway(gatewayIdentifier=gid, **common)
    return wait_ready(lambda: ctl.get_gateway(gatewayIdentifier=gid), f"gateway {GATEWAY_NAME}")


def deploy_targets(aws: Aws, gateway_id: str, tools: dict[str, dict[str, Any]], arns: dict[str, str]) -> None:
    ctl = aws.client("bedrock-agentcore-control")
    existing = {t["name"]: t["targetId"]
                for t in ctl.list_gateway_targets(gatewayIdentifier=gateway_id).get("items", [])}
    for tool, spec in tools.items():
        config = {"mcp": {"lambda": {"lambdaArn": arns[tool],
                                     "toolSchema": {"inlinePayload": [spec["definition"]]}}}}
        creds = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]
        if spec["target"] in existing:
            tid = existing[spec["target"]]
            ctl.update_gateway_target(gatewayIdentifier=gateway_id, targetId=tid, name=spec["target"],
                                      targetConfiguration=config, credentialProviderConfigurations=creds)
        else:
            tid = ctl.create_gateway_target(gatewayIdentifier=gateway_id, name=spec["target"],
                                            description=f"{tool}@1 -> {TOOL_FUNCTIONS[tool]}",
                                            targetConfiguration=config,
                                            credentialProviderConfigurations=creds)["targetId"]
        wait_ready(lambda tid=tid: ctl.get_gateway_target(gatewayIdentifier=gateway_id, targetId=tid),
                   f"target {spec['target']}")


def deploy_runtime(aws: Aws, *, role_arn: str, bucket: str, key: str, authorizer: dict[str, Any],
                   env: dict[str, str]) -> dict[str, Any]:
    ctl = aws.client("bedrock-agentcore-control")
    body: dict[str, Any] = {
        "agentRuntimeArtifact": {"codeConfiguration": {"code": {"s3": {"bucket": bucket, "prefix": key}},
                                                       "runtime": "PYTHON_3_12", "entryPoint": ["main.py"]}},
        "roleArn": role_arn,
        "networkConfiguration": {"networkMode": "PUBLIC"},
        "authorizerConfiguration": authorizer,
        "requestHeaderConfiguration": {"requestHeaderAllowlist": ["Authorization"]},
        "protocolConfiguration": {"serverProtocol": "HTTP"},
        "environmentVariables": env,
        "description": "Regionproof answer agent (Strands)",
    }
    rid = find_runtime(ctl, RUNTIME_NAME)
    if rid is None:
        rid = ctl.create_agent_runtime(agentRuntimeName=RUNTIME_NAME, **body)["agentRuntimeId"]
    else:
        ctl.update_agent_runtime(agentRuntimeId=rid, **body)
    return wait_ready(lambda: ctl.get_agent_runtime(agentRuntimeId=rid), f"runtime {RUNTIME_NAME}", timeout=900)


def git_sha() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short=12", "HEAD"],  # noqa: S603,S607
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    ap.add_argument("--identity-mode", choices=["interceptor", "agent_hook"], default="interceptor",
                    help="agent_hook = named fallback: the Strands BeforeToolCallEvent hook injects identity")
    ap.add_argument("--debug-errors", action="store_true", help="Gateway exceptionLevel DEBUG")
    ap.add_argument("--bundle-only", action="store_true", help="build the zip and the tool schemas, call no AWS API")
    ap.add_argument("--skip-gateway", action="store_true")
    ap.add_argument("--skip-runtime", action="store_true")
    args = ap.parse_args(argv)

    tools = tool_definitions(json.loads((REPO / "contracts" / "generated" / "mcp_tools.json").read_text()))
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "gateway_tools.json").write_text(json.dumps(tools, indent=2))
    zip_path = build_bundle()
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()[:16]
    print(f"bundle {zip_path} ({zip_path.stat().st_size // 1024} KiB, sha {digest})")
    if args.bundle_only:
        return 0

    import boto3

    aws = Aws(session=boto3.Session(profile_name=args.profile or None, region_name=args.region), region=args.region)
    account = aws.client("sts").get_caller_identity()["Account"]
    bucket = ssm_get(aws, "/rp/data-bucket")
    pool = ssm_get(aws, "/rp/user-pool-id")
    client = ssm_get(aws, "/rp/user-pool-client-id")
    assert bucket and pool and client
    authorizer = jwt_authorizer(args.region, pool, client)
    outputs: dict[str, Any] = {"region": args.region}

    gateway_url = ssm_get(aws, "/rp/agent/gateway-url", required=False)
    if not args.skip_gateway:
        arns = {t: ssm_get(aws, f"/rp/lambda/{fn}/arn") for t, fn in TOOL_FUNCTIONS.items()}
        interceptor_arn = ssm_get(aws, f"/rp/lambda/{INTERCEPTOR_FUNCTION}/arn")
        role = (ssm_get(aws, "/rp/agent/gateway-role-arn", required=False)
                or ensure_role(aws, GATEWAY_ROLE, gateway_policy([*arns.values(), interceptor_arn])))  # type: ignore[list-item]
        gw = deploy_gateway(aws, role_arn=role, authorizer=authorizer, interceptor_arn=str(interceptor_arn),
                            debug=args.debug_errors)
        deploy_targets(aws, gw["gatewayId"], tools, arns)  # type: ignore[arg-type]
        gateway_url = gw["gatewayUrl"]
        ssm_put(aws, "/rp/agent/gateway-id", gw["gatewayId"])
        ssm_put(aws, "/rp/agent/gateway-url", gateway_url)
        outputs.update(gatewayId=gw["gatewayId"], gatewayUrl=gateway_url)
    if not gateway_url:
        raise SystemExit("no gateway URL: deploy the gateway first (drop --skip-gateway)")

    if not args.skip_runtime:
        key = f"{BUNDLE_PREFIX}/rp_agent-{digest}.zip"
        aws.client("s3").upload_file(str(zip_path), bucket, key)
        enc = aws.client("s3").get_bucket_encryption(Bucket=bucket)["ServerSideEncryptionConfiguration"]
        kms = next((r["ApplyServerSideEncryptionByDefault"].get("KMSMasterKeyID") for r in enc["Rules"]
                    if "ApplyServerSideEncryptionByDefault" in r), None)
        role = (ssm_get(aws, "/rp/agent/runtime-role-arn", required=False)
                or ensure_role(aws, RUNTIME_ROLE, runtime_policy(account=account, region=args.region, bucket=bucket,
                                                                 kms_key=kms)))
        env = runtime_env(bucket=bucket, region=args.region, pool=pool, client=client, gateway_url=gateway_url,
                          identity_mode=args.identity_mode, code_sha=git_sha())
        rt = deploy_runtime(aws, role_arn=role, bucket=bucket, key=key, authorizer=authorizer, env=env)
        url = runtime_url(args.region, rt["agentRuntimeArn"])
        ssm_put(aws, "/rp/agent/runtime-arn", rt["agentRuntimeArn"])
        ssm_put(aws, "/rp/agent/runtime-url", url)
        outputs.update(runtimeArn=rt["agentRuntimeArn"], runtimeUrl=url, runtimeVersion=rt.get("agentRuntimeVersion"),
                       bundleKey=key, identityMode=args.identity_mode)

    (BUILD / "outputs.json").write_text(json.dumps(outputs, indent=2))
    print(json.dumps(outputs, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
