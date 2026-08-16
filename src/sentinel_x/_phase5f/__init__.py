"""Private Phase-5F falsification support.

Only the reasoner-visible projection boundary is exported here.  Hidden gold truth
lives in ``sentinel_x._phase5f.gold`` and is intentionally not re-exported.
"""

from __future__ import annotations

from .visible import CaseSource, project_case_evidence, visible_evidence_refs

__all__ = [
    "CaseSource",
    "project_case_evidence",
    "visible_evidence_refs",
]
