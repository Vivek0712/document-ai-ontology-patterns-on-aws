from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from boto3.dynamodb.conditions import Key
from evidence_core import s3keys
from evidence_core.ddb import to_ddb
from evidence_core.ids import doc_id_for, unit_id
from evidence_core.models.answer import RunRecord, RunSummary
from rp_ops import agentcore, reset, runs, seed
from rp_ops.api import parse_sse
from rp_ops.aws import Aws, lambda_name, mask_account, mask_arn
from rp_ops.verify import Check, render
from rp_ops.warm import unwrap_tool_result

from .conftest import BUCKET

OWNER = "7c41e2aa-0000-4000-8000-00000000000a"
PKG = seed.DEMO_PACKAGE_ID


# --- pure helpers -----------------------------------------------------------------------------------

def test_lambda_name_follows_contract() -> None:
    assert lambda_name("get_region_http") == "rp-get-region-http"
    assert lambda_name("search_units") == "rp-search-units"


def test_mask_account_never_prints_twelve_digits() -> None:
    assert mask_account("arn:aws:iam::123456789012:role/x") == "arn:aws:iam::********9012:role/x"


def test_mask_arn_hides_account_and_session_name() -> None:
    arn = "arn:aws:sts::123456789012:assumed-role/AWSReservedSSO_Admin_abc/someone@example.com"
    assert mask_arn(arn) == "arn:aws:sts::********9012:assumed-role/AWSReservedSSO_Admin_abc/****"
    assert mask_arn("arn:aws:iam::123456789012:user/alice") == "arn:aws:iam::********9012:user/****"
    assert mask_arn("arn:aws:iam::123456789012:role/x") == "arn:aws:iam::********9012:role/x"


def test_aws_session_uses_the_default_chain_without_a_profile(monkeypatch) -> None:
    """No profile default: without AWS_PROFILE the session names no profile (the default credential chain)."""
    import boto3

    seen: dict = {}
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("RP_NO_PROFILE", raising=False)
    monkeypatch.setattr(boto3, "Session", lambda **kw: seen.update(kw) or object())
    Aws(region="us-east-1").session  # noqa: B018 (cached property under test)
    assert seen == {"profile_name": None, "region_name": "us-east-1"}
    seen.clear()
    monkeypatch.setenv("AWS_PROFILE", "someone-else")
    Aws(region="us-east-1").session  # noqa: B018
    assert seen["profile_name"] == "someone-else"


def test_parse_sse_handles_buffered_body_comments_and_multiline() -> None:
    body = ': keepalive\n\ndata: {"type": "text",\ndata: "delta": "a"}\n\ndata: {"type":"text","delta":"b"}'
    assert [e["delta"] for e in parse_sse(body.splitlines(keepends=True))] == ["a", "b"]


def test_unwrap_tool_result_shapes() -> None:
    assert unwrap_tool_result({"units": []}) == {"units": []}
    assert unwrap_tool_result({"content": [{"type": "text", "text": '{"rowCount": 2}'}]}) == {"rowCount": 2}
    assert unwrap_tool_result({"statusCode": 200, "body": '{"a": 1}'}) == {"a": 1}
    assert unwrap_tool_result(None) == {}
    with pytest.raises(RuntimeError, match="forbidden"):
        unwrap_tool_result({"error": {"code": "forbidden", "message": "no access"}})


def test_execution_matches_package() -> None:
    key = s3keys.bronze_key(OWNER, PKG, "w2.pdf")
    assert reset.execution_matches(json.dumps({"key": key, "pipelineVersion": "1.0"}), PKG)
    assert not reset.execution_matches(json.dumps({"key": key}), "pkg-other")
    assert not reset.execution_matches("not json", PKG)


def test_generated_password_meets_policy() -> None:
    pw = seed.generate_password()
    assert len(pw) == 20 and any(c.isupper() for c in pw) and any(c.islower() for c in pw)
    assert any(c.isdigit() for c in pw) and not pw.isalnum()


def test_render_checks() -> None:
    out = render([Check("region", "PASS", "us-east-1"), Check("textract", "SKIP", "--read-only")])
    assert "PASS  region" in out and "SKIP  textract" in out


# --- seed ----------------------------------------------------------------------------------------------

def test_seed_pipelines_pins_s3_version_and_checks_blocks(aws_env: Aws) -> None:
    items = seed.seed_pipelines(aws_env, BUCKET)
    assert [i.pipeline_version for i in items] == ["1.0", "2.3.1"]
    assert all(i.config_version_id for i in items)
    assert items[0].block_shas["retrieval"] == items[1].block_shas["retrieval"]
    assert items[0].config_sha != items[1].config_sha
    assert items[1].ontology_version and items[0].ontology_version is None
    row = aws_env.table("pipeline_versions").get_item(Key={"pipelineVersion": "2.3.1"})["Item"]
    assert row["modelIds"]["answer"] == "us.anthropic.claude-sonnet-4-6"
    assert row["mode"] == "evidence_units"


