"""Run records: full JSON at ``runs/{runId}.json``, summary row in ``rp-runs``.

Also computes the package corpus hash from the source sha256 of every document in Bronze.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from boto3.dynamodb.conditions import Key
from evidence_core.ddb import from_ddb, to_ddb
from evidence_core.hashing import corpus_hash
from evidence_core.models.answer import RunRecord, RunSummary


class RunStore(Protocol):
    def corpus_hash(self, package_id: str) -> str: ...

    def write(self, record: RunRecord) -> None: ...


def summary_of(record: RunRecord) -> RunSummary:
    answer = record.answer
    return RunSummary(
        run_id=record.run_id,
        created_at=record.created_at,
        package_id=record.package_id,
        pipeline_version=record.pipeline_version,
        question=record.question,
        caller_sub=record.caller_sub,
        model_id=record.model_id,
        prompt_sha=record.prompt_sha,
        config_sha=record.config_sha,
        corpus_hash=record.corpus_hash,
        total=answer.total if answer else None,
        check_match=record.check.match if record.check else None,
        qid=record.check.qid if record.check else None,
        claims=len(answer.claims) if answer else 0,
        resolvable_claims=sum(1 for c in answer.claims if c.resolvable) if answer else 0,
        tool_sequence=[c.tool for c in record.tool_calls],
        latency_ms=record.latency_ms,
        status=record.status,
        memory_records_injected=record.memory_records_injected,
        s3_key=record.s3_key,
        generation_profile=record.generation_profile,
    )


@dataclass
class AwsRunStore:
    bucket: str
    runs_table: Any  # boto3 Table rp-runs
    documents_table: Any  # boto3 Table rp-documents
    s3: Any

    @classmethod
    def from_settings(cls, settings: Any) -> AwsRunStore:
        import boto3

        ddb = boto3.resource("dynamodb", region_name=settings.region)
        return cls(bucket=settings.data_bucket, runs_table=ddb.Table(settings.table("runs")),
                   documents_table=ddb.Table(settings.table("documents")),
                   s3=boto3.client("s3", region_name=settings.region))

    def corpus_hash(self, package_id: str) -> str:
        shas: list[str] = []
        kwargs: dict[str, Any] = {"KeyConditionExpression": Key("packageId").eq(package_id),
                                  "ProjectionExpression": "sha256, uploadState"}
        while True:
            resp = self.documents_table.query(**kwargs)
            for item in resp.get("Items", []):
                it = from_ddb(item)
                if it.get("sha256") and it.get("uploadState") == "IN_BRONZE":
                    shas.append(it["sha256"])
            if "LastEvaluatedKey" not in resp:
                return corpus_hash(shas)
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def write(self, record: RunRecord) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=record.s3_key, Body=record.dump_json().encode(),
                           ContentType="application/json")
        self.runs_table.put_item(Item=to_ddb(summary_of(record).dump()))


@dataclass
class MemoryRunStore:
    """Tests and local mode."""

    corpus: str = "0" * 64
    records: list[RunRecord] | None = None

    def corpus_hash(self, package_id: str) -> str:
        return self.corpus

    def write(self, record: RunRecord) -> None:
        if self.records is None:
            self.records = []
        self.records.append(RunRecord.load(json.loads(record.dump_json())))
