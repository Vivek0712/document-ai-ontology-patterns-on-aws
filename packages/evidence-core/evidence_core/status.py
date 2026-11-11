"""DocStatus writer and the ``@stage_handler`` decorator every stage Lambda uses.

Writes to table ``rp-doc-status`` (RP_TABLE_DOC_STATUS):
  - stage record   pk=docId sk=STAGE#{pv}#{order}#{stageId}   (current state, see StageRecord)
  - event          pk=docId sk=EVENT#{pv}#{at}#{stageId}#{evt} (append-only, TTL 30 d)
  - lane record    pk=docId sk=LANE#{pv}                       (current stage, lane state)
  - history        pk=docId sk=HIST#{pv}#{runKey}#...           (the previous execution's lane and stage
                                                               records, archived by init_lane; TTL 30 d)

Every event carries the ``executionArn`` of the run that wrote it, so the inspector shows the current
run's events and lists earlier runs from the history records instead of mixing them.

Transitions: PENDING -> QUEUED? -> RUNNING (STARTED, HEARTBEAT*) -> SUCCEEDED | FAILED;
PENDING -> SKIPPED. A retry (Step Functions Retry or reprocess) calls start() again,
which increments ``attempt`` and clears the previous error/end time.
"""

from __future__ import annotations

import functools
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import config as config_mod
from .config import LoadedPipeline, config_slice
from .ddb import from_ddb, to_ddb
from .enums import Engine, LaneState, StageEventType, StageId, StageState
from .env import Settings
from .hashing import sha256_json
from .models.status import (
    ArtifactRef,
    LaneRecord,
    StageConfigSnapshot,
    StageEnvelope,
    StageError,
    StageEvent,
    StageProgress,
    StageRecord,
    StageResult,
    StageVersions,
    iso,
    utcnow,
)
from .stages import (
    ResolvedStage,
    event_sk,
    history_run_key,
    history_sk,
    lane_keys,
    resolve_stages,
    stage_def,
    stage_keys,
)

EVENT_TTL_SECONDS = 30 * 24 * 3600
_CAUSE_MAX = 4096


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def error_from_exception(exc: BaseException, *, retryable: bool = False) -> StageError:
    cause = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-_CAUSE_MAX:]
    return StageError(type=type(exc).__name__, message=str(exc)[:1000], cause=cause, retryable=retryable)


def error_from_sfn(err: dict[str, Any]) -> StageError:
    """Step Functions Catch output ``{"Error": ..., "Cause": ...}``."""
    cause = str(err.get("Cause") or "")[:_CAUSE_MAX]
    return StageError(
        type=str(err.get("Error") or "States.TaskFailed"), message=cause[:1000] or "task failed", cause=cause
    )


def _update(table: Any, key: dict[str, str], set_: dict[str, Any], add: dict[str, int] | None = None,
            remove: list[str] | None = None) -> None:
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    parts: list[str] = []
    sets = []
    for i, (k, v) in enumerate((k, v) for k, v in set_.items() if v is not None):
        names[f"#s{i}"] = k
        values[f":s{i}"] = to_ddb(v)
        sets.append(f"#s{i} = :s{i}")
    if sets:
        parts.append("SET " + ", ".join(sets))
    if add:
        adds = []
        for i, (k, v) in enumerate(add.items()):
            names[f"#a{i}"] = k
            values[f":a{i}"] = v
            adds.append(f"#a{i} :a{i}")
        parts.append("ADD " + ", ".join(adds))
    rem = [r for r in (remove or []) if r not in set_]
    if rem:
        rems = []
        for i, k in enumerate(rem):
            names[f"#r{i}"] = k
            rems.append(f"#r{i}")
        parts.append("REMOVE " + ", ".join(rems))
    kwargs: dict[str, Any] = {"Key": key, "UpdateExpression": " ".join(parts), "ExpressionAttributeNames": names}
    if values:
        kwargs["ExpressionAttributeValues"] = values
    table.update_item(**kwargs)


