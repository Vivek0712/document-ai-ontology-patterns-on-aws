"""List prices (``config/prices.yaml``) for cost estimates and budget guards.

An amount computed here is always ``estimate · measured usage × list price``: the usage is measured (tokens a
Converse call reported, pages, seconds a stage logged), the price is the list price with its ``source`` and
``asOf``. Budget guards use ``guard_cost``, which never prices a model below ``guardPerMTok``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from .config import ConfigSource, default_source

PRICES_KEY = "config/prices.yaml"


@dataclass(frozen=True)
class ModelPrice:
    model_id: str
    input_per_mtok: float
    output_per_mtok: float
    source: str


@dataclass(frozen=True)
class ServicePrice:
    service: str
    unit: str
    usd: float
    source: str
    min_units_per_request: int = 0


@dataclass(frozen=True)
class Prices:
    as_of: str
    models: dict[str, ModelPrice]
    services: dict[str, ServicePrice]
    guard: dict[str, tuple[float, float]]
    sizes: dict[str, dict[str, Any]]

    @classmethod
    def parse(cls, text: str) -> Prices:
        raw: dict[str, Any] = yaml.safe_load(text) or {}
        models = {mid: ModelPrice(mid, float(m["inputPerMTok"]), float(m["outputPerMTok"]), str(m["source"]))
                  for mid, m in (raw.get("models") or {}).items()}
        services = {name: ServicePrice(name, str(s["unit"]), float(s["usd"]), str(s["source"]),
                                       int(s.get("minUnitsPerRequest") or 0))
                    for name, s in (raw.get("services") or {}).items()}
        guard = {mid: (float(g["input"]), float(g["output"])) for mid, g in (raw.get("guardPerMTok") or {}).items()}
        return cls(as_of=str(raw.get("asOf") or ""), models=models, services=services, guard=guard,
                   sizes=dict(raw.get("sizes") or {}))

    def model(self, model_id: str) -> ModelPrice:
        if model_id not in self.models:
            raise KeyError(f"no list price for model {model_id!r} in {PRICES_KEY}")
        return self.models[model_id]

    def model_cost(self, model_id: str, usage: dict[str, int]) -> float:
        """List-price cost of one call's (or one run's) reported token usage."""
        p = self.model(model_id)
        return (usage.get("inputTokens", 0) * p.input_per_mtok + usage.get("outputTokens", 0) * p.output_per_mtok) / 1e6

    def guard_cost(self, model_id: str, usage: dict[str, int]) -> float:
        """The cost a budget guard counts: the list price, or ``guardPerMTok`` when that is higher."""
        p = self.model(model_id)
        gin, gout = self.guard.get(model_id, (0.0, 0.0))
        pin, pout = max(p.input_per_mtok, gin), max(p.output_per_mtok, gout)
        return (usage.get("inputTokens", 0) * pin + usage.get("outputTokens", 0) * pout) / 1e6


def load_prices(source: ConfigSource | None = None) -> Prices:
    text, _ = (source or default_source()).read_text(PRICES_KEY)
    return Prices.parse(text)
