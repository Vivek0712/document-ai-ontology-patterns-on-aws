"""Local logged runs of the v1 agent over the local pipeline's outputs (dev only; see ``harness.py``).

``rp_agent`` (services/agent) and the deploy script's tool definitions live outside ``services/`` on the
import path, so this package adds ``services/agent`` and the repo root (for ``eval``) once.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "services" / "agent", _REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
