"""Conservative propagation-evidence synthesis for Sentinel-X Phase 5E.1.

This module combines already-typed Phase 5D evidence for one *exact* propagation
candidate context.  It does not estimate probability, assign confidence, infer
root cause, or generalize controlled laboratory observations beyond the evidence
context that produced them.

The synthesis deliberately preserves distinct evidence signals instead of
collapsing them into a scalar score.  In particular, affected-anomaly
observations, forward temporal consistency, directional counterevidence,
bounded negative observations, and insufficient evidence remain separately
counted and provenance-addressable.  Conflicting evidence is surfaced rather
than resolved by precedence or weighting.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Iterable, TypeAlias

from sentinel_x.dependency.coverage import (
    PropagationCoverageEvidence,
    PropagationCoverageInterpretation,
)
from sentinel_x.dependency.propagation import (
    DependencyPropagationCandidate,
    PairwiseFaultPropagationEvidence,
    PairwiseTemporalInterpretation,
)
from sentinel_x.dependency.propagation_experiment import (
    ControlledPropagationExperimentRecord,
)

PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION: Final[str] = (
    "sentinel-x.propagation-evidence-synthesis.v1"
)

DEFAULT_MAX_SYNTHESIS_INPUT_EVIDENCE: Final[int] = 256
MAX_SYNTHESIS_INPUT_EVIDENCE: Final[int] = 4096

_SYNTHESIS_ID_DOMAIN: Final[bytes] = b"sentinel-x.propagation-evidence-synthesis.v1\x00"
_CONTRIBUTION_ID_DOMAIN: Final[bytes] = (
    b"sentinel-x.propagation-evidence-contribution.v1\x00"
)


class PropagationEvidenceSynthesisError(RuntimeError):
    """Base error for Phase 5E.1 conservative evidence synthesis."""


class PropagationEvidenceSynthesisContractError(PropagationEvidenceSynthesisError):
    """Raised when typed evidence cannot be safely synthesized together."""


class PropagationEvidenceSynthesisCapacityError(PropagationEvidenceSynthesisError):
    """Raised when bounded synthesis input capacity is exceeded."""


class PropagationEvidenceContext(StrEnum):
    """Acquisition context for one normalized evidence contribution."""

    OBSERVATIONAL = "observational"
    CONTROLLED_INTERVENTIONAL = "controlled_interventional"


class PropagationEvidenceSynthesisScope(StrEnum):
    """Comparability scope within which evidence may be synthesized."""

    EMPTY_CANDIDATE_CONTEXT = "empty_candidate_context"
    OBSERVATIONAL_EPISODE = "observational_episode"
    CONTROLLED_EXPERIMENT = "controlled_experiment"


class PropagationEvidenceModality(StrEnum):
    """Evidence modality retained through synthesis."""

    PAIRWISE_TEMPORAL = "pairwise_temporal"
    SAMPLING_COVERAGE = "sampling_coverage"


class PropagationEvidenceSignal(StrEnum):
    """Non-causal signal represented by one evidence contribution."""

    AFFECTED_ANOMALY_OBSERVED = "affected_anomaly_observed"
    FORWARD_TEMPORAL_CONSISTENCY = "forward_temporal_consistency"
    DIRECTIONAL_COUNTEREVIDENCE = "directional_counterevidence"
    BOUNDED_NEGATIVE_OBSERVATION = "bounded_negative_observation"
    INSUFFICIENT = "insufficient"


class PropagationEvidenceSynthesisInterpretation(StrEnum):
    """Aggregate evidence profile without scoring, probability, or causality."""

    NO_EVIDENCE = "no_evidence"
    INSUFFICIENT_ONLY = "insufficient_only"
    SUPPORTIVE_WITHOUT_COUNTER_OR_NEGATIVE = "supportive_without_counter_or_negative"
    COUNTEREVIDENCE_WITHOUT_SUPPORT_OR_NEGATIVE = (
        "counterevidence_without_support_or_negative"
    )
    BOUNDED_NEGATIVE_WITHOUT_SUPPORT_OR_COUNTER = (
        "bounded_negative_without_support_or_counter"
    )
    COUNTER_AND_BOUNDED_NEGATIVE_WITHOUT_SUPPORT = (
        "counter_and_bounded_negative_without_support"
    )
    CONFLICTING_EVIDENCE = "conflicting_evidence"


PropagationEvidenceInput: TypeAlias = (
    PairwiseFaultPropagationEvidence
    | PropagationCoverageEvidence
    | ControlledPropagationExperimentRecord
)


@dataclass(frozen=True, slots=True)
class PropagationEvidenceContribution:
    """One normalized, provenance-addressable Phase 5D evidence contribution."""

    contribution_id: str
    context: PropagationEvidenceContext
    modality: PropagationEvidenceModality
    signal: PropagationEvidenceSignal
    source_evidence_id: str
    source_record_id: str | None

    def __post_init__(self) -> None:
        if not _is_prefixed_digest(self.contribution_id, "propsig-"):
            raise PropagationEvidenceSynthesisContractError(
                "contribution_id must be a propsig- prefixed SHA-256 identity"
            )
        if not isinstance(self.context, PropagationEvidenceContext):
            raise PropagationEvidenceSynthesisContractError("context must be typed")
        if not isinstance(self.modality, PropagationEvidenceModality):
            raise PropagationEvidenceSynthesisContractError("modality must be typed")
        if not isinstance(self.signal, PropagationEvidenceSignal):
            raise PropagationEvidenceSynthesisContractError("signal must be typed")
        _validate_text(self.source_evidence_id, field_name="source_evidence_id")
        if self.source_record_id is not None:
            _validate_text(self.source_record_id, field_name="source_record_id")
        if (
            self.context is PropagationEvidenceContext.OBSERVATIONAL
            and self.source_record_id is not None
        ):
            raise PropagationEvidenceSynthesisContractError(
                "observational contributions cannot claim a controlled source record"
            )
        if (
            self.context is PropagationEvidenceContext.CONTROLLED_INTERVENTIONAL
            and self.source_record_id is None
        ):
            raise PropagationEvidenceSynthesisContractError(
                "controlled contributions require source_record_id provenance"
            )
        expected_id = _contribution_id(
            context=self.context,
            modality=self.modality,
            signal=self.signal,
            source_evidence_id=self.source_evidence_id,
            source_record_id=self.source_record_id,
        )
        if self.contribution_id != expected_id:
            raise PropagationEvidenceSynthesisContractError(
                "contribution identity does not match normalized evidence content"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION,
            "contribution_id": self.contribution_id,
            "context": self.context.value,
            "modality": self.modality.value,
            "signal": self.signal.value,
            "source_evidence_id": self.source_evidence_id,
            "source_record_id": self.source_record_id,
            "causal_claim": False,
            "propagation_claim_assigned": False,
            "root_cause_claim_assigned": False,
            "probabilistic_confidence_assigned": False,
            "evidence_independence_assumed": False,
        }


@dataclass(frozen=True, slots=True)
class PropagationEvidenceSynthesis:
    """Bounded synthesis of typed evidence for one exact candidate context."""

    synthesis_id: str
    candidate: DependencyPropagationCandidate
    max_input_evidence: int
    input_evidence: tuple[PropagationEvidenceInput, ...]
    contributions: tuple[PropagationEvidenceContribution, ...]

    def __post_init__(self) -> None:
        if not _is_prefixed_digest(self.synthesis_id, "propsyn-"):
            raise PropagationEvidenceSynthesisContractError(
                "synthesis_id must be a propsyn- prefixed SHA-256 identity"
            )
        if not isinstance(self.candidate, DependencyPropagationCandidate):
            raise PropagationEvidenceSynthesisContractError(
                "candidate must be a DependencyPropagationCandidate"
            )
        _validate_capacity(self.max_input_evidence)
        if not isinstance(self.input_evidence, tuple):
            raise PropagationEvidenceSynthesisContractError(
                "input_evidence must be a tuple"
            )
        if len(self.input_evidence) > self.max_input_evidence:
            raise PropagationEvidenceSynthesisContractError(
                "input evidence count exceeds max_input_evidence"
            )

        input_ids: list[str] = []
        expected_contributions: list[PropagationEvidenceContribution] = []
        seen_input_ids: set[str] = set()
        seen_source_evidence_ids: set[str] = set()
        for item in self.input_evidence:
            item_candidate = _candidate_for_input(item)
            if item_candidate != self.candidate:
                raise PropagationEvidenceSynthesisContractError(
                    "all evidence must use the exact candidate context being synthesized"
                )
            input_id = _input_identity(item)
            if input_id in seen_input_ids:
                raise PropagationEvidenceSynthesisContractError(
                    "duplicate input evidence identity is not allowed"
                )
            seen_input_ids.add(input_id)
            input_ids.append(input_id)
            for contribution in _normalize_input(item):
                if contribution.source_evidence_id in seen_source_evidence_ids:
                    raise PropagationEvidenceSynthesisContractError(
                        "source evidence cannot be supplied through multiple synthesis inputs"
                    )
                seen_source_evidence_ids.add(contribution.source_evidence_id)
                expected_contributions.append(contribution)

        canonical_input_ids = tuple(sorted(input_ids))
        if tuple(input_ids) != canonical_input_ids:
            raise PropagationEvidenceSynthesisContractError(
                "input_evidence must be canonically sorted by evidence identity"
            )
        _validate_synthesis_scope(self.input_evidence)
        if not isinstance(self.contributions, tuple):
            raise PropagationEvidenceSynthesisContractError(
                "contributions must be a tuple"
            )
        canonical_expected_contributions = tuple(
            sorted(
                expected_contributions,
                key=lambda contribution: contribution.contribution_id,
            )
        )
        if self.contributions != canonical_expected_contributions:
            raise PropagationEvidenceSynthesisContractError(
                "contributions must exactly match normalized typed input evidence"
            )
        expected_id = _synthesis_id(
            candidate=self.candidate,
            max_input_evidence=self.max_input_evidence,
            input_evidence_ids=canonical_input_ids,
            contributions=self.contributions,
        )
        if self.synthesis_id != expected_id:
            raise PropagationEvidenceSynthesisContractError(
                "synthesis identity does not match candidate and evidence content"
            )

    @property
    def scope(self) -> PropagationEvidenceSynthesisScope:
        return _synthesis_scope(self.input_evidence)[0]

    @property
    def source_context_id(self) -> str | None:
        return _synthesis_scope(self.input_evidence)[1]

    @property
    def analysis_window_usec(self) -> int | None:
        return _synthesis_scope(self.input_evidence)[2]

    @property
    def input_evidence_ids(self) -> tuple[str, ...]:
        return tuple(_input_identity(item) for item in self.input_evidence)

    @property
    def input_evidence_count(self) -> int:
        return len(self.input_evidence)

    @property
    def contribution_count(self) -> int:
        return len(self.contributions)

    @property
    def affected_anomaly_observed_count(self) -> int:
        return self._count_signal(PropagationEvidenceSignal.AFFECTED_ANOMALY_OBSERVED)

    @property
    def forward_temporal_consistency_count(self) -> int:
        return self._count_signal(
            PropagationEvidenceSignal.FORWARD_TEMPORAL_CONSISTENCY
        )

    @property
    def directional_counterevidence_count(self) -> int:
        return self._count_signal(PropagationEvidenceSignal.DIRECTIONAL_COUNTEREVIDENCE)

    @property
    def bounded_negative_observation_count(self) -> int:
        return self._count_signal(
            PropagationEvidenceSignal.BOUNDED_NEGATIVE_OBSERVATION
        )

    @property
    def insufficient_evidence_count(self) -> int:
        return self._count_signal(PropagationEvidenceSignal.INSUFFICIENT)

    @property
    def supportive_signal_count(self) -> int:
        return (
            self.affected_anomaly_observed_count
            + self.forward_temporal_consistency_count
        )

    @property
    def observational_contribution_count(self) -> int:
        return sum(
            contribution.context is PropagationEvidenceContext.OBSERVATIONAL
            for contribution in self.contributions
        )

    @property
    def controlled_contribution_count(self) -> int:
        return sum(
            contribution.context is PropagationEvidenceContext.CONTROLLED_INTERVENTIONAL
            for contribution in self.contributions
        )

    @property
    def conflicting_evidence_present(self) -> bool:
        return self.supportive_signal_count > 0 and (
            self.directional_counterevidence_count > 0
            or self.bounded_negative_observation_count > 0
        )

    @property
    def interpretation(self) -> PropagationEvidenceSynthesisInterpretation:
        supportive = self.supportive_signal_count > 0
        counter = self.directional_counterevidence_count > 0
        negative = self.bounded_negative_observation_count > 0
        if self.contribution_count == 0:
            return PropagationEvidenceSynthesisInterpretation.NO_EVIDENCE
        if not supportive and not counter and not negative:
            return PropagationEvidenceSynthesisInterpretation.INSUFFICIENT_ONLY
        if supportive and (counter or negative):
            return PropagationEvidenceSynthesisInterpretation.CONFLICTING_EVIDENCE
        if supportive:
            return PropagationEvidenceSynthesisInterpretation.SUPPORTIVE_WITHOUT_COUNTER_OR_NEGATIVE
        if counter and negative:
            return PropagationEvidenceSynthesisInterpretation.COUNTER_AND_BOUNDED_NEGATIVE_WITHOUT_SUPPORT
        if counter:
            return PropagationEvidenceSynthesisInterpretation.COUNTEREVIDENCE_WITHOUT_SUPPORT_OR_NEGATIVE
        return PropagationEvidenceSynthesisInterpretation.BOUNDED_NEGATIVE_WITHOUT_SUPPORT_OR_COUNTER

    def _count_signal(self, signal: PropagationEvidenceSignal) -> int:
        return sum(contribution.signal is signal for contribution in self.contributions)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": PROPAGATION_EVIDENCE_SYNTHESIS_SCHEMA_VERSION,
            "synthesis_id": self.synthesis_id,
            "candidate": self.candidate.to_dict(),
            "scope": self.scope.value,
            "source_context_id": self.source_context_id,
            "analysis_window_usec": self.analysis_window_usec,
            "input_evidence_count": self.input_evidence_count,
            "max_input_evidence": self.max_input_evidence,
            "input_evidence_ids": list(self.input_evidence_ids),
            "contribution_count": self.contribution_count,
            "contributions": [
                contribution.to_dict() for contribution in self.contributions
            ],
            "affected_anomaly_observed_count": self.affected_anomaly_observed_count,
            "forward_temporal_consistency_count": (
                self.forward_temporal_consistency_count
            ),
            "directional_counterevidence_count": (
                self.directional_counterevidence_count
            ),
            "bounded_negative_observation_count": (
                self.bounded_negative_observation_count
            ),
            "insufficient_evidence_count": self.insufficient_evidence_count,
            "supportive_signal_count": self.supportive_signal_count,
            "observational_contribution_count": self.observational_contribution_count,
            "controlled_contribution_count": self.controlled_contribution_count,
            "interpretation": self.interpretation.value,
            "conflicting_evidence_present": self.conflicting_evidence_present,
            "candidate_context_exact_match_required": True,
            "topology_temporal_applicability_claim": False,
            "generalization_beyond_evidence_context_permitted": False,
            "evidence_independence_assumed": False,
            "scalar_score_assigned": False,
            "causal_claim": False,
            "propagation_claim_assigned": False,
            "root_cause_claim_assigned": False,
            "universal_systemd_behavior_claim": False,
            "probabilistic_confidence_assigned": False,
        }


def synthesize_propagation_evidence(
    candidate: DependencyPropagationCandidate,
    evidence: Iterable[PropagationEvidenceInput],
    *,
    max_input_evidence: int = DEFAULT_MAX_SYNTHESIS_INPUT_EVIDENCE,
) -> PropagationEvidenceSynthesis:
    """Normalize and synthesize bounded evidence for one exact candidate context."""

    if not isinstance(candidate, DependencyPropagationCandidate):
        raise PropagationEvidenceSynthesisContractError(
            "candidate must be a DependencyPropagationCandidate"
        )
    _validate_capacity(max_input_evidence)
    _reject_text_iterable(evidence)
    items = _materialize_bounded_evidence(
        evidence,
        max_input_evidence=max_input_evidence,
    )

    input_ids: list[str] = []
    contributions: list[PropagationEvidenceContribution] = []
    seen_input_ids: set[str] = set()
    seen_source_evidence_ids: set[str] = set()

    for item in items:
        item_candidate = _candidate_for_input(item)
        if item_candidate != candidate:
            raise PropagationEvidenceSynthesisContractError(
                "all evidence must use the exact candidate context being synthesized"
            )
        input_id = _input_identity(item)
        if input_id in seen_input_ids:
            raise PropagationEvidenceSynthesisContractError(
                "duplicate input evidence identity is not allowed"
            )
        seen_input_ids.add(input_id)
        input_ids.append(input_id)

        for contribution in _normalize_input(item):
            if contribution.source_evidence_id in seen_source_evidence_ids:
                raise PropagationEvidenceSynthesisContractError(
                    "source evidence cannot be supplied through multiple synthesis inputs"
                )
            seen_source_evidence_ids.add(contribution.source_evidence_id)
            contributions.append(contribution)

    canonical_items = tuple(sorted(items, key=_input_identity))
    canonical_input_ids = tuple(_input_identity(item) for item in canonical_items)
    canonical_contributions = tuple(
        sorted(contributions, key=lambda contribution: contribution.contribution_id)
    )
    synthesis_id = _synthesis_id(
        candidate=candidate,
        max_input_evidence=max_input_evidence,
        input_evidence_ids=canonical_input_ids,
        contributions=canonical_contributions,
    )
    return PropagationEvidenceSynthesis(
        synthesis_id=synthesis_id,
        candidate=candidate,
        max_input_evidence=max_input_evidence,
        input_evidence=canonical_items,
        contributions=canonical_contributions,
    )


def _validate_synthesis_scope(
    items: tuple[PropagationEvidenceInput, ...],
) -> None:
    _synthesis_scope(items)


def _synthesis_scope(
    items: tuple[PropagationEvidenceInput, ...],
) -> tuple[PropagationEvidenceSynthesisScope, str | None, int | None]:
    if not items:
        return (
            PropagationEvidenceSynthesisScope.EMPTY_CANDIDATE_CONTEXT,
            None,
            None,
        )
    controlled = tuple(
        item
        for item in items
        if isinstance(item, ControlledPropagationExperimentRecord)
    )
    if controlled:
        if len(items) != 1:
            raise PropagationEvidenceSynthesisContractError(
                "controlled experiment synthesis cannot mix records or observational episodes"
            )
        record = controlled[0]
        return (
            PropagationEvidenceSynthesisScope.CONTROLLED_EXPERIMENT,
            record.record_id,
            record.controlled_coverage_evidence.analysis_window_usec,
        )

    source_ids: set[str] = set()
    windows: set[int] = set()
    for item in items:
        if isinstance(item, PairwiseFaultPropagationEvidence):
            source_ids.add(item.source_incident.evidence_id)
            windows.add(item.analysis_window_usec)
            continue
        if isinstance(item, PropagationCoverageEvidence):
            source_ids.add(item.source_incident.evidence_id)
            windows.add(item.analysis_window_usec)
            continue
        raise PropagationEvidenceSynthesisContractError(
            "observational synthesis accepts only pairwise or sampling coverage evidence"
        )
    if len(source_ids) != 1:
        raise PropagationEvidenceSynthesisContractError(
            "observational synthesis requires one exact source incident anchor"
        )
    if len(windows) != 1:
        raise PropagationEvidenceSynthesisContractError(
            "observational synthesis requires one exact analysis window"
        )
    return (
        PropagationEvidenceSynthesisScope.OBSERVATIONAL_EPISODE,
        next(iter(source_ids)),
        next(iter(windows)),
    )


def _normalize_input(
    item: object,
) -> tuple[PropagationEvidenceContribution, ...]:
    if isinstance(item, PairwiseFaultPropagationEvidence):
        return (
            _make_contribution(
                context=PropagationEvidenceContext.OBSERVATIONAL,
                modality=PropagationEvidenceModality.PAIRWISE_TEMPORAL,
                signal=_pairwise_signal(item),
                source_evidence_id=item.evidence_id,
                source_record_id=None,
            ),
        )
    if isinstance(item, PropagationCoverageEvidence):
        return (
            _make_contribution(
                context=PropagationEvidenceContext.OBSERVATIONAL,
                modality=PropagationEvidenceModality.SAMPLING_COVERAGE,
                signal=_coverage_signal(item.interpretation),
                source_evidence_id=item.evidence_id,
                source_record_id=None,
            ),
        )
    if isinstance(item, ControlledPropagationExperimentRecord):
        contributions = [
            _make_contribution(
                context=PropagationEvidenceContext.CONTROLLED_INTERVENTIONAL,
                modality=PropagationEvidenceModality.SAMPLING_COVERAGE,
                signal=_coverage_signal(
                    item.controlled_coverage_evidence.interpretation
                ),
                source_evidence_id=item.controlled_coverage_evidence.evidence_id,
                source_record_id=item.record_id,
            )
        ]
        if item.pairwise_evidence is not None:
            contributions.append(
                _make_contribution(
                    context=PropagationEvidenceContext.CONTROLLED_INTERVENTIONAL,
                    modality=PropagationEvidenceModality.PAIRWISE_TEMPORAL,
                    signal=_pairwise_signal(item.pairwise_evidence),
                    source_evidence_id=item.pairwise_evidence.evidence_id,
                    source_record_id=item.record_id,
                )
            )
        return tuple(contributions)
    raise PropagationEvidenceSynthesisContractError(
        "synthesis input must be typed Phase 5D propagation evidence"
    )


def _pairwise_signal(
    evidence: PairwiseFaultPropagationEvidence,
) -> PropagationEvidenceSignal:
    if (
        evidence.interpretation
        is PairwiseTemporalInterpretation.CONSISTENT_WITH_CANDIDATE_DIRECTION
    ):
        return PropagationEvidenceSignal.FORWARD_TEMPORAL_CONSISTENCY
    if (
        evidence.interpretation
        is PairwiseTemporalInterpretation.COUNTEREVIDENCE_TO_CANDIDATE_DIRECTION
    ):
        return PropagationEvidenceSignal.DIRECTIONAL_COUNTEREVIDENCE
    return PropagationEvidenceSignal.INSUFFICIENT


def _coverage_signal(
    interpretation: PropagationCoverageInterpretation,
) -> PropagationEvidenceSignal:
    if (
        interpretation
        is PropagationCoverageInterpretation.AFFECTED_ANOMALY_OBSERVED_IN_WINDOW
    ):
        return PropagationEvidenceSignal.AFFECTED_ANOMALY_OBSERVED
    if (
        interpretation
        is PropagationCoverageInterpretation.NEGATIVE_EVIDENCE_WITHIN_BOUNDED_SAMPLING
    ):
        return PropagationEvidenceSignal.BOUNDED_NEGATIVE_OBSERVATION
    return PropagationEvidenceSignal.INSUFFICIENT


def _candidate_for_input(item: object) -> DependencyPropagationCandidate:
    if isinstance(item, PairwiseFaultPropagationEvidence):
        return item.candidate
    if isinstance(item, PropagationCoverageEvidence):
        return item.candidate
    if isinstance(item, ControlledPropagationExperimentRecord):
        return item.candidate
    raise PropagationEvidenceSynthesisContractError(
        "synthesis input must be typed Phase 5D propagation evidence"
    )


def _input_identity(item: object) -> str:
    if isinstance(item, PairwiseFaultPropagationEvidence):
        return item.evidence_id
    if isinstance(item, PropagationCoverageEvidence):
        return item.evidence_id
    if isinstance(item, ControlledPropagationExperimentRecord):
        return item.record_id
    raise PropagationEvidenceSynthesisContractError(
        "synthesis input must be typed Phase 5D propagation evidence"
    )


def _make_contribution(
    *,
    context: PropagationEvidenceContext,
    modality: PropagationEvidenceModality,
    signal: PropagationEvidenceSignal,
    source_evidence_id: str,
    source_record_id: str | None,
) -> PropagationEvidenceContribution:
    contribution_id = _contribution_id(
        context=context,
        modality=modality,
        signal=signal,
        source_evidence_id=source_evidence_id,
        source_record_id=source_record_id,
    )
    return PropagationEvidenceContribution(
        contribution_id=contribution_id,
        context=context,
        modality=modality,
        signal=signal,
        source_evidence_id=source_evidence_id,
        source_record_id=source_record_id,
    )


def _contribution_id(
    *,
    context: PropagationEvidenceContext,
    modality: PropagationEvidenceModality,
    signal: PropagationEvidenceSignal,
    source_evidence_id: str,
    source_record_id: str | None,
) -> str:
    payload: dict[str, object] = {
        "context": context.value,
        "modality": modality.value,
        "signal": signal.value,
        "source_evidence_id": source_evidence_id,
        "source_record_id": source_record_id,
    }
    return "propsig-" + _content_digest(_CONTRIBUTION_ID_DOMAIN, payload)


def _synthesis_id(
    *,
    candidate: DependencyPropagationCandidate,
    max_input_evidence: int,
    input_evidence_ids: tuple[str, ...],
    contributions: tuple[PropagationEvidenceContribution, ...],
) -> str:
    payload: dict[str, object] = {
        "candidate": candidate.to_dict(),
        "max_input_evidence": max_input_evidence,
        "input_evidence_ids": list(input_evidence_ids),
        "contributions": [contribution.to_dict() for contribution in contributions],
    }
    return "propsyn-" + _content_digest(_SYNTHESIS_ID_DOMAIN, payload)


def _content_digest(domain: bytes, payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(domain + encoded).hexdigest()


def _materialize_bounded_evidence(
    evidence: Iterable[object],
    *,
    max_input_evidence: int,
) -> tuple[PropagationEvidenceInput, ...]:
    materialized: list[PropagationEvidenceInput] = []
    iterator = iter(evidence)
    for _ in range(max_input_evidence + 1):
        try:
            item = next(iterator)
        except StopIteration:
            return tuple(materialized)
        if not isinstance(
            item,
            (
                PairwiseFaultPropagationEvidence,
                PropagationCoverageEvidence,
                ControlledPropagationExperimentRecord,
            ),
        ):
            raise PropagationEvidenceSynthesisContractError(
                "synthesis input must be typed Phase 5D propagation evidence"
            )
        if len(materialized) == max_input_evidence:
            raise PropagationEvidenceSynthesisCapacityError(
                "synthesis input exceeds max_input_evidence"
            )
        materialized.append(item)
    raise AssertionError("bounded evidence materialization loop did not terminate")


def _reject_text_iterable(value: object) -> None:
    if isinstance(value, (str, bytes, bytearray)):
        raise PropagationEvidenceSynthesisContractError(
            "text and byte strings are not evidence iterables"
        )


def _validate_capacity(value: object) -> None:
    if type(value) is not int or not (1 <= value <= MAX_SYNTHESIS_INPUT_EVIDENCE):
        raise PropagationEvidenceSynthesisContractError(
            "max_input_evidence must be an integer within the synthesis bound"
        )


def _validate_text(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise PropagationEvidenceSynthesisContractError(
            f"{field_name} must be non-empty NUL-free text"
        )


def _is_prefixed_digest(value: object, prefix: str) -> bool:
    if not isinstance(value, str) or not value.startswith(prefix):
        return False
    digest = value[len(prefix) :]
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )
