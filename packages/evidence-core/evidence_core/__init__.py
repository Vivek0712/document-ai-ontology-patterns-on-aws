"""Shared contracts for Provenant (import name kept: evidence_core).

Every Lambda, the Strands agent, the local pipeline and the eval harness import
this package. It is owned by the lead architect; builders propose changes rather
than edit it.
"""

__version__ = "1.0.0"

PIPELINE_VERSIONS: tuple[str, ...] = ("1.0", "2.3.1")
"""Pipeline versions shipped in v1 of the product, in display order."""