@dataclass
class StatusWriter:
    """Writes stage/lane/event records for one (doc, pipelineVersion, stage)."""

    table: Any  # boto3 DynamoDB Table resource for rp-doc-status
    doc_id: str
    package_id: str
    pipeline_version: str
    stage_id: StageId
    heartbeat_seconds: float = 5.0
    clock: Callable[[], float] = time.monotonic
    execution_arn: str | None = None  # stamped on every event (StageEvent.executionArn)
    _started_mono: float | None = field(default=None, init=False)
    _last_hb: float = field(default=0.0, init=False)
    _attempt: int = field(default=0, init=False)

    @classmethod
    def for_table_name(cls, table_name: str, doc_id: str, package_id: str, pv: str, stage_id: StageId,
                       heartbeat_seconds: float = 5.0) -> StatusWriter:
        import boto3

        return cls(boto3.resource("dynamodb").Table(table_name), doc_id, package_id, pv, StageId(stage_id),
                   heartbeat_seconds)

    def for_stage(self, stage_id: StageId) -> StatusWriter:
        return StatusWriter(self.table, self.doc_id, self.package_id, self.pipeline_version, StageId(stage_id),
                            self.heartbeat_seconds, self.clock, self.execution_arn)

    # --- keys ------------------------------------------------------------------------
    def _stage_key(self) -> dict[str, str]:
        k = stage_keys(self.doc_id, self.package_id, self.pipeline_version, self.stage_id)
        return {"pk": k.pk, "sk": k.sk}

    def _lane_key(self) -> dict[str, str]:
        k = lane_keys(self.doc_id, self.package_id, self.pipeline_version)
        return {"pk": k.pk, "sk": k.sk}

    def _event(self, event: StageEventType, **kw: Any) -> None:
        ev = StageEvent(doc_id=self.doc_id, package_id=self.package_id, pipeline_version=self.pipeline_version,
                        stage_id=self.stage_id, event=event, attempt=self._attempt,
                        execution_arn=self.execution_arn, **kw)
        item = ev.dump()
        item.update(pk=self.doc_id, sk=event_sk(self.pipeline_version, ev.at, self.stage_id, event.value),
                    recordType="EVENT", expiresAt=int(time.time()) + EVENT_TTL_SECONDS)
        self.table.put_item(Item=to_ddb(item))

    # --- lane-level (register / LaneFailed / MarkReady) ----------------------------------
    def archive_previous_run(self, new_execution_arn: str | None) -> int:
        """Copy the lane and stage records of the previous execution to ``HIST#`` before a new execution
        (reprocess, re-upload) overwrites them, so its errors, artifacts, durations and versions stay
        inspectable. Returns the number of records archived."""
        from boto3.dynamodb.conditions import Key

        prev = self.get_lane()
        if prev is None or not prev.execution_arn or prev.execution_arn == new_execution_arn:
            return 0
        run_key = history_run_key(prev.started_at, prev.execution_arn)
        resp = self.table.query(KeyConditionExpression=Key("pk").eq(self.doc_id)
                                & Key("sk").begins_with(f"STAGE#{self.pipeline_version}#"))
        expires = int(time.time()) + EVENT_TTL_SECONDS
        n = 0
        lane_item = prev.dump() | {"pk": self.doc_id, "sk": history_sk(self.pipeline_version, run_key, "LANE"),
                                   "recordType": "LANE_HIST", "expiresAt": expires}
        self.table.put_item(Item=to_ddb(lane_item))
        n += 1
        for raw in resp.get("Items", []):
            rec = StageRecord.load(from_ddb(raw))
            suffix = f"S{rec.order:02d}#{rec.stage_id.value}"
            item = rec.dump() | {"pk": self.doc_id, "sk": history_sk(self.pipeline_version, run_key, suffix),
                                 "recordType": "STAGE_HIST", "expiresAt": expires}
            self.table.put_item(Item=to_ddb(item))
            n += 1
        return n

    def init_lane(self, stages: list[ResolvedStage], lane: LaneRecord) -> None:
        """Called by `register`: PENDING/SKIPPED record for every stage, lane RUNNING. The previous
        execution's records are archived first (``archive_previous_run``)."""
        self.execution_arn = lane.execution_arn or self.execution_arn
        self.archive_previous_run(lane.execution_arn)
        for st in stages:
            rec = StageRecord(
                doc_id=self.doc_id, package_id=self.package_id, pipeline_version=self.pipeline_version,
                stage_id=st.id, order=st.order, group=st.group, title=st.title,
                state=StageState.PENDING if st.enabled else StageState.SKIPPED,
                services=st.services, function_name=st.functions[0] if st.functions else None,
                execution_arn=lane.execution_arn, skip_reason=st.skip_reason,
                ended_at=None if st.enabled else iso(),
            )
            keys = stage_keys(self.doc_id, self.package_id, self.pipeline_version, st.id)
            item = rec.dump() | {"pk": keys.pk, "sk": keys.sk, "gsi1pk": keys.gsi1pk, "gsi1sk": keys.gsi1sk,
                                 "recordType": "STAGE"}
            self.table.put_item(Item=to_ddb(item))
            if not st.enabled:
                self.for_stage(st.id)._event(StageEventType.SKIPPED, message=st.skip_reason)
        self.put_lane(lane)

    def put_lane(self, lane: LaneRecord) -> None:
        keys = lane_keys(self.doc_id, self.package_id, self.pipeline_version)
        item = lane.dump() | {"pk": keys.pk, "sk": keys.sk, "gsi1pk": keys.gsi1pk, "gsi1sk": keys.gsi1sk,
                              "recordType": "LANE"}
        self.table.put_item(Item=to_ddb(item))

    def update_lane(self, **fields: Any) -> None:
        fields["updatedAt"] = iso()
        _update(self.table, self._lane_key(), fields)

    def lane_succeeded(self, *, units: int | None = None, held: int | None = None, indexed: int | None = None,
                       triples: int | None = None) -> None:
        lane = self.get_lane()
        started = lane.started_at if lane else None
        now = utcnow()
        dur = int((now - _parse_iso(started)).total_seconds() * 1000) if started else None
        self.update_lane(state=LaneState.SUCCEEDED.value, endedAt=iso(now), durationMs=dur, units=units, held=held,
                         indexed=indexed, triples=triples, currentStage=StageId.READY.value)

    def lane_failed(self, error: StageError) -> None:
        """Mark the lane FAILED and, if the current stage is still open, fail it too."""
        lane = self.get_lane()
        if lane and lane.current_stage:
            cur = self.for_stage(lane.current_stage)
            rec = cur.get()
            if rec and rec.state in (StageState.RUNNING, StageState.QUEUED, StageState.PENDING):
                cur.fail(error, update_lane=False)
        self.update_lane(state=LaneState.FAILED.value, endedAt=iso(), error=error.dump())

    def get_lane(self) -> LaneRecord | None:
        item = self.table.get_item(Key=self._lane_key()).get("Item")
        return LaneRecord.load(from_ddb(item)) if item else None

    def get(self) -> StageRecord | None:
        item = self.table.get_item(Key=self._stage_key()).get("Item")
        return StageRecord.load(from_ddb(item)) if item else None

    # --- stage-level ----------------------------------------------------------------------
    def queued(self, engine: Engine | None = None, message: str | None = None) -> None:
        now = iso()
        _update(self.table, self._stage_key(), {"state": StageState.QUEUED.value, "queuedAt": now,
                                                "engine": engine.value if engine else None, "message": message,
                                                "updatedAt": now})
        self._event(StageEventType.QUEUED, message=message)

    def start(
        self,
        *,
        engine: Engine | None = None,
        inputs: list[ArtifactRef] | None = None,
        input_summary: dict[str, Any] | None = None,
        config: StageConfigSnapshot | None = None,
        versions: StageVersions | None = None,
        sfn_state: str | None = None,
        execution_arn: str | None = None,
        function_name: str | None = None,
        message: str | None = None,
    ) -> None:
        now = iso()
        self._started_mono = self.clock()
        self._last_hb = self._started_mono
        if execution_arn:
            self.execution_arn = execution_arn
        prev = self.get()
        self._attempt = (prev.attempt if prev else 0) + 1
        _update(
            self.table,
            self._stage_key(),
            {
                "state": StageState.RUNNING.value,
                "attempt": self._attempt,
                "startedAt": now,
                "lastHeartbeatAt": now,
                "heartbeatCount": 0,
                "engine": engine.value if engine else None,
                "inputs": [a.dump() for a in inputs] if inputs is not None else None,
                "input": input_summary,
                "config": config.dump() if config else None,
                "versions": versions.dump() if versions else None,
                "sfnState": sfn_state,
                "executionArn": execution_arn,
                "functionName": function_name,
                "message": message,
                "updatedAt": now,
            },
            remove=["error", "endedAt", "durationMs", "progress", "artifacts", "output", "metrics", "headline",
                    "skipReason"],
        )
        self.update_lane(state=LaneState.RUNNING.value, currentStage=self.stage_id.value)
        self._event(StageEventType.STARTED, message=message)

    def heartbeat(self, progress: StageProgress | None = None, message: str | None = None, *,
                  force: bool = False) -> bool:
        """Record liveness; throttled to one write per ``heartbeat_seconds``. Returns True if written."""
        now_mono = self.clock()
        if not force and now_mono - self._last_hb < self.heartbeat_seconds:
            return False
        self._last_hb = now_mono
        now = iso()
        _update(self.table, self._stage_key(),
                {"lastHeartbeatAt": now, "progress": progress.dump() if progress else None, "message": message,
                 "updatedAt": now},
                add={"heartbeatCount": 1})
        self._event(StageEventType.HEARTBEAT, message=message, progress=progress)
        return True

    def _duration_ms(self) -> int | None:
        if self._started_mono is not None:
            return int((self.clock() - self._started_mono) * 1000)
        rec = self.get()
        if rec and rec.started_at:
            return int((utcnow() - _parse_iso(rec.started_at)).total_seconds() * 1000)
        return None

    def succeed(self, result: StageResult) -> StageResult:
        now = iso()
        dur = result.duration_ms if result.duration_ms is not None else self._duration_ms()
        result.duration_ms = dur
        _update(
            self.table,
            self._stage_key(),
            {
                "state": StageState.SUCCEEDED.value,
                "endedAt": now,
                "durationMs": dur,
                "engine": result.engine.value if result.engine else None,
                "headline": result.headline,
                "artifacts": [a.dump() for a in result.artifacts],
                "metrics": result.metrics,
                "output": result.output,
                "updatedAt": now,
            },
        )
        self._event(StageEventType.SUCCEEDED, duration_ms=dur, message=result.headline)
        return result

    def set_model_used(self, model_id: str | None) -> None:
        """Replace ``versions.modelId`` with the model that actually ran (None: no model was called)."""
        key = self._stage_key()
        if model_id:
            self.table.update_item(Key=key, UpdateExpression="SET #v.#m = :m",
                                   ConditionExpression="attribute_exists(#v)",
                                   ExpressionAttributeNames={"#v": "versions", "#m": "modelId"},
                                   ExpressionAttributeValues={":m": model_id})
        else:
            self.table.update_item(Key=key, UpdateExpression="REMOVE #v.#m",
                                   ConditionExpression="attribute_exists(#v)",
                                   ExpressionAttributeNames={"#v": "versions", "#m": "modelId"})

    def fail(self, error: StageError, *, update_lane: bool = True) -> None:
        now = iso()
        dur = self._duration_ms()
        _update(self.table, self._stage_key(),
                {"state": StageState.FAILED.value, "endedAt": now, "durationMs": dur, "error": error.dump(),
                 "updatedAt": now})
        self._event(StageEventType.FAILED, duration_ms=dur, error=error, message=error.message)
        if update_lane:
            self.update_lane(state=LaneState.FAILED.value, error=error.dump(), endedAt=now)

    def skip(self, reason: str) -> None:
        now = iso()
        _update(self.table, self._stage_key(),
                {"state": StageState.SKIPPED.value, "skipReason": reason, "endedAt": now, "updatedAt": now})
        self._event(StageEventType.SKIPPED, message=reason)


