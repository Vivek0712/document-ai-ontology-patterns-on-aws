"""export_runs.sh: rp-runs summaries + runs/*.json records into the repo's runs/ (replay fixtures and
the logged runs). Idempotent: existing files are overwritten with the same content."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from boto3.dynamodb.conditions import Key
from evidence_core import s3keys
from evidence_core.ddb import from_ddb
from evidence_core.models.answer import RunRecord, RunSummary

from .aws import Aws, log


def scan_runs(aws: Aws, package_id: str | None = None) -> list[RunSummary]:
    table = aws.table("runs")
    items: list[dict[str, Any]] = []
    if package_id:
        kwargs: dict[str, Any] = {"IndexName": "byPackage", "KeyConditionExpression": Key("packageId").eq(package_id)}
        op = table.query
    else:
        kwargs, op = {}, table.scan
    while True:
        resp = op(**kwargs)
        items += resp.get("Items", [])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    runs = [RunSummary.load(from_ddb(i)) for i in items]
    return sorted(runs, key=lambda r: r.created_at)


def export_runs(aws: Aws, out_dir: Path, *, package_id: str | None = None, with_records: bool = True
                ) -> dict[str, int]:
    bucket = aws.ssm("data_bucket")
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = scan_runs(aws, package_id)
    index_path = out_dir / "runs_table.jsonl"
    index_path.write_text("".join(s.dump_json() + "\n" for s in summaries), encoding="utf-8")
    log(f"{len(summaries)} run summaries -> {index_path}")
    written = missing = 0
    if with_records:
        s3 = aws.client("s3")
        for s in summaries:
            key = s.s3_key or s3keys.run_key(s.run_id)
            try:
                body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            except s3.exceptions.NoSuchKey:
                missing += 1
                log(f"missing record {key}")
                continue
            record = RunRecord.load(json.loads(body))  # validate before it becomes a replay fixture
            (out_dir / f"{record.run_id}.json").write_text(
                json.dumps(record.dump(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            written += 1
        log(f"{written} run records -> {out_dir}/<runId>.json ({missing} missing)")
    return {"summaries": len(summaries), "records": written, "missing": missing}