def test_seed_users_idempotent(aws_env: Aws, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("joserfc", reason="moto Cognito backend needs joserfc (eval/requirements.txt)")
    idp = aws_env.client("cognito-idp")
    pool = idp.create_user_pool(PoolName="rp-users")["UserPool"]["Id"]
    monkeypatch.setenv("RP_SSM_RP_USER_POOL_ID", pool)
    pw = seed.ensure_demo_password(aws_env, None)
    assert seed.ensure_demo_password(aws_env, None) == pw  # reused from SSM
    first = seed.seed_users(aws_env, pw)
    second = seed.seed_users(aws_env, pw)
    assert first == second and set(first) == {"user-a", "user-b"}
    groups = idp.admin_list_groups_for_user(UserPoolId=pool, Username="user-a@example.com")["Groups"]
    assert [g["GroupName"] for g in groups] == ["lending-analyst"]


def test_seed_package_and_upload(aws_env: Aws, tmp_path: Path) -> None:
    pkg = seed.seed_package(aws_env, OWNER)
    assert pkg.allowed_principals == [f"user:{OWNER}", "group:lending-analyst"]
    seed.seed_package(aws_env, OWNER)  # exists -> no overwrite, no error
    pdf = tmp_path / "w2_2025_maya_fictus.pdf"
    pdf.write_bytes(b"%PDF-1.7\n% fictitious\n")
    docs = seed.upload_documents(aws_env, BUCKET, pkg, [pdf])
    doc_id = doc_id_for(PKG, pdf.name)
    assert docs[0].doc_id == doc_id and docs[0].upload_state == "AWAITING_UPLOAD"
    head = aws_env.client("s3").head_object(Bucket=BUCKET, Key=s3keys.bronze_key(OWNER, PKG, pdf.name))
    assert head["ContentType"] == "application/pdf"
    assert aws_env.table("doc_acl").get_item(Key={"docId": doc_id})["Item"]["allowedPrincipals"][1] == \
        "group:lending-analyst"
    assert aws_env.table("packages").get_item(Key={"packageId": PKG})["Item"]["docCount"] == 1


def test_demo_packages_mirror_docgen_acl(aws_env: Aws) -> None:
    """The four demo packages, owned by user-a: lending packages for lending-analyst only, the quarterly reports and
    trade confirmations for both groups (as data/docgen/packages.py and the mock world define them)."""
    root = Path(__file__).resolve().parents[4] / "data" / "samples"
    want = {"pkg-lending-01": ["group:lending-analyst"], "pkg-lending-02": ["group:lending-analyst"],
            "pkg-quarterly-01": ["group:lending-analyst", "group:markets-analyst"],
            "pkg-tradeconf-01": ["group:lending-analyst", "group:markets-analyst"]}
    assert [s.package_id for s in seed.DEMO_PACKAGES] == list(want)
    for spec in seed.DEMO_PACKAGES:
        pkg = seed.seed_package(aws_env, OWNER, package_id=spec.package_id, samples_root=root)
        assert pkg.allowed_principals == [f"user:{OWNER}", *want[spec.package_id]]
        assert pkg.pipeline_versions == list(seed.PIPELINE_VERSIONS) and pkg.name
    assert seed.demo_package(OWNER, package_id="pkg-quarterly-01", samples_root=root).name.startswith("Quarterly")
    assert seed.demo_package(OWNER, package_id="pkg-custom").allowed_principals == \
        [f"user:{OWNER}", "group:lending-analyst"]  # a non-demo id keeps the single-package default
    with pytest.raises(SystemExit, match="unknown demo package"):
        seed.demo_spec("pkg-nope")


def test_seed_targets_resolve_sample_dirs(tmp_path: Path) -> None:
    from rp_ops.__main__ import build_parser, seed_targets

    def targets(*argv: str) -> list[tuple[str, Path]]:
        return seed_targets(build_parser().parse_args(["seed", "--samples-root", str(tmp_path), *argv]))

    assert targets() == [("pkg-lending-01", tmp_path / "lending")]
    assert targets("--package-id", "pkg-tradeconf-01") == [("pkg-tradeconf-01", tmp_path / "tradeconf")]
    assert targets("--package-id", "pkg-x", "--samples", "/s") == [("pkg-x", Path("/s"))]
    assert [t[1].name for t in targets("--all-packages")] == ["lending", "lending-02", "quarterly", "tradeconf"]
    with pytest.raises(SystemExit, match="all-packages"):
        targets("--all-packages", "--samples", "/s")


def test_sample_files_requires_pdfs(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="make data"):
        seed.sample_files(tmp_path)


# --- reset -----------------------------------------------------------------------------------------------

def _populate(aws: Aws, tmp_path: Path) -> str:
    pkg = seed.seed_package(aws, OWNER)
    pdf = tmp_path / "w2.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    doc_id = seed.upload_documents(aws, BUCKET, pkg, [pdf])[0].doc_id
    s3 = aws.client("s3")
    s3.put_object(Bucket=BUCKET, Key=s3keys.silver_units_key(PKG, doc_id, "2.3.1"), Body=b"{}")
    s3.put_object(Bucket=BUCKET, Key=s3keys.silver_units_key(PKG, doc_id, "2.3.1"), Body=b"{}\n")  # 2 versions
    s3.put_object(Bucket=BUCKET, Key=s3keys.gold_units_key(PKG, doc_id, "2.3.1"), Body=b"{}")
    s3.put_object(Bucket=BUCKET, Key="silver/pkg-other/x/1.0/units/units.jsonl", Body=b"{}")
    status = aws.table("doc_status")
    for sk in ("STAGE#2.3.1#01#register", "LANE#2.3.1", "EVENT#2.3.1#2026-11-03T00:00:00.000Z#register#STARTED"):
        item: dict[str, Any] = {"pk": doc_id, "sk": sk}
        if not sk.startswith("EVENT"):
            item |= {"gsi1pk": PKG, "gsi1sk": f"2.3.1#{doc_id}#{sk[:5]}"}
        status.put_item(Item=item)
    uid = unit_id(doc_id, "2.3.1", "eu-01")
    aws.table("unit_status").put_item(Item={"unitId": uid, "packageLane": f"{PKG}#2.3.1",
                                            "docLane": f"{doc_id}#2.3.1"})
    aws.table("review_queue").put_item(Item={"packageId": PKG, "reviewKey": f"{doc_id}#2.3.1#{uid}#f-box1"})
    aws.table("packages").update_item(Key={"packageId": PKG}, UpdateExpression="SET lanes = :l",
                                      ExpressionAttributeValues={":l": {"2.3.1": {"state": "SUCCEEDED"}}})
    aws.table("runs").put_item(Item={"runId": "r-0123456789ab", "packageId": PKG, "createdAt": "2026-11-03"})
    return doc_id


def test_reset_dry_run_deletes_nothing(aws_env: Aws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc_id = _populate(aws_env, tmp_path)
    rep = reset.reset_package(aws_env, PKG, dry_run=True)
    assert rep.deleted["ddb:docStatus"] == 3 and rep.deleted["s3:silver"] == 2
    assert aws_env.table("doc_acl").get_item(Key={"docId": doc_id}).get("Item")


def test_reset_purges_package_and_keeps_runs(aws_env: Aws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc_id = _populate(aws_env, tmp_path)
    calls: list[tuple[str, dict[str, Any]]] = []
    reply = {"op": "drop_package", "graphs": 1, "triples": 40}
    monkeypatch.setattr(Aws, "invoke", lambda self, fn, payload: calls.append((fn, payload)) or reply)
    rep = reset.reset_package(aws_env, PKG)
    assert calls == [("rp-graph-admin", {"op": "drop_package", "packageId": PKG})]
    assert rep.deleted["s3:bronze"] == 1 and rep.deleted["s3:silver"] == 2 and rep.deleted["s3:gold"] == 1
    assert rep.deleted["graphs"] == 1 and "ddb:runs" not in rep.deleted
    s3 = aws_env.client("s3")
    left = s3.list_object_versions(Bucket=BUCKET, Prefix=f"silver/{PKG}/")
    assert not left.get("Versions") and not left.get("DeleteMarkers")
    assert s3.list_objects_v2(Bucket=BUCKET, Prefix="silver/pkg-other/")["KeyCount"] == 1
    assert aws_env.table("doc_status").query(KeyConditionExpression=Key("pk").eq(doc_id))["Count"] == 0
    assert aws_env.table("unit_status").scan()["Count"] == 0
    assert aws_env.table("review_queue").scan()["Count"] == 0
    assert not aws_env.table("documents").get_item(Key={"packageId": PKG, "docId": doc_id}).get("Item")
    pkg = aws_env.table("packages").get_item(Key={"packageId": PKG})["Item"]
    assert pkg["lanes"] == {} and pkg["docCount"] == 0
    assert aws_env.table("runs").scan()["Count"] == 1


def test_reset_include_runs_and_drop_package(aws_env: Aws, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _populate(aws_env, tmp_path)
    monkeypatch.setattr(Aws, "invoke", lambda self, fn, payload: {"dropped": []})
    reset.reset_package(aws_env, PKG, include_runs=True, drop_package=True)
    assert aws_env.table("runs").scan()["Count"] == 0
    assert not aws_env.table("packages").get_item(Key={"packageId": PKG}).get("Item")


# --- runs export / buckets -------------------------------------------------------------------------------

def _run_record(run_id: str) -> RunRecord:
    return RunRecord(
        run_id=run_id, created_at="2026-11-03T10:00:00.000Z", package_id=PKG, pipeline_version="2.3.1",
        question="What is the total income across this package?", caller_sub="7c41e2", groups=["lending-analyst"],
        model_id="us.anthropic.claude-sonnet-4-6", prompt_sha="p" * 64, config_sha="c" * 64, block_shas={},
        corpus_hash="h" * 64, tools_enabled=["search_units"], tool_calls=[], retrieved=[], retrieved_text=[],
        latency_ms=4100, status="ok", s3_key=s3keys.run_key(run_id),
    )


def test_export_runs_writes_table_and_records(aws_env: Aws, tmp_path: Path) -> None:
    s3 = aws_env.client("s3")
    for rid in ("r-0000000000aa", "r-0000000000bb"):
        rec = _run_record(rid)
        summary = RunSummary(run_id=rid, created_at=rec.created_at, package_id=PKG, pipeline_version="2.3.1",
                             question=rec.question, caller_sub="7c41e2", model_id=rec.model_id,
                             prompt_sha=rec.prompt_sha, config_sha=rec.config_sha, corpus_hash=rec.corpus_hash,
                             latency_ms=rec.latency_ms, status="ok", s3_key=rec.s3_key, total="169,650.00")
        aws_env.table("runs").put_item(Item=to_ddb(summary.dump()))
        if rid.endswith("aa"):
            s3.put_object(Bucket=BUCKET, Key=rec.s3_key, Body=rec.dump_json().encode())
    out = tmp_path / "runs"
    res = runs.export_runs(aws_env, out, package_id=PKG)
    assert res == {"summaries": 2, "records": 1, "missing": 1}
    lines = (out / "runs_table.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["total"] == "169,650.00"
    assert RunRecord.load(json.loads((out / "r-0000000000aa.json").read_text())).latency_ms == 4100


def test_empty_bucket_removes_versions(aws_env: Aws) -> None:
    s3 = aws_env.client("s3")
    s3.put_object(Bucket=BUCKET, Key="a", Body=b"1")
    s3.put_object(Bucket=BUCKET, Key="a", Body=b"2")
    s3.delete_object(Bucket=BUCKET, Key="a")
    before = sum(len(p.get(k, [])) for p in [s3.list_object_versions(Bucket=BUCKET)]
                 for k in ("Versions", "DeleteMarkers"))
    assert agentcore.empty_bucket(aws_env, BUCKET, dry_run=True) == before
    assert agentcore.empty_bucket(aws_env, BUCKET) == before
    assert not s3.list_object_versions(Bucket=BUCKET).get("Versions")
    assert agentcore.empty_bucket(aws_env, "rp-no-such-bucket") == 0


def test_api_client_ask_can_target_the_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.request

    from evidence_core.models.answer import AskRequest
    from rp_ops.api import RUNTIME_SESSION_HEADER, ApiClient

    sent: list[urllib.request.Request] = []

    class Resp:
        def __enter__(self) -> Resp:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def __iter__(self):  # noqa: ANN204
            return iter([b'data: {"type":"error","code":"x","message":"m"}\n', b"\n"])

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: sent.append(req) or Resp())
    req = AskRequest(question="q", package_id="pkg-lending-01", pipeline_version="1.0")
    events = list(ApiClient("https://api.example/v1/", "Bearer t").ask(req))
    assert sent[-1].full_url == "https://api.example/v1/ask" and len(events) == 1
    assert not any(k.lower() == RUNTIME_SESSION_HEADER.lower() for k in sent[-1].headers)
    list(ApiClient("https://api.example/v1/", "Bearer t", runtime_url="https://rt.example/inv?qualifier=DEFAULT").ask(req))
    assert sent[-1].full_url == "https://rt.example/inv?qualifier=DEFAULT"
    session = sent[-1].get_header(RUNTIME_SESSION_HEADER.capitalize())
    assert session and len(session) >= 33
