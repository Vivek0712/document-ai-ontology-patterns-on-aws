"""Offline stand-ins for the AWS services moto does not cover (tests and ``make units`` only).

- ``FakeComprehend``: regex PII detector shaped like ``DetectPiiEntities`` (SSN incl. truncated
  ``XXX-XX-1234``, 8-17 digit account numbers).
- ``FakeTextract``: returns a canned ``AnalyzeDocument`` response.
- ``FakeSfn``: records task-token calls.
- ``FakeAoss``: in-memory index with auto ids, bulk, delete, count.
- ``FakeSparql``: records SPARQL updates; counts nothing.
- ``FakeConverse``: canned Bedrock Converse reply for the classify tie-break.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

_SSN = re.compile(r"(?:\b\d{3}-\d{2}-\d{4}\b|\bX{3}-X{2}-\d{4}\b)")
_ACCOUNT = re.compile(r"(?<!\d)\d{8,17}(?!\d)")


@dataclass
class FakeComprehend:
    calls: int = 0

    def detect_pii_entities(self, Text: str, LanguageCode: str = "en") -> dict[str, Any]:  # noqa: N803
        self.calls += 1
        ents = [{"Type": "SSN", "Score": 0.99, "BeginOffset": m.start(), "EndOffset": m.end()}
                for m in _SSN.finditer(Text)]
        ents += [{"Type": "BANK_ACCOUNT_NUMBER", "Score": 0.97, "BeginOffset": m.start(), "EndOffset": m.end()}
                 for m in _ACCOUNT.finditer(Text)]
        return {"Entities": ents}


@dataclass
class FakeTextract:
    response: dict[str, Any]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def analyze_document(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"FeatureTypes": kwargs.get("FeatureTypes"), "bytes": len(kwargs["Document"]["Bytes"])})
        return dict(self.response)


@dataclass
class FakeSfn:
    successes: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    heartbeats: int = 0

    def send_task_success(self, taskToken: str, output: str) -> None:  # noqa: N803
        self.successes.append({"token": taskToken, "output": output})

    def send_task_failure(self, taskToken: str, error: str, cause: str) -> None:  # noqa: N803
        self.failures.append({"token": taskToken, "error": error, "cause": cause})

    def send_task_heartbeat(self, taskToken: str) -> None:  # noqa: N803
        self.heartbeats += 1


@dataclass
class FakeAoss:
    docs: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)  # index -> id -> body
    created: list[str] = field(default_factory=list)

    def ensure_index(self, index: str, dimensions: int) -> bool:
        if index in self.docs:
            return False
        self.docs[index] = {}
        self.created.append(index)
        return True

    def bulk_index(self, index: str, docs: list[dict[str, Any]]) -> list[str]:
        ids = []
        for d in docs:
            i = uuid.uuid4().hex[:20]
            self.docs.setdefault(index, {})[i] = d
            ids.append(i)
        return ids

    def delete_ids(self, index: str, ids: list[str]) -> int:
        return sum(1 for i in ids if self.docs.get(index, {}).pop(i, None) is not None)

    def count(self, index: str, doc_id: str, pipeline_version: str) -> int:
        return sum(1 for d in self.docs.get(index, {}).values()
                   if d["docId"] == doc_id and d["pipelineVersion"] == pipeline_version)


@dataclass
class FakeSparql:
    updates: list[str] = field(default_factory=list)

    def update(self, sparql: str) -> None:
        self.updates.append(sparql)

    def query(self, sparql: str) -> dict[str, Any]:
        return {"head": {"vars": ["n"]}, "results": {"bindings": [{"n": {"value": "0"}}]}}


@dataclass
class FakeConverse:
    reply: str
    calls: int = 0

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {"output": {"message": {"content": [{"text": self.reply}]}}}


def _geo(left: float, top: float, width: float, height: float) -> dict[str, Any]:
    return {"BoundingBox": {"Left": left, "Top": top, "Width": width, "Height": height},
            "Polygon": [{"X": left, "Y": top}, {"X": left + width, "Y": top},
                        {"X": left + width, "Y": top + height}, {"X": left, "Y": top + height}]}


def textract_response(layout: list[tuple[str, str, tuple[float, float, float, float]]],
                      kvs: list[tuple[str, str, tuple[float, float, float, float], tuple[float, float, float, float],
                                  float]] = ()) -> dict[str, Any]:  # type: ignore[assignment]
    """A minimal but well-formed AnalyzeDocument (LAYOUT + FORMS) response for one page.

    ``layout``: (BlockType, text, (left, top, width, height)) in reading order; ``kvs``: (key, value,
    key box, value box, confidence 0..100), placed inside the first LAYOUT_KEY_VALUE block.
    """
    blocks: list[dict[str, Any]] = []
    page_children: list[str] = []
    n = [0]

    def nid(prefix: str) -> str:
        n[0] += 1
        return f"{prefix}-{n[0]:04d}"

    def words_line(text: str, box: tuple[float, float, float, float], conf: float = 99.0) -> str:
        wid, lid = nid("w"), nid("l")
        blocks.append({"BlockType": "WORD", "Id": wid, "Text": text, "Confidence": conf, "Geometry": _geo(*box),
                       "Page": 1, "TextType": "PRINTED"})
        blocks.append({"BlockType": "LINE", "Id": lid, "Text": text, "Confidence": conf, "Geometry": _geo(*box),
                       "Page": 1, "Relationships": [{"Type": "CHILD", "Ids": [wid]}]})
        page_children.append(lid)
        return lid

    for block_type, text, box in layout:
        line_ids = [words_line(t, (box[0], box[1] + i * 0.02, box[2], 0.015)) for i, t in enumerate(text.split("\n"))
                    if t] if text else []
        lay = {"BlockType": block_type, "Id": nid("lay"), "Confidence": 95.0, "Geometry": _geo(*box), "Page": 1,
               "Relationships": [{"Type": "CHILD", "Ids": line_ids}]}
        blocks.append(lay)
        page_children.append(lay["Id"])
        if block_type == "LAYOUT_KEY_VALUE" and kvs:
            for key, value, kbox, vbox, conf in kvs:
                kw, vw = nid("w"), nid("w")
                blocks.append({"BlockType": "WORD", "Id": kw, "Text": key, "Confidence": conf, "Geometry": _geo(*kbox),
                               "Page": 1, "TextType": "PRINTED"})
                blocks.append({"BlockType": "WORD", "Id": vw, "Text": value, "Confidence": conf,
                               "Geometry": _geo(*vbox), "Page": 1, "TextType": "PRINTED"})
                kid, vid = nid("k"), nid("v")
                blocks.append({"BlockType": "KEY_VALUE_SET", "Id": kid, "EntityTypes": ["KEY"], "Confidence": conf,
                               "Geometry": _geo(*kbox), "Page": 1,
                               "Relationships": [{"Type": "VALUE", "Ids": [vid]}, {"Type": "CHILD", "Ids": [kw]}]})
                blocks.append({"BlockType": "KEY_VALUE_SET", "Id": vid, "EntityTypes": ["VALUE"], "Confidence": conf,
                               "Geometry": _geo(*vbox), "Page": 1, "Relationships": [{"Type": "CHILD", "Ids": [vw]}]})
                page_children += [kid, vid]
            kvs = ()  # type: ignore[assignment]
    blocks.insert(0, {"BlockType": "PAGE", "Id": "page-1", "Geometry": _geo(0, 0, 1, 1), "Page": 1,
                      "Relationships": [{"Type": "CHILD", "Ids": page_children}]})
    return {"DocumentMetadata": {"Pages": 1}, "Blocks": blocks, "AnalyzeDocumentModelVersion": "1.0"}


def scanned_payslip_response(gross: str = "6,500.00", k401: str = "400.00", ytd: str = "58,500.00",
                             ytd_confidence: float = 72.0) -> dict[str, Any]:
    """What Textract returns for the synthetic scanned payslip (``local_pipeline.synthetic``)."""
    return textract_response(
        layout=[
            ("LAYOUT_TITLE", "Payslip · NewAge Global Fintech Payments", (0.08, 0.06, 0.6, 0.02)),
            ("LAYOUT_TEXT", "Employee: Arjun Fictus", (0.08, 0.095, 0.4, 0.015)),
            ("LAYOUT_TEXT", "Pay period 01–30 Sep 2026   Pay date 30 Sep 2026", (0.08, 0.115, 0.6, 0.015)),
            ("LAYOUT_SECTION_HEADER", "Earnings", (0.08, 0.17, 0.2, 0.015)),
            ("LAYOUT_KEY_VALUE", "", (0.09, 0.19, 0.58, 0.07)),
            ("LAYOUT_FOOTER", "Fictitious document · NewAge Global Fintech · for demonstration only",
             (0.08, 0.955, 0.5, 0.01)),
        ],
        kvs=[("Gross pay", gross, (0.098, 0.192, 0.12, 0.015), (0.58, 0.192, 0.08, 0.015), 96.0),
             ("401(k) deferral", k401, (0.098, 0.215, 0.14, 0.015), (0.6, 0.215, 0.06, 0.015), 94.0),
             ("YTD gross", ytd, (0.098, 0.238, 0.1, 0.015), (0.58, 0.238, 0.08, 0.015), ytd_confidence)],
    )


@dataclass
class FixtureDocling:
    """Docling backend that returns recorded DoclingDocument JSON per file name (tests)."""

    fixtures: dict[str, dict[str, Any]]
    engine_version: str = "docling 2.145.0 (recorded fixture)"
    converted: list[str] = field(default_factory=list)

    def wait_ready(self, timeout_s: float, tick: Any) -> str:
        tick("docling fixture ready")
        return self.engine_version

    def convert(self, pdf: bytes, file_name: str, tick: Any, **_: Any) -> Any:
        from .docling_client import DoclingResult

        self.converted.append(file_name)
        stem = file_name.rsplit(".", 1)[0]
        return DoclingResult(document=self.fixtures[stem], engine_version=self.engine_version, processing_ms=1200,
                             task_id=f"task-{stem}")


def load_docling_fixtures(directory: Any) -> dict[str, dict[str, Any]]:
    import json
    from pathlib import Path

    return {p.name.removesuffix(".docling.json"): json.loads(p.read_text())
            for p in Path(directory).glob("*.docling.json")}
