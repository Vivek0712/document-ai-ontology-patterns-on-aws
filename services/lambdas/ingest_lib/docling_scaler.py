"""Demand-driven capacity for docling-serve on Fargate (one task, scale to zero).

The CloudWatch alarms on the docling queue are only a backstop: SQS metrics of a queue that was idle
for more than 6 hours can take up to 15 minutes to resume, which is too slow for a cold start. So:

- ``ensure_capacity`` (called by ExtractOpen when a 2.3.1 lane has digital pages) raises the scaling
  floor to 1, which starts the task right away, and records the demand time in an SSM parameter.
- ``reap`` (an EventBridge schedule every 5 minutes) lowers the floor back to 0 once the last demand
  is older than ``IDLE_SECONDS`` and the queue is empty right now (real-time SQS attributes, not
  CloudWatch metrics).

The floor parameter also records ``prewarm`` (``scripts/prewarm.sh``): a prewarmed floor is never
lowered here; ``scripts/pause.sh`` resets it to ``none``.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from evidence_core.logs import get_logger, log_kv

FLOOR_PARAM = "/rp/docling/floor"
ECS_CLUSTER = "rp-cluster"
ECS_SERVICE = "rp-docling"
MAX_CAPACITY = 1  # docling-serve keeps async task state in process memory: exactly one task
IDLE_SECONDS = 20 * 60
DEMAND_REFRESH_SECONDS = 60
log = get_logger("rp-docling-scaler")


@dataclass
class Floor:
    owner: str  # none | demand | prewarm
    at: float

    @classmethod
    def parse(cls, raw: str | None) -> Floor:
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            data = {}
        return cls(owner=str(data.get("owner") or "none"), at=float(data.get("at") or 0.0))

    def dump(self) -> str:
        return json.dumps({"owner": self.owner, "at": round(self.at, 3)})


class DoclingScaler:
    def __init__(self, clients: Any, queue_url: str | None, *, param: str = FLOOR_PARAM,
                 cluster: str = ECS_CLUSTER, service: str = ECS_SERVICE) -> None:
        self.ssm = clients.client("ssm")
        self.aas = clients.client("application-autoscaling")
        self.ecs = clients.client("ecs")
        self.sqs = clients.client("sqs")
        self.queue_url = queue_url
        self.param = param
        self.cluster = cluster
        self.service = service

    @property
    def resource_id(self) -> str:
        return f"service/{self.cluster}/{self.service}"

    def floor(self) -> Floor:
        try:
            return Floor.parse(self.ssm.get_parameter(Name=self.param)["Parameter"]["Value"])
        except self.ssm.exceptions.ParameterNotFound:
            return Floor("none", 0.0)

    def _set_floor(self, floor: Floor) -> None:
        self.ssm.put_parameter(Name=self.param, Value=floor.dump(), Type="String", Overwrite=True)

    def _min_capacity(self) -> int:
        targets = self.aas.describe_scalable_targets(ServiceNamespace="ecs", ResourceIds=[self.resource_id],
                                                     ScalableDimension="ecs:service:DesiredCount")
        found = targets.get("ScalableTargets") or []
        return int(found[0]["MinCapacity"]) if found else 0

    def _register(self, minimum: int) -> None:
        self.aas.register_scalable_target(ServiceNamespace="ecs", ResourceId=self.resource_id,
                                          ScalableDimension="ecs:service:DesiredCount",
                                          MinCapacity=minimum, MaxCapacity=MAX_CAPACITY)

    def _desired(self) -> int:
        svc = self.ecs.describe_services(cluster=self.cluster, services=[self.service])["services"]
        return int(svc[0]["desiredCount"]) if svc else 0

    def queue_depth(self) -> int:
        if not self.queue_url:
            return 0
        attrs = self.sqs.get_queue_attributes(
            QueueUrl=self.queue_url,
            AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
                            "ApproximateNumberOfMessagesDelayed"],
        )["Attributes"]
        return sum(int(v) for v in attrs.values())

    def ensure_capacity(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        floor = self.floor()
        if floor.owner == "prewarm":
            return {"action": "none", "reason": "prewarmed"}
        if floor.owner != "demand" or now - floor.at >= DEMAND_REFRESH_SECONDS:
            self._set_floor(Floor("demand", now))
        raised = False
        if self._min_capacity() < 1:
            self._register(1)
            raised = True
        if self._desired() < 1:
            self.ecs.update_service(cluster=self.cluster, service=self.service, desiredCount=1)
            raised = True
        log_kv(log, "docling demand", raised=raised)
        return {"action": "scale_out" if raised else "none", "reason": "demand"}

    def reap(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        floor = self.floor()
        if floor.owner != "demand":
            return {"action": "none", "reason": f"floor owner {floor.owner}"}
        idle_for = now - floor.at
        if idle_for < IDLE_SECONDS:
            return {"action": "none", "reason": f"last demand {int(idle_for)} s ago"}
        depth = self.queue_depth()
        if depth > 0:
            return {"action": "none", "reason": f"{depth} message(s) queued or in flight"}
        self._register(0)
        self.ecs.update_service(cluster=self.cluster, service=self.service, desiredCount=0)
        self._set_floor(Floor("none", now))
        log_kv(log, "docling reaped", idleSeconds=int(idle_for))
        return {"action": "scale_in", "reason": f"idle {int(idle_for)} s, queue empty"}


def scaler_from_env(clients: Any, queue_url: str | None) -> DoclingScaler:
    e = os.environ
    return DoclingScaler(clients, queue_url, param=e.get("RP_DOCLING_FLOOR_PARAM", FLOOR_PARAM),
                         cluster=e.get("RP_DOCLING_CLUSTER", ECS_CLUSTER),
                         service=e.get("RP_DOCLING_SERVICE", ECS_SERVICE))