# --- decorator ------------------------------------------------------------------------------

_UNSET = object()


@dataclass
class StageContext:
    """What a stage function receives."""

    envelope: StageEnvelope
    writer: StatusWriter
    settings: Settings
    lambda_context: Any = None
    _pipeline: LoadedPipeline | None = None
    _model_used: Any = field(default=_UNSET, repr=False)

    def record_model(self, model_id: str | None) -> None:
        """The model this stage really invoked (None when it called none). Without this call the stage
        record keeps the configured model of ``model_key``."""
        self._model_used = model_id

    @property
    def model_recorded(self) -> bool:
        return self._model_used is not _UNSET

    @property
    def model_used(self) -> str | None:
        return None if self._model_used is _UNSET else self._model_used

    @property
    def ctx(self):  # noqa: ANN201 - DocCtx
        return self.envelope.ctx

    @property
    def pipeline(self) -> LoadedPipeline:
        if self._pipeline is None:
            self._pipeline = _load_pipeline_cached(self.ctx.pipeline_version, self.ctx.config_version_id)
        return self._pipeline

    def heartbeat(self, progress: StageProgress | None = None, message: str | None = None) -> bool:
        return self.writer.heartbeat(progress, message)

    def remaining_ms(self) -> int | None:
        if self.lambda_context is None:
            return None
        return int(self.lambda_context.get_remaining_time_in_millis())


