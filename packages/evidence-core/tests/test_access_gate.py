"""Ask-gate refusal rows (rp-access-log, SC-7)."""

from __future__ import annotations

from datetime import UTC, datetime

from evidence_core import access_gate
from evidence_core.models.golden import GoldenEntry
from evidence_core.models.governance import AccessDecision

GOLD = GoldenEntry(qid="q-lend-01", type="table", question="What is the total income across this package?",
                   answer="169,650.00", answer_pattern="169",
                   aliases=["What is the total income for this lending package?"])


def test_refusal_row_hashes_the_question_and_keeps_six_characters_of_the_sub() -> None:
    row = access_gate.refusal_row(
        path="runtime", request_id="sess-1", sub="abcdef-1234", groups=["markets-analyst"], package_id="pkg-1",
        pipeline_version="2.3.1", question="  what is the TOTAL income for this lending package ", code="forbidden",
        reason="no", golden=[GOLD], now=datetime(2026, 11, 11, 12, 0, tzinfo=UTC))
    assert row.run_id == "gate#pkg-1" and row.call_id == "sess-1" and row.caller_sub6 == "abcdef"
    assert row.at == "2026-11-11T12:00:00.000Z" and row.day == "2026-11-11" and row.decision == "deny"
    assert row.gate is not None and row.gate.question_id == "q-lend-01"  # an alias, normalised
    assert row.gate.question_sha == access_gate.question_sha("What is the total income for this lending package?")
    assert AccessDecision.load(row.dump()) == row


def test_write_is_best_effort() -> None:
    class Broken:
        def put_item(self, **_: object) -> None:
            raise RuntimeError("no table")

    row = access_gate.refusal_row(path="api", request_id="r", sub="s", groups=[], package_id="p",
                                  pipeline_version="1.0", question="q", code="forbidden", reason="no")
    assert access_gate.write(None, row) is False and access_gate.write(Broken(), row) is False
    assert row.gate is not None and row.gate.question_id is None
