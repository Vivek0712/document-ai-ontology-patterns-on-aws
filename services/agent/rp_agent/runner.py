"""One ask, end to end: the SSE event sequence the UI reads:

run_start -> (tool_start -> tool_end)* -> text* -> answer -> check? -> run   (or error)

Same agent, prompt, model, retriever and K for both pipeline versions: only the
``pipelineVersion`` header (and so the index / graph the tools read) differs.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from evidence_core import PIPELINE_VERSIONS
from evidence_core.config import LoadedPipeline
from evidence_core.ids import new_run_id, short
from evidence_core.logs import get_logger
from evidence_core.models.answer import (
    GENERATION_PROFILE_MODEL_KEYS,
    AnswerEvent,
    AnswerPayload,
    AskRequest,
    Check,
    CheckEvent,
    ErrorEvent,
    RunEvent,
    RunRecord,
    RunStartEvent,
    TextEvent,
    ToolCallRecord,
    ToolEndEvent,
    ToolStartEvent,
)
from evidence_core.models.golden import GoldenEntry, match_golden, normalise_amount
from evidence_core.models.status import iso
from evidence_core.models.tools import HEADER_PACKAGE, HEADER_PIPELINE_VERSION, HEADER_RUN
from evidence_core.s3keys import run_key

from .citations import AnswerParseError, Validation, parse_answer_json, retry_prompt, validate
from .driver import Completed, Driver, DriverSpec, TextDelta, ToolFinished, ToolStarted
from .ledger import ToolLedger
from .numbers import display
from .runs import RunStore

log = get_logger("rp.agent")
MAX_REGION_LOOKUPS = 8


@dataclass(frozen=True)
class CallerIdentity:
    sub: str
    groups: list[str]
    authorization: str  # "Bearer <access token>" as received by the Runtime


def tool_summary(tool: str, payload: dict[str, Any] | None, status: str, error: str | None) -> str:
    if status != "ok" or payload is None:
        return ("denied: " if status == "denied" else "error: ") + (error or "no result")[:120]
    if tool == "search_units":
        return f"{len(payload.get('units', []))} units"
    if tool == "graph_hop":
        rows = payload.get("rowCount", 0)
        total = display(payload.get("total"))
        note = payload.get("note")
        text = f"{rows} rows" + (f" · total {total}" if total else "")
        return text if rows or not note else f"0 rows · {note}"
    if tool == "get_region":
        what = payload.get("fieldLabel") or payload.get("label") or payload.get("unitId")
        return f"p.{payload.get('page')} {what}"
    if tool == "doc_status":
        docs = payload.get("docs", [])
        return f"{len(docs)} docs · {'ready' if payload.get('ready') else 'not ready'}"
    return "ok"


def tool_end_event(ev: ToolFinished, t: float) -> ToolEndEvent:
    payload = ev.payload if ev.status == "ok" else None
    end = ToolEndEvent(call_id=ev.call_id, tool=ev.tool, ms=ev.ms, status=ev.status,
                       summary=tool_summary(ev.tool, ev.payload, ev.status, ev.error), error=ev.error, t=t)
    if payload and ev.tool == "search_units":
        units = payload.get("units", [])
        end.units = [u["unitId"] for u in units]
        end.scores = [{"unitId": u["unitId"], "denseNorm": u["scores"]["denseNorm"],
                       "bm25Norm": u["scores"]["bm25Norm"], "fused": u["scores"]["fused"]} for u in units]
        from evidence_core.models.tools import RetrievedUnit

        end.retrieved = [RetrievedUnit.load(u) for u in units]
    elif payload and ev.tool == "graph_hop":
        end.rows = payload.get("rows", [])
        end.total = payload.get("total")
        end.template = payload.get("template")
    return end


@dataclass
class _RunState:
    run_id: str
    started_at: str
    t0: float
    ledger: ToolLedger = field(default_factory=ToolLedger)
    calls: list[ToolCallRecord] = field(default_factory=list)
    open_calls: dict[str, tuple[ToolStarted, str]] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)

    def t(self) -> float:
        return round(time.monotonic() - self.t0, 3)


@dataclass
class AskRunner:
    pipeline_loader: Callable[[str], LoadedPipeline]
    driver_factory: Callable[[DriverSpec], Driver]
    run_store: RunStore
    golden: list[GoldenEntry]
    gateway_url: str
    identity_mode: str = "interceptor"
    region: str = "us-east-1"
    # package -> (runId, corpus hash): one documents query per ask, shared by the check and the run record
    _corpus_cache: dict[str, tuple[str, str]] = field(default_factory=dict, init=False, repr=False)

    async def run(self, request: AskRequest, caller: CallerIdentity,
                  session_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
        st = _RunState(run_id=new_run_id(), started_at=iso(), t0=time.monotonic())
        if request.pipeline_version not in PIPELINE_VERSIONS:
            yield ErrorEvent(code="bad_request", message=f"unknown pipelineVersion {request.pipeline_version}",
                             run_id=st.run_id).dump()
            return
        lp = self.pipeline_loader(request.pipeline_version)
        tools = [t.split("@", 1)[0] for t in lp.config.agent.tools]
        tools = [t for t in tools if t not in set(request.tools_disabled)]
        injected = {"callerSub": caller.sub, "groups": list(caller.groups),
                    "pipelineVersion": request.pipeline_version, "packageId": request.package_id,
                    "runId": st.run_id}
        model_key, model_id, temperature = self._generation(lp, request.generation_profile)
        spec = DriverSpec(
            model_id=model_id,
            temperature=temperature,
            max_tokens=lp.config.generation.max_tokens,
            system_prompt=lp.prompt_text,
            tools=tools,
            gateway_url=self.gateway_url,
            headers={"Authorization": caller.authorization, HEADER_PIPELINE_VERSION: request.pipeline_version,
                     HEADER_PACKAGE: request.package_id, HEADER_RUN: st.run_id},
            max_tool_calls=lp.config.agent.max_tool_calls,
            inject=injected if self.identity_mode == "agent_hook" else None,
            region=self.region,
        )
        yield RunStartEvent(run_id=st.run_id, package_id=request.package_id,
                            pipeline_version=request.pipeline_version, question=request.question,
                            model_id=spec.model_id, config_sha=lp.config_sha, prompt_sha=lp.prompt_sha,
                            tools=tools).dump()
        answer: AnswerPayload | None = None
        check: Check | None = None
        status, error_text = "ok", None
        driver: Driver | None = None
        try:
            driver = self.driver_factory(spec)
            validation: Validation | None = None
            prompt = request.question
            retries = lp.config.agent.citation_retry
            for attempt in range(retries + 1):
                text = ""
                async for ev in driver.run(prompt):
                    for out in self._on_event(ev, st, injected):
                        yield out
                    if isinstance(ev, Completed):
                        text = ev.text
                try:
                    raw = parse_answer_json(text)
                except AnswerParseError as e:
                    log.info("unparseable answer", extra={"rp": {"runId": st.run_id, "reason": str(e)}})
                    raw = {"answer": text, "claims": [], "caveats": ["The answer was not in the expected format."]}
                validation = validate(raw, st.ledger)
                if "get_region" in tools and validation.needs_regions:
                    for claim in validation.needs_regions[:MAX_REGION_LOOKUPS]:
                        args = {"unitId": claim.unit_id} | ({"fieldId": claim.field_id} if claim.field_id else {})
                        async for ev in driver.call_tool("get_region", args):
                            for out in self._on_event(ev, st, injected):
                                yield out
                    validation = validate(raw, st.ledger)
                validation.payload.citation_retries = attempt
                if not validation.uncited or attempt == retries:
                    break
                prompt = retry_prompt(validation)
            assert validation is not None
            answer = validation.payload
            if validation.dropped:
                log.info("claims dropped", extra={"rp": {"runId": st.run_id,
                                                         "dropped": [d.reason for d in validation.dropped]}})
            yield AnswerEvent(**answer.model_dump()).dump()
            check = self._check(request, answer, lp, self._corpus(request.package_id, st.run_id))
            if check is not None:
                yield CheckEvent(**check.model_dump()).dump()
        except Exception as e:  # the stream must end with an event, never a dropped connection
            log.exception("ask failed", extra={"rp": {"runId": st.run_id}})
            status, error_text = "error", f"{type(e).__name__}: {e}"[:500]
            yield ErrorEvent(code="agent_error", message="The agent could not finish this answer.",
                             run_id=st.run_id).dump()
        finally:
            if driver is not None:
                driver.close()

        latency_ms = int((time.monotonic() - st.t0) * 1000)
        corpus = self._corpus(request.package_id, st.run_id)
        record = RunRecord(
            run_id=st.run_id, created_at=st.started_at, package_id=request.package_id,
            pipeline_version=request.pipeline_version, question=request.question, caller_sub=caller.sub,
            groups=list(caller.groups), model_id=spec.model_id, prompt_sha=lp.prompt_sha,
            config_sha=lp.config_sha, block_shas=lp.block_shas, corpus_hash=corpus, memory_records_injected=0,
            tools_enabled=tools, tool_calls=st.calls, retrieved=st.ledger.retrieved,
            retrieved_text=[u.text for u in st.ledger.retrieved], answer=answer, check=check, usage=st.usage,
            latency_ms=latency_ms, status=status, error=error_text, runtime_session_id=session_id,
            s3_key=run_key(st.run_id), generation_profile=request.generation_profile, model_key=model_key,
        )
        try:
            self.run_store.write(record)
        except Exception:
            log.exception("run record write failed", extra={"rp": {"runId": st.run_id}})
        if status == "ok":
            yield RunEvent(run_id=st.run_id, package_id=request.package_id,
                           pipeline_version=request.pipeline_version, config_sha=lp.config_sha,
                           block_shas=lp.block_shas, prompt_sha=lp.prompt_sha, model_id=spec.model_id,
                           corpus_hash=corpus, memory_records_injected=0, latency_ms=latency_ms, usage=st.usage,
                           started_at=st.started_at, runtime_session_id=session_id,
                           question=request.question).dump()

    # ------------------------------------------------------------------------------------------
    @staticmethod
    def _temperature(lp: LoadedPipeline, model_id: str | None = None) -> float | None:
        """Temperature 0 where the model accepts sampling parameters; else omitted."""
        gen = lp.config.generation
        spec = next((m for m in lp.models.models.values() if m.model_id == (model_id or gen.model_id)), None)
        if spec is not None and not spec.accepts_temperature:
            return None
        return gen.temperature

    @classmethod
    def _generation(cls, lp: LoadedPipeline, profile: str) -> tuple[str | None, str, float | None]:
        """(models.yaml key, model id, temperature) for this ask. ``default`` is the lane's generation model. Another
        profile swaps only the model id, for this run only: prompt, tools, temperature, max tokens and the
        lane config (so its configSha) stay the lane's."""
        if profile == "default":
            key = next((k for k, m in lp.models.models.items() if m.model_id == lp.config.generation.model_id), None)
            return key, lp.config.generation.model_id, cls._temperature(lp)
        key = GENERATION_PROFILE_MODEL_KEYS[profile]
        model_id = lp.models.model_id(key)
        return key, model_id, cls._temperature(lp, model_id)

    def _on_event(self, ev: Any, st: _RunState, injected: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(ev, TextDelta):
            return [TextEvent(delta=ev.text).dump()] if ev.text else []
        if isinstance(ev, ToolStarted):
            st.open_calls[ev.call_id] = (ev, iso())
            return [ToolStartEvent(call_id=ev.call_id, tool=ev.tool, input=ev.input, t=st.t()).dump()]
        if isinstance(ev, ToolFinished):
            started, started_at = st.open_calls.pop(ev.call_id, (ToolStarted(ev.call_id, ev.tool, {}), iso()))
            if ev.status == "ok" and ev.payload is not None:
                try:
                    st.ledger.record(ev.tool, ev.payload)
                except Exception:
                    log.exception("tool result did not match its contract", extra={"rp": {"tool": ev.tool}})
            st.calls.append(ToolCallRecord(
                call_id=ev.call_id, tool=ev.tool, input=started.input,
                injected=injected | {"callerSub": short(injected["callerSub"]), "via": self.identity_mode},
                started_at=started_at, ms=ev.ms, status=ev.status, output=ev.payload or {}, error=ev.error,
            ))
            return [tool_end_event(ev, st.t()).dump()]
        if isinstance(ev, Completed):
            if ev.usage:
                st.usage = ev.usage
            return []
        return []

    def _corpus(self, package_id: str, run_id: str) -> str:
        cached = self._corpus_cache.get(package_id)
        if cached is not None and cached[0] == run_id:
            return cached[1]
        try:
            corpus = self.run_store.corpus_hash(package_id)
        except Exception:
            log.exception("corpus hash failed", extra={"rp": {"runId": run_id}})
            corpus = ""
        self._corpus_cache[package_id] = (run_id, corpus)
        return corpus

    def _check(self, request: AskRequest, answer: AnswerPayload, lp: LoadedPipeline,
               corpus_hash: str | None = None) -> Check | None:
        if not lp.config.agent.golden_set_check or not self.golden:
            return None
        entry = match_golden(request.question, self.golden, request.package_id, corpus_hash or None)
        if entry is None or not entry.headline:
            return None
        actual = normalise_amount(answer.total) if answer.total else None
        expected = normalise_amount(entry.answer)
        return Check(qid=entry.qid, expected=entry.answer, actual=answer.total,
                     match=actual is not None and actual == expected)