@functools.lru_cache(maxsize=8)
def _load_pipeline_cached(pv: str, version_id: str | None) -> LoadedPipeline:
    return config_mod.load_pipeline(pv, version_id=version_id)


def config_snapshot(pipeline: LoadedPipeline, stage_id: StageId) -> StageConfigSnapshot:
    resolved = next(s for s in resolve_stages(pipeline.config) if s.id == stage_id)
    values = {p: config_slice(pipeline, p) for p in resolved.config_paths}
    return StageConfigSnapshot(paths=resolved.config_paths, values=values, sha=sha256_json(values))


def stage_handler(
    stage_id: StageId,
    *,
    engine: Engine | None = None,
    engine_version: str | None = None,
    model_key: str | None = None,
    input_summary: Callable[[StageEnvelope], dict[str, Any]] | None = None,
    inputs: Callable[[StageEnvelope], list[ArtifactRef]] | None = None,
    table: Any = None,
) -> Callable[[Callable[[StageContext], StageResult]], Callable[[dict[str, Any], Any], dict[str, Any]]]:
    """Wrap a stage function as a Lambda handler with full status recording.

    The wrapped function returns a StageResult; the handler returns its JSON to Step Functions.
    On exception it writes FAILED (with traceback) and re-raises so the ASL Retry/Catch applies.
    ``table`` overrides the DynamoDB Table resource (tests).
    """

    def deco(fn: Callable[[StageContext], StageResult]) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
        @functools.wraps(fn)
        def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
            settings = Settings.from_env()
            env = StageEnvelope.load(event)
            tbl = table
            if tbl is None:
                import boto3

                tbl = boto3.resource("dynamodb").Table(settings.table("doc_status"))
            writer = StatusWriter(tbl, env.ctx.doc_id, env.ctx.package_id, env.ctx.pipeline_version, stage_id,
                                  settings.heartbeat_seconds,
                                  execution_arn=env.execution_arn or env.ctx.execution_arn)
            sctx = StageContext(envelope=env, writer=writer, settings=settings, lambda_context=context)
            snapshot: StageConfigSnapshot | None = None
            model_id: str | None = None
            try:
                snapshot = config_snapshot(sctx.pipeline, stage_id)
                if model_key:
                    model_id = sctx.pipeline.models.model_id(model_key)
            except Exception as exc:  # config must load for the stage to run at all
                writer.start(engine=engine, sfn_state=env.state_name, execution_arn=env.execution_arn)
                writer.fail(error_from_exception(exc))
                raise
            versions = StageVersions(
                pipeline_version=env.ctx.pipeline_version,
                config_sha=env.ctx.config_sha,
                config_version_id=env.ctx.config_version_id,
                ontology_version=env.ctx.ontology_version,
                engine_version=engine_version,
                model_id=model_id,
                prompt_sha=env.ctx.prompt_sha,
                code_sha=settings.code_sha,
            )
            common = {"engine": engine, "config": snapshot, "versions": versions, "sfn_state": env.state_name,
                      "execution_arn": env.execution_arn, "function_name": getattr(context, "function_name", None)}
            try:  # a missing upstream artifact must fail THIS stage's record, not leave it PENDING
                stage_inputs = inputs(env) if inputs else None
                summary = input_summary(env) if input_summary else None
            except Exception as exc:
                writer.start(**common)
                writer.fail(error_from_exception(exc))
                raise
            writer.start(inputs=stage_inputs, input_summary=summary, **common)
            try:
                result = fn(sctx)
            except Exception as exc:
                writer.fail(error_from_exception(exc))
                raise
            if result.stage_id != stage_id:
                raise ValueError(f"stage function returned {result.stage_id}, expected {stage_id}")
            if sctx.model_recorded and sctx.model_used != model_id:
                writer.set_model_used(sctx.model_used)
            return writer.succeed(result).dump()

        handler.stage_id = stage_id  # type: ignore[attr-defined]
        return handler

    return deco


def stage_order(stage_id: StageId) -> int:
    return stage_def(stage_id).order
