"""Scripted driver + tool payloads for agent tests (no Bedrock, no Gateway)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evidence_core.bbox import BBox
from evidence_core.config import LocalConfigSource, load_pipeline
from evidence_core.enums import UnitType
from evidence_core.models.tools import (
    FieldBrief,
    GraphHopResult,
    GraphOperand,
    RegionResult,
    RetrievedUnit,
    SearchQueryEcho,
    SearchUnitsResult,
    UnitScores,
)
from rp_agent.driver import Completed, DriverEvent, DriverSpec, TextDelta, ToolFinished, ToolStarted

REPO = Path(__file__).resolve().parents[3]
MAYA = "w2-2025-maya-fictus-3fa2c1:2.3.1:eu-01"
LEO = "w2-2025-leo-example-77ab01:2.3.1:eu-01"
CHUNK = "w2-2025-maya-fictus-3fa2c1:1.0:c-03"


def loader(pv: str):
    return load_pipeline(pv, LocalConfigSource(REPO))


def retrieved(uid: str, name: str, value: str, rank: int, *, with_bbox: bool = True) -> RetrievedUnit:
    box1 = BBox(page=1, x0=50, y0=216, x1=306, y1=284)
    return RetrievedUnit(
        rank=rank, unit_id=uid, doc_id=uid.split(":")[0], file_name=f"{uid.split(':')[0]}.pdf",
        unit_type=UnitType.TABLE_PANEL, label=f"W-2 · {name} · Boxes 1–6", doc_type="doc:W2",
        breadcrumb=f"W-2 2025 › {name}", text=f"1 Wages, tips, other compensation │ {value}", pages=[1],
        page_width=612, page_height=792, bboxes=[BBox(page=1, x0=50, y0=216, x1=562, y1=422)] if with_bbox else [],
        fields=[FieldBrief(field_id="f-box1", label="1 Wages, tips, other compensation", value=value,
                           value_number=value.replace(",", ""), concept="fin:W2WagesBox1",
                           bbox=box1 if with_bbox else None, page=1 if with_bbox else None)],
        resolvable=with_bbox,
        scores=UnitScores(dense_norm=1.0 / rank, bm25_norm=0.5, fused=round(0.7 / rank + 0.15, 4)),
    )


def chunk_unit() -> RetrievedUnit:
    return RetrievedUnit(
        rank=1, unit_id=CHUNK, doc_id=CHUNK.split(":")[0], file_name="w2_2025_maya_fictus.pdf",
        unit_type=UnitType.CHUNK, label="chunk c-03", chunk_label="chunk c-03",
        text="Wages tips other compensation 98,400.00 Social security wages 105,000.00", pages=[1],
        resolvable=False, scores=UnitScores(dense_norm=1, bm25_norm=1, fused=1),
    )


def search_payload(units: list[RetrievedUnit], pv: str = "2.3.1") -> dict[str, Any]:
    return SearchUnitsResult(units=units, query=SearchQueryEcho(
        index="units-v" + pv.replace(".", "-"), k=5, sub_query_k=20, weights={"dense": 0.7, "bm25": 0.3},
        filter={"bool": {}}, embed_model="amazon.titan-embed-text-v2:0"), latency_ms=40).dump()


def graph_payload() -> dict[str, Any]:
    return GraphHopResult(
        template="qi_sum_w2_box1_v1", sparql="SELECT ...", graphs=["urn:rp:graph:abox:pkg-lending-01:2.3.1"],
        columns=["borrower", "taxYear", "wagesBox1", "unit", "field"],
        rows=[{"borrower": "Maya Fictus", "wagesBox1": "98400"}, {"borrower": "Leo Example", "wagesBox1": "71250"}],
        row_count=2, total="169650.00",
        operands=[GraphOperand(unit_id=MAYA, field_id="f-box1", value="98400.00", label="Maya Fictus"),
                  GraphOperand(unit_id=LEO, field_id="f-box1", value="71250.00", label="Leo Example")],
        latency_ms=30).dump()


def region_payload(uid: str) -> dict[str, Any]:
    return RegionResult(unit_id=uid, field_id="f-box1", doc_id=uid.split(":")[0], file_name="x.pdf",
                        label="W-2 · Leo Example · Boxes 1–6", page=1,
                        bbox=BBox(page=1, x0=50, y0=216, x1=306, y1=284),
                        field_bbox=BBox(page=1, x0=50, y0=216, x1=306, y1=284),
                        unit_bboxes=[BBox(page=1, x0=50, y0=216, x1=562, y1=422)], page_width=612, page_height=792,
                        page_image_key="gold/x/p001.png", unit_type=UnitType.TABLE_PANEL,
                        pipeline_version="2.3.1", field_label="1 Wages, tips, other compensation").dump()


GOOD_ANSWER = {
    "answer": "Total qualifying income is 169,650.00 USD: Maya Fictus 98,400.00 and Leo Example 71,250.00.",
    "total": "169,650.00",
    "currency": "USD",
    "arithmetic": "98,400.00 + 71,250.00 = 169,650.00",
    "claims": [
        {"text": "98,400.00", "value": "98400.00", "unitId": MAYA, "fieldId": "f-box1"},
        {"text": "71,250.00", "value": "71250.00", "unitId": LEO, "fieldId": "f-box1"},
    ],
    "caveats": ["Payslips corroborate and are not added."],
}


@dataclass
class Turn:
    tools: list[tuple[str, dict[str, Any], dict[str, Any] | None, str]]  # (tool, input, payload, status)
    reply: Any  # dict -> JSON text, str -> raw text


@dataclass
class ScriptedDriver:
    spec: DriverSpec
    turns: list[Turn]
    direct: dict[str, Any] = field(default_factory=dict)  # unitId -> get_region payload
    prompts: list[str] = field(default_factory=list)
    direct_calls: list[dict[str, Any]] = field(default_factory=list)
    closed: bool = False
    _n: int = 0

    async def run(self, prompt: str) -> AsyncIterator[DriverEvent]:
        self.prompts.append(prompt)
        turn = self.turns[min(len(self.prompts), len(self.turns)) - 1]
        for tool, inp, payload, status in turn.tools:
            self._n += 1
            cid = f"tu-{self._n}"
            yield ToolStarted(cid, tool, inp)
            yield ToolFinished(cid, tool, 12, status, payload,  # type: ignore[arg-type]
                               None if status == "ok" else (payload or {}).get("error", {}).get("message"))
        text = json.dumps(turn.reply) if isinstance(turn.reply, dict) else turn.reply
        for i in range(0, len(text), 40):
            yield TextDelta(text[i : i + 40])
        yield Completed(text=text, usage={"inputTokens": 100, "outputTokens": 50, "totalTokens": 150})

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> AsyncIterator[DriverEvent]:
        self.direct_calls.append(arguments)
        self._n += 1
        cid = f"agent-{self._n}"
        yield ToolStarted(cid, tool, arguments)
        payload = self.direct.get(arguments["unitId"])
        yield ToolFinished(cid, tool, 5, "ok" if payload else "error", payload, None if payload else "not found")

    def close(self) -> None:
        self.closed = True
