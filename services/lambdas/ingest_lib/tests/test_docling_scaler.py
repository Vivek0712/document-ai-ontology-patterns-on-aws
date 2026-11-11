"""Demand-driven docling-serve floor: raise on ExtractOpen, lower after idle, never touch a prewarm."""

from __future__ import annotations

from typing import Any

from lambdas.ingest_lib.docling_scaler import IDLE_SECONDS, MAX_CAPACITY, DoclingScaler, Floor


class _NotFound(Exception):
    pass


class _Ssm:
    class exceptions:  # noqa: N801 - mirrors the boto3 client attribute
        ParameterNotFound = _NotFound

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get_parameter(self, Name: str) -> dict[str, Any]:  # noqa: N803
        if Name not in self.values:
            raise _NotFound(Name)
        return {"Parameter": {"Value": self.values[Name]}}

    def put_parameter(self, Name: str, Value: str, **_: Any) -> None:  # noqa: N803
        self.values[Name] = Value


class _Aas:
    def __init__(self) -> None:
        self.min = 0
        self.registered: list[tuple[int, int]] = []

    def describe_scalable_targets(self, **_: Any) -> dict[str, Any]:
        return {"ScalableTargets": [{"MinCapacity": self.min, "MaxCapacity": MAX_CAPACITY}]}

    def register_scalable_target(self, MinCapacity: int, MaxCapacity: int, **_: Any) -> None:  # noqa: N803
        self.min = MinCapacity
        self.registered.append((MinCapacity, MaxCapacity))


class _Ecs:
    def __init__(self) -> None:
        self.desired = 0

    def describe_services(self, **_: Any) -> dict[str, Any]:
        return {"services": [{"desiredCount": self.desired}]}

    def update_service(self, desiredCount: int, **_: Any) -> None:  # noqa: N803
        self.desired = desiredCount


class _Sqs:
    def __init__(self) -> None:
        self.depth = 0

    def get_queue_attributes(self, **_: Any) -> dict[str, Any]:
        return {"Attributes": {"ApproximateNumberOfMessages": "0",
                               "ApproximateNumberOfMessagesNotVisible": str(self.depth),
                               "ApproximateNumberOfMessagesDelayed": "0"}}


class _Clients:
    def __init__(self) -> None:
        self.map = {"ssm": _Ssm(), "application-autoscaling": _Aas(), "ecs": _Ecs(), "sqs": _Sqs()}

    def client(self, name: str) -> Any:
        return self.map[name]


def _scaler() -> tuple[DoclingScaler, _Clients]:
    c = _Clients()
    return DoclingScaler(c, "https://sqs.example/rp-docling"), c


def test_demand_raises_floor_and_starts_one_task() -> None:
    s, c = _scaler()
    assert s.ensure_capacity(now=1000.0)["action"] == "scale_out"
    assert c.map["application-autoscaling"].registered == [(1, 1)] and c.map["ecs"].desired == 1
    assert s.floor() == Floor("demand", 1000.0)
    assert s.ensure_capacity(now=1010.0)["action"] == "none"  # already up


def test_reaper_waits_for_idle_and_empty_queue() -> None:
    s, c = _scaler()
    s.ensure_capacity(now=1000.0)
    assert s.reap(now=1000.0 + IDLE_SECONDS - 1)["action"] == "none"
    c.map["sqs"].depth = 1
    assert s.reap(now=1000.0 + IDLE_SECONDS + 1)["action"] == "none"
    c.map["sqs"].depth = 0
    assert s.reap(now=1000.0 + IDLE_SECONDS + 1)["action"] == "scale_in"
    assert c.map["application-autoscaling"].min == 0 and c.map["ecs"].desired == 0
    assert s.floor().owner == "none"


def test_prewarmed_floor_is_left_alone() -> None:
    s, c = _scaler()
    c.map["ssm"].put_parameter(Name=s.param, Value=Floor("prewarm", 0.0).dump())
    c.map["application-autoscaling"].min = 1
    assert s.ensure_capacity(now=5000.0)["reason"] == "prewarmed"
    assert s.reap(now=10 ** 9)["action"] == "none"
    assert c.map["application-autoscaling"].min == 1
