"""In-process stand-in for the ``rp-ingest`` state machine (dev and tests only).

It calls the same Lambda handlers with the same envelopes the ASL builds: Register,
ModeChoice, TextLayer/NaiveChunk or Route/ExtractOpen/Extract(Docling task token + Textract Map)/
ExtractClose/Classify/BuildUnits, then PiiGate, Promote, IndexAndGraph, MarkReady; any failure goes
through LaneFailed. ``until`` stops after a stage (``make units`` stops after ``units``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from evidence_core.enums import StageId
from evidence_core.models.status import StageResult
from lambdas.classify import handler as classify
from lambdas.docling_call import handler as docling_call
from lambdas.embed_index import handler as embed_index
from lambdas.eu_builder import handler as eu_builder
from lambdas.graph_write import handler as graph_write
from lambdas.ingest_lib.aws import services
from lambdas.mark_ready import handler as mark_ready
from lambdas.naive_chunk import handler as naive_chunk
from lambdas.pii_gate import handler as pii_gate
from lambdas.promote import handler as promote
from lambdas.register import handler as register
from lambdas.route import handler as route
from lambdas.stage_status import handler as stage_status
from lambdas.text_layer import handler as text_layer
from lambdas.textract_page import handler as textract_page

ORDER = [s.value for s in StageId]


class LaneFailed(RuntimeError):
    pass


@dataclass
class LaneRun:
    pipeline_version: str
    state: dict[str, Any] = field(default_factory=dict)
    status: str = "RUNNING"  # RUNNING | SUCCEEDED | FAILED | REJECTED | SKIPPED | STOPPED
    error: dict[str, Any] | None = None
    states_visited: list[str] = field(default_factory=list)

    @property
    def ctx(self) -> dict[str, Any]:
        return self.state.get("ctx", {})


def _envelope(state: dict[str, Any], name: str, execution_arn: str) -> dict[str, Any]:
    return {"ctx": state["ctx"], "stages": state.get("stages", {}), "executionArn": execution_arn,
            "stateName": name, "attempt": 0}


@dataclass
class LocalIngest:
    execution_prefix: str = "arn:aws:states:us-east-1:000000000000:execution:rp-ingest:local"
    until: str | None = None  # stop after this stage id

    def run(self, trigger: dict[str, Any]) -> LaneRun:
        pv = trigger["pipelineVersion"]
        arn = f"{self.execution_prefix}-{pv.replace('.', '-')}"
        lane = LaneRun(pipeline_version=pv)
        try:
            self._run(trigger, arn, lane)
            if lane.status == "RUNNING":
                lane.status = "SUCCEEDED"
        except _Stop:
            lane.status = "STOPPED"
        except Exception as exc:  # noqa: BLE001 - the Catch → LaneFailed path
            lane.status = "FAILED"
            lane.error = {"Error": type(exc).__name__, "Cause": json.dumps({"errorMessage": str(exc)})}
            if lane.state.get("ctx"):
                stage_status.handler({"op": "lane_failed", "ctx": lane.state["ctx"], "error": lane.error,
                                      "stages": {}, "executionArn": arn, "stateName": "LaneFailed"})
        return lane

    def _task(self, lane: LaneRun, name: str, stage: str, fn: Any, arn: str, **extra: Any) -> dict[str, Any]:
        lane.states_visited.append(name)
        event = _envelope(lane.state, name, arn) | extra
        out = fn(event, None)
        lane.state.setdefault("stages", {})[stage] = out
        if self.until and stage == self.until:
            raise _Stop
        return out

    def _run(self, trigger: dict[str, Any], arn: str, lane: LaneRun) -> None:
        lane.states_visited.append("Register")
        lane.state = register.handler({"trigger": trigger, "executionArn": arn})
        validation = lane.state["stages"]["register"]["output"]["validation"]
        if validation == "skipped":
            lane.status = "SKIPPED"
            return
        if validation == "quarantined":
            lane.status = "REJECTED"
            return
        if self.until == "register":
            raise _Stop
        if lane.ctx["mode"] == "naive":
            self._task(lane, "TextLayer", "extract", text_layer.handler, arn)
            self._task(lane, "NaiveChunk", "units", naive_chunk.handler, arn)
        else:
            self._evidence_branch(lane, arn)
        self._task(lane, "PiiGate", "pii_gate", pii_gate.handler, arn)
        self._task(lane, "Promote", "promote", promote.handler, arn)
        lane.states_visited.append("IndexAndGraph")
        idx = embed_index.handler(_envelope(lane.state, "EmbedIndex", arn), None)
        if lane.ctx["graphEnabled"]:
            graph = graph_write.handler(_envelope(lane.state, "GraphWrite", arn), None)
        else:
            graph = {"stageId": "graph", "state": "SKIPPED"}
        lane.state["stages"]["index"] = idx
        lane.state["stages"]["graph"] = graph
        if self.until in ("index", "graph"):
            raise _Stop
        self._task(lane, "MarkReady", "ready", mark_ready.handler, arn)

    def _evidence_branch(self, lane: LaneRun, arn: str) -> None:
        r = self._task(lane, "Route", "route", route.handler, arn)
        lane.states_visited.append("ExtractOpen")
        lane.state["stages"]["extractOpen"] = stage_status.handler(
            _envelope(lane.state, "ExtractOpen", arn) | {"op": "start", "stageId": "extract"}, None)
        digital, scan = r["output"]["digitalPages"], r["output"]["scanPages"]
        docling_part: Any = {}
        if digital:
            lane.states_visited.append("DoclingExtract")
            token = f"local-token-{lane.ctx['docId']}"
            body = {"taskToken": token, "ctx": lane.ctx, "pages": digital, "executionArn": arn}
            docling_call.handler({"Records": [{"messageId": "m-1", "body": json.dumps(body)}]}, None)
            sfn = services().sfn
            sent = [s for s in getattr(sfn, "successes", []) if s["token"] == token]
            failed = [f for f in getattr(sfn, "failures", []) if f["token"] == token]
            if failed:
                raise RuntimeError(f"{failed[-1]['error']}: {failed[-1]['cause']}")
            if not sent:
                raise RuntimeError("docling shim sent no task result")
            docling_part = json.loads(sent[-1]["output"])
        textract_parts = []
        for page in scan:
            lane.states_visited.append("TextractPage")
            textract_parts.append(textract_page.handler({"ctx": lane.ctx, "page": page, "executionArn": arn}, None))
        lane.states_visited.append("ExtractClose")
        closed = stage_status.handler(_envelope(lane.state, "ExtractClose", arn)
                                      | {"op": "close_extract", "extractParts": [docling_part, textract_parts]}, None)
        lane.state["stages"]["extract"] = closed
        if self.until == "extract":
            raise _Stop
        if lane.ctx["classifyEnabled"]:
            self._task(lane, "Classify", "classify", classify.handler, arn)
        self._task(lane, "BuildUnits", "units", eu_builder.handler, arn)


class _Stop(Exception):  # noqa: N818 - control flow for ``until``
    pass


def result_of(lane: LaneRun, stage: StageId) -> StageResult | None:
    raw = lane.state.get("stages", {}).get(stage.value)
    return StageResult.load(raw) if raw and raw.get("stageId") else None
