from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parents[1]
REPO = DATA_DIR.parent
if str(DATA_DIR) not in sys.path:
    sys.path.insert(0, str(DATA_DIR))

SAMPLES = DATA_DIR / "samples"


@pytest.fixture(scope="session")
def samples_dir() -> Path:
    return SAMPLES


@pytest.fixture(scope="session")
def golds() -> dict[str, dict]:
    return {p.parent.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(SAMPLES.glob("*/gold.json"))}


@pytest.fixture(scope="session")
def regenerated(tmp_path_factory) -> Path:
    """A fresh generator run into a temp dir (samples + golden set)."""
    import generate

    out = tmp_path_factory.mktemp("samples")
    generate.main(["--out", str(out), "--golden", str(out / "golden.jsonl")])
    return out
