"""services/agent manages its own import path: ``rp_agent`` and ``main`` import from here."""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent
for path in (AGENT_DIR, AGENT_DIR / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
