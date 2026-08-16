"""Private Phase-5F falsification support.

The package exports only reasoner-visible evaluation helpers and deterministic benchmark
baselines. Hidden gold truth lives in ``sentinel_x._phase5f.gold`` and is intentionally
not re-exported.
"""

from __future__ import annotations

from .baselines import (
    run_b0_state_rule,
    run_b1_graph_time,
    run_b1s_current_synthesis,
)
from .visible import CaseSource, project_case_evidence, visible_evidence_refs

__all__ = [
    "CaseSource",
    "project_case_evidence",
    "run_b0_state_rule",
    "run_b1_graph_time",
    "run_b1s_current_synthesis",
    "visible_evidence_refs",
]
