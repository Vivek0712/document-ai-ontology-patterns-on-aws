"""rp-register: first state of rp-ingest. Validates the Bronze object, writes the manifest,
DocumentItem/DocAcl, and PENDING/SKIPPED records for every stage of this lane.

Input: ``{"trigger": IngestTrigger, "executionArn": ...}``. Output (ResultPath ``$``):
``RegisterResult {ctx, stages: {register: StageResult}}``; ``stages.register.output.validation`` is
``ok`` | ``quarantined`` | ``skipped`` (read by ModeChoice).

Guards before any work:
- an object larger than ``MAX_UPLOAD_BYTES`` (from HeadObject) is quarantined without being read;
- a document whose bytes (sha256) are already in the package under another file name is SKIPPED as a
  duplicate, so its facts are not counted twice (``duplicateOf`` on the DocumentItem, the reason on
  every stage record). The earliest-registered copy is canonical (``firstRegisteredAt``, then docId).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote_plus

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from evidence_core import s3keys
from evidence_core.config import LoadedPipeline, load_pipeline
from evidence_core.ddb import from_ddb, to_ddb
from evidence_core.enums import ArtifactKind, Engine, LaneState, StageId, StageState
from evidence_core.hashing import sha256_bytes
from evidence_core.ids import doc_id_for
from evidence_core.models.api import MAX_UPLOAD_BYTES, DocAclItem, Package
from evidence_core.models.stage_outputs import RegisterOutput, RegisterResult
from evidence_core.models.status import (
    ArtifactRef,
    DocCtx,
    IngestTrigger,
    LaneRecord,
    StageError,
    StageResult,
    StageVersions,
    iso,
)
from evidence_core.stages import resolve_stages, stage_keys
from evidence_core.status import StatusWriter, config_snapshot, error_from_exception

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.pdf import PdfInspection, inspect_pdf, pypdf_version
from lambdas.ingest_lib.store import DataStore


def _package(package_id: str) -> Package | None:
    item = services().table("packages").get_item(Key={"packageId": package_id}).get("Item")
    return Package.load(from_ddb(item)) if item else None


def _update_document(package_id: str, doc_id: str, **fields: Any) -> None:
    names, values, sets = {}, {}, []
    for i, (k, v) in enumerate((k, v) for k, v in fields.items() if v is not None):
        names[f"#k{i}"] = k
        values[f":v{i}"] = to_ddb(v)
        sets.append(f"#k{i} = :v{i}")
    services().table("documents").update_item(
        Key={"packageId": package_id, "docId": doc_id}, UpdateExpression="SET " + ", ".join(sets),
        ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def resolve_object_key(client: Any, bucket: str, key: str) -> str:
    """The real object key for an S3 event key.

    S3 event notifications URL-encode object keys (a space arrives as ``+``). The EventBridge input
    transformer passes ``detail.object.key`` through unchanged, so a file name with spaces or special
    characters may arrive encoded. The key as given wins when it exists; otherwise the decoded key is used.
    """
    decoded = unquote_plus(key)
    if decoded == key:
        return key
    try:
        client.head_object(Bucket=bucket, Key=key)
        return key
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey", "NotFound"):
            raise
    return decoded


def register(trigger: IngestTrigger, execution_arn: str) -> RegisterResult:
    svc = services()
    trigger.key = resolve_object_key(svc.s3, trigger.bucket, trigger.key)
    bk = s3keys.parse_bronze_key(trigger.key)
    doc_id = doc_id_for(bk.package_id, bk.file_name)
    pv = trigger.pipeline_version
    writer = StatusWriter(svc.table("doc_status"), doc_id, bk.package_id, pv, StageId.REGISTER,
                          svc.settings.heartbeat_seconds, execution_arn=execution_arn)
    now = iso()
    pipeline: LoadedPipeline = load_pipeline(pv)
    cfg = pipeline.config
    stages = resolve_stages(cfg)
    lane = LaneRecord(doc_id=doc_id, package_id=bk.package_id, pipeline_version=pv, state=LaneState.RUNNING,
                      current_stage=StageId.REGISTER, execution_arn=execution_arn, started_at=now,
                      config_sha=pipeline.config_sha, config_version_id=pipeline.version_id,
                      prompt_sha=pipeline.prompt_sha, ontology_version=cfg.ontology_version)

    package = _package(bk.package_id)
    store = DataStore(trigger.bucket, svc.s3)
    skip_reason = None
    if package is None:
        skip_reason = f"package {bk.package_id} does not exist (deleted?)"
    elif pv not in package.pipeline_versions:
        skip_reason = f"pipeline {pv} is not enabled for package {bk.package_id}"
    if skip_reason:
        skipped = [s.model_copy(update={"enabled": False, "skip_reason": skip_reason}) for s in stages]
        writer.init_lane(skipped, lane.model_copy(update={"state": LaneState.SKIPPED, "skip_reason": skip_reason,
                                                          "ended_at": now, "current_stage": None}))
        out = RegisterOutput(page_count=0, pages=[], sha256="", size_bytes=trigger.size or 0,
                             content_type="application/pdf", source_version_id=trigger.version_id,
                             validation="skipped", quarantine_reason=skip_reason)
        ctx = _ctx(trigger, bk, doc_id, pipeline, package, execution_arn, now, "", trigger.size or 0, 0)
        result = StageResult(stage_id=StageId.REGISTER, state=StageState.SKIPPED, engine=Engine.LAMBDA,
                             headline="skipped", output=out.dump())
        return RegisterResult(ctx=ctx, stages={StageId.REGISTER.value: result})

    writer.init_lane(stages, lane)
    snapshot = config_snapshot(pipeline, StageId.REGISTER)
    versions = StageVersions(pipeline_version=pv, config_sha=pipeline.config_sha,
                             config_version_id=pipeline.version_id, ontology_version=cfg.ontology_version,
                             engine_version=pypdf_version(), prompt_sha=pipeline.prompt_sha,
                             code_sha=svc.settings.code_sha)
    src_ref = ArtifactRef(key=trigger.key, kind=ArtifactKind.SOURCE_PDF, content_type="application/pdf",
                          size_bytes=trigger.size, version_id=trigger.version_id)
    writer.start(engine=Engine.LAMBDA, inputs=[src_ref], config=snapshot, versions=versions, sfn_state="Register",
                 execution_arn=execution_arn, function_name="rp-register",
                 input_summary={"trigger": trigger.trigger, "key": trigger.key, "eventTime": trigger.event_time,
                                "requestedBy": (trigger.requested_by or "")[:6] or None})
    try:
        head = store.head(trigger.key, trigger.version_id)
        size = int(head.get("ContentLength") or trigger.size or 0)
        if size > MAX_UPLOAD_BYTES:  # never pull an oversized object into memory
            sha = ""
            insp = PdfInspection(False, f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
                                        f"({size} bytes; not read)")
        else:
            data = store.get_bytes(trigger.key, trigger.version_id)
            size = len(data)
            sha = sha256_bytes(data)
            insp = inspect_pdf(data, max_bytes=MAX_UPLOAD_BYTES)
        version_id = trigger.version_id or head.get("VersionId")
        src_ref = src_ref.model_copy(update={"sha256": sha or None, "size_bytes": size, "version_id": version_id})
        ctx = _ctx(trigger, bk, doc_id, pipeline, package, execution_arn, now, sha, size, insp.page_count,
                   version_id=version_id, content_type=head.get("ContentType") or "application/pdf")
        out = RegisterOutput(page_count=insp.page_count, pages=insp.pages, sha256=sha, size_bytes=size,
                             content_type=ctx.content_type, source_version_id=version_id,
                             validation="ok" if insp.ok else "quarantined", quarantine_reason=insp.reason)
        manifest = store.put_json(s3keys.manifest_key(bk.package_id, doc_id, pv), out, kind=ArtifactKind.MANIFEST,
                                  count=insp.page_count)
        artifacts = [manifest]
        acl = DocAclItem(doc_id=doc_id, package_id=bk.package_id, owner_sub=bk.owner_sub,
                         allowed_principals=package.allowed_principals)  # type: ignore[union-attr]
        svc.table("doc_acl").put_item(Item=to_ddb(acl.dump()))
        if insp.ok:
            issued = None if _doc_exists(bk.package_id, doc_id) else now
            _update_document(bk.package_id, doc_id, fileName=bk.file_name, bronzeKey=trigger.key,
                             uploadedBy=bk.owner_sub, uploadState="IN_BRONZE", sha256=sha, sizeBytes=size,
                             sourceVersionId=version_id, pageCount=insp.page_count,
                             inBronzeAt=trigger.event_time or now, contentType=ctx.content_type,
                             uploadUrlIssuedAt=issued)
            _mark_first_registered(bk.package_id, doc_id, now)
            original = duplicate_of(bk.package_id, doc_id, sha)
            _set_duplicate_of(bk.package_id, doc_id, original)
            if original is not None:
                reason = (f"duplicate of {original.get('fileName') or original['docId']} "
                          f"(same sha256 {sha[:12]}…); not ingested again")
                skipped = [s.model_copy(update={"enabled": False, "skip_reason": reason}) for s in stages]
                writer.init_lane(skipped, lane.model_copy(update={
                    "state": LaneState.SKIPPED, "skip_reason": reason, "ended_at": iso(), "current_stage": None}))
                out = out.model_copy(update={"validation": "skipped", "quarantine_reason": reason})
                result = StageResult(stage_id=StageId.REGISTER, state=StageState.SKIPPED, engine=Engine.LAMBDA,
                                     headline="skipped: duplicate", artifacts=artifacts, output=out.dump())
                _record(writer, [src_ref], result)
                return RegisterResult(ctx=ctx, stages={StageId.REGISTER.value: result})
            headline = f"{insp.page_count} page{'s' if insp.page_count != 1 else ''} · {size // 1024} KB"
            result = StageResult(stage_id=StageId.REGISTER, state=StageState.SUCCEEDED, engine=Engine.LAMBDA,
                                 headline=headline, artifacts=artifacts, output=out.dump(),
                                 metrics={"pages": insp.page_count, "bytes": size})
            _record(writer, [src_ref])
            writer.succeed(result)
            return RegisterResult(ctx=ctx, stages={StageId.REGISTER.value: result})

        qkey = s3keys.quarantine_key(bk.package_id, doc_id, bk.file_name)
        store.copy(trigger.key, qkey, src_version_id=version_id)
        _update_document(bk.package_id, doc_id, fileName=bk.file_name, bronzeKey=trigger.key,
                         uploadedBy=bk.owner_sub, uploadState="QUARANTINED", sha256=sha or None, sizeBytes=size,
                         quarantineReason=insp.reason, quarantineKey=qkey,
                         uploadUrlIssuedAt=now if not _doc_exists(bk.package_id, doc_id) else None)
        artifacts.append(ArtifactRef(key=qkey, kind=ArtifactKind.SOURCE_PDF, content_type=ctx.content_type,
                                     size_bytes=size, sha256=sha or None, note="quarantined copy"))
        result = StageResult(stage_id=StageId.REGISTER, state=StageState.FAILED, engine=Engine.LAMBDA,
                             headline=f"quarantined: {insp.reason}", artifacts=artifacts, output=out.dump())
        _record(writer, [src_ref], result)
        writer.fail(StageError(type="Quarantined", message=insp.reason or "rejected", retryable=False))
        return RegisterResult(ctx=ctx, stages={StageId.REGISTER.value: result})
    except Exception as exc:
        writer.fail(error_from_exception(exc))
        raise


def _mark_first_registered(package_id: str, doc_id: str, now: str) -> None:
    """Set once; reprocessing keeps it, so the canonical copy of duplicate bytes never flips."""
    services().table("documents").update_item(
        Key={"packageId": package_id, "docId": doc_id},
        UpdateExpression="SET firstRegisteredAt = if_not_exists(firstRegisteredAt, :now)",
        ExpressionAttributeValues={":now": now})


def _set_duplicate_of(package_id: str, doc_id: str, original: dict[str, Any] | None) -> None:
    table = services().table("documents")
    key = {"packageId": package_id, "docId": doc_id}
    if original is None:
        table.update_item(Key=key, UpdateExpression="REMOVE duplicateOf")
    else:
        table.update_item(Key=key, UpdateExpression="SET duplicateOf = :d",
                          ExpressionAttributeValues={":d": str(original["docId"])})


def duplicate_of(package_id: str, doc_id: str, sha: str) -> dict[str, Any] | None:
    """The canonical document with the same bytes in this package, when this one is not it.

    Every copy writes its sha256 and ``firstRegisteredAt`` before calling this, so two copies
    registered at the same moment agree on which one is canonical (earliest, then lowest docId)."""
    kwargs: dict[str, Any] = {"KeyConditionExpression": Key("packageId").eq(package_id)}
    same: list[dict[str, Any]] = []
    table = services().table("documents")
    while True:
        resp = table.query(**kwargs)
        for raw in resp.get("Items", []):
            item = from_ddb(raw)
            if item.get("sha256") == sha and item.get("uploadState") == "IN_BRONZE":
                same.append(item)
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    if not any(i["docId"] == doc_id for i in same):
        return None
    canonical = min(same, key=lambda i: (str(i.get("firstRegisteredAt") or "~"), str(i["docId"])))
    return None if canonical["docId"] == doc_id else canonical


def _doc_exists(package_id: str, doc_id: str) -> bool:
    return "Item" in services().table("documents").get_item(Key={"packageId": package_id, "docId": doc_id},
                                                            ProjectionExpression="docId")


def _record(writer: StatusWriter, inputs: list[ArtifactRef], result: StageResult | None = None) -> None:
    """Set inputs (with the sha now known) and, for a rejected upload, artifacts/output, without a
    state change: the FAILED transition is written by ``writer.fail``."""
    k = stage_keys(writer.doc_id, writer.package_id, writer.pipeline_version, writer.stage_id)
    fields: dict[str, Any] = {"inputs": [a.dump() for a in inputs]}
    if result is not None:
        fields |= {"artifacts": [a.dump() for a in result.artifacts], "output": result.output,
                   "headline": result.headline, "engine": Engine.LAMBDA.value}
    names = {f"#k{i}": k_ for i, k_ in enumerate(fields)}
    values = {f":v{i}": to_ddb(v) for i, v in enumerate(fields.values())}
    writer.table.update_item(Key={"pk": k.pk, "sk": k.sk},
                             UpdateExpression="SET " + ", ".join(f"#k{i} = :v{i}" for i in range(len(fields))),
                             ExpressionAttributeNames=names, ExpressionAttributeValues=values)


def _ctx(trigger: IngestTrigger, bk: s3keys.BronzeKey, doc_id: str, pipeline: LoadedPipeline,
         package: Package | None, execution_arn: str, now: str, sha: str, size: int, pages: int, *,
         version_id: str | None = None, content_type: str = "application/pdf") -> DocCtx:
    cfg = pipeline.config
    return DocCtx(
        package_id=bk.package_id, doc_id=doc_id, pipeline_version=cfg.pipeline_version, mode=cfg.mode,
        owner_sub=bk.owner_sub, file_name=bk.file_name, bucket=trigger.bucket, bronze_key=trigger.key,
        source_version_id=version_id or trigger.version_id, source_sha256=sha, size_bytes=size,
        content_type=content_type, page_count=pages, config_key=pipeline.source_key,
        config_version_id=pipeline.version_id, config_sha=pipeline.config_sha, prompt_sha=pipeline.prompt_sha,
        ontology_version=cfg.ontology_version, route_enabled=cfg.route_enabled,
        classify_enabled=cfg.classify_enabled, graph_enabled=cfg.graph_enabled,
        allowed_principals=package.allowed_principals if package else [], execution_arn=execution_arn,
        lane_started_at=now, trigger=trigger.trigger,
    )


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    trigger = IngestTrigger.load(event.get("trigger") or event)
    execution_arn = str(event.get("executionArn") or "local")
    return register(trigger, execution_arn).dump()
