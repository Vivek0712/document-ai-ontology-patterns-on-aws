"""One stage vocabulary everywhere: the registry (evidence_core.stages), the generated stages.json, the
ASL ResultPaths, contracts/api.ts STAGE_ORDER and the UI board groups."""

from __future__ import annotations

import json
import re
from pathlib import Path

from evidence_core.stages import REGISTRY, STAGE_IDS

ROOT = Path(__file__).resolve().parents[2]
ASL = json.loads((ROOT / "contracts/generated/ingest_asl.json").read_text())
ORDER = [s.value for s in STAGE_IDS]


def _states(states: dict) -> dict:
    out = dict(states)
    for st in states.values():
        for b in st.get("Branches", []):
            out |= _states(b["States"])
        if "ItemProcessor" in st:
            out |= _states(st["ItemProcessor"]["States"])
    return out


def test_registry_order_matches_contract_and_ui() -> None:
    ts = (ROOT / "contracts/api.ts").read_text()
    block = re.search(r"STAGE_ORDER: StageId\[\] = \[(.*?)\]", ts, re.S).group(1)
    assert re.findall(r'"(\w+)"', block) == ORDER
    ui = (ROOT / "usecases/provenant/ui/src/lib/stages.ts").read_text()
    groups = re.findall(r'stages: \[([^\]]*)\]', ui.split("GROUPS")[1].split("];")[0])
    assert [s for g in groups for s in re.findall(r'"(\w+)"', g)] == ORDER
    by_group = {s.id.value: s.group.value for s in REGISTRY}
    for line in re.findall(r'\{ group: "(\w+)", title: "[^"]+", stages: \[([^\]]*)\] \}', ui):
        for sid in re.findall(r'"(\w+)"', line[1]):
            assert by_group[sid] == line[0], sid


def test_generated_stages_json_matches_registry() -> None:
    gen = json.loads((ROOT / "contracts/generated/stages.json").read_text())
    for pv, lane in gen.items():
        assert [s["id"] for s in lane["stages"]] == ORDER, pv


def test_every_stage_has_a_writer_in_the_state_machine() -> None:
    states = _states(ASL["States"])
    paths = {st.get("ResultPath") for st in states.values() if st.get("Type") == "Task"}
    written = {p.removeprefix("$.stages.") for p in paths if isinstance(p, str) and p.startswith("$.stages.")}
    written |= {"register", "index", "graph"}  # Register replaces $; index/graph via MergeIndexGraph
    assert set(ORDER) <= written, sorted(set(ORDER) - written)
    assert written - set(ORDER) == {"extractOpen"}
