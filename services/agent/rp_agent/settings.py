"""Agent settings (environment of the AgentCore Runtime, set by scripts/deploy_agentcore.sh)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from evidence_core.env import Settings

IdentityMode = Literal["interceptor", "agent_hook"]
AGENT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AgentSettings:
    common: Settings
    gateway_url: str | None
    identity_mode: IdentityMode
    golden_path: Path | None

    @classmethod
    def from_env(cls) -> AgentSettings:
        common = Settings.from_env()
        mode = os.environ.get("RP_IDENTITY_INJECTION", "interceptor")
        if mode not in ("interceptor", "agent_hook"):
            raise ValueError(f"RP_IDENTITY_INJECTION must be interceptor or agent_hook, not {mode!r}")
        golden = os.environ.get("RP_GOLDEN_PATH")
        defaults = [AGENT_DIR / "golden.jsonl", common.repo_root / "eval" / "golden.jsonl"]
        candidates = [Path(golden)] if golden else defaults
        return cls(
            common=common,
            gateway_url=common.gateway_url,
            identity_mode=mode,  # type: ignore[arg-type]
            golden_path=next((p for p in candidates if p.is_file()), None),
        )
