"""Hidden scorer-only Phase-5F case truth.

This module is deliberately not imported by ``sentinel_x._phase5f`` or the visible
projection module.  Evaluation exporters must never serialize this object into a
reasoner-visible evidence bundle.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._common import (
    MAX_GOLD_GROUP_CHARS,
    validate_bounded_text,
    validate_case_id,
    validate_evidence_ref,
)

_CLASSIFICATIONS = frozenset(
    {
        "EFFECT_OBSERVED",
        "BOUNDED_NEGATIVE",
        "COUNTEREVIDENCE",
        "INSUFFICIENT",
        "AMBIGUOUS",
    }
)
_CAUSAL_STRENGTHS = frozenset(
    {
        "none",
        "association",
        "hypothesis",
        "established_cause",
    }
)
_ORIGINS = frozenset({"empirical", "adversarial"})


@dataclass(frozen=True, slots=True)
class CaseGold:
    """Hidden scoring truth for exactly one opaque evaluation case."""

    case_id: str
    classification: str
    must_abstain: bool
    supporting_evidence_refs: tuple[str, ...]
    invalid_evidence_refs: tuple[str, ...]
    counterevidence_refs: tuple[str, ...]
    maximum_allowed_causal_strength: str
    origin: str
    source_run_group: str

    def __post_init__(self) -> None:
        validate_case_id(self.case_id)
        if self.classification not in _CLASSIFICATIONS:
            raise ValueError("classification is not a Phase-5F benchmark label")
        if type(self.must_abstain) is not bool:
            raise ValueError("must_abstain must be a boolean")

        supporting = _canonical_refs(
            self.supporting_evidence_refs,
            field_name="supporting_evidence_refs",
        )
        invalid = _canonical_refs(
            self.invalid_evidence_refs,
            field_name="invalid_evidence_refs",
        )
        counter = _canonical_refs(
            self.counterevidence_refs,
            field_name="counterevidence_refs",
        )
        if set(supporting) & set(invalid):
            raise ValueError(
                "supporting_evidence_refs and invalid_evidence_refs must be disjoint"
            )
        if set(counter) & set(invalid):
            raise ValueError(
                "counterevidence_refs and invalid_evidence_refs must be disjoint"
            )
        if self.maximum_allowed_causal_strength not in _CAUSAL_STRENGTHS:
            raise ValueError("maximum_allowed_causal_strength is invalid")
        if self.origin not in _ORIGINS:
            raise ValueError("origin must be empirical or adversarial")
        validate_bounded_text(
            self.source_run_group,
            field_name="source_run_group",
            max_chars=MAX_GOLD_GROUP_CHARS,
        )

        object.__setattr__(self, "supporting_evidence_refs", supporting)
        object.__setattr__(self, "invalid_evidence_refs", invalid)
        object.__setattr__(self, "counterevidence_refs", counter)

    def to_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "classification": self.classification,
            "must_abstain": self.must_abstain,
            "supporting_evidence_refs": list(self.supporting_evidence_refs),
            "invalid_evidence_refs": list(self.invalid_evidence_refs),
            "counterevidence_refs": list(self.counterevidence_refs),
            "maximum_allowed_causal_strength": (self.maximum_allowed_causal_strength),
            "origin": self.origin,
            "source_run_group": self.source_run_group,
        }


def _canonical_refs(values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    if not isinstance(values, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    refs = tuple(validate_evidence_ref(value) for value in values)
    if len(set(refs)) != len(refs):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(sorted(refs))
