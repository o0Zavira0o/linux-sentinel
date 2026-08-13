"""Bounded process-event projection for Sentinel-X."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from sentinel_x.observability.process import ProcessProbeFailure
from sentinel_x.observability.process_sampling import (
    ProcessObservation,
    ProcessSample,
    ProcessSampleStatus,
)


PROCESS_EVENT_PROJECTION_VERSION: Final[int] = 1
PROCESS_EVENT_MAX_SAMPLES: Final[int] = 48
PROCESS_EVENT_TOP_CPU_LIMIT: Final[int] = 16
PROCESS_EVENT_TOP_MEMORY_LIMIT: Final[int] = 12
PROCESS_EVENT_TOP_IO_LIMIT: Final[int] = 12
PROCESS_EVENT_MAX_DROPPED_FAILURES: Final[int] = 16


_STATUS_PRIORITY: Final[dict[ProcessSampleStatus, int]] = {
    ProcessSampleStatus.PID_REUSED: 0,
    ProcessSampleStatus.COUNTER_RESET: 1,
    ProcessSampleStatus.START_UNVERIFIED: 2,
    ProcessSampleStatus.EXIT_UNVERIFIED: 3,
    ProcessSampleStatus.STARTED: 4,
    ProcessSampleStatus.EXITED: 5,
    ProcessSampleStatus.SAMPLED: 6,
}


def build_process_event_attributes(
    observation: ProcessObservation,
) -> dict[str, object]:
    """Project a full process observation into a bounded event payload."""

    selected, exceptional_candidate_count = _select_samples(observation)
    exceptional_selected_count = sum(
        _has_exception_reason(reasons) for _, reasons in selected
    )
    start_failures = _project_failures(observation.start_dropped_failures)
    end_failures = _project_failures(observation.end_dropped_failures)

    return {
        "sample_started_at": observation.sample_started_at.isoformat(),
        "captured_at": observation.captured_at.isoformat(),
        "sample_interval_seconds": observation.sample_interval_seconds,
        "logical_cpu_count": observation.logical_cpu_count,
        "clock_ticks_per_second": observation.clock_ticks_per_second,
        "page_size_bytes": observation.page_size_bytes,
        "summary": _build_summary(observation),
        "projection": {
            "version": PROCESS_EVENT_PROJECTION_VERSION,
            "max_samples": PROCESS_EVENT_MAX_SAMPLES,
            "selected_sample_count": len(selected),
            "omitted_sample_count": len(observation.samples) - len(selected),
            "truncated": len(selected) < len(observation.samples),
            "exceptional_candidate_count": exceptional_candidate_count,
            "exceptional_selected_count": exceptional_selected_count,
            "exceptional_omitted_count": (
                exceptional_candidate_count - exceptional_selected_count
            ),
            "top_cpu_limit": PROCESS_EVENT_TOP_CPU_LIMIT,
            "top_memory_limit": PROCESS_EVENT_TOP_MEMORY_LIMIT,
            "top_io_limit": PROCESS_EVENT_TOP_IO_LIMIT,
            "max_dropped_failures_per_endpoint": (PROCESS_EVENT_MAX_DROPPED_FAILURES),
            "start_dropped_failures_included": len(start_failures),
            "start_dropped_failures_omitted": (
                len(observation.start_dropped_failures) - len(start_failures)
            ),
            "end_dropped_failures_included": len(end_failures),
            "end_dropped_failures_omitted": (
                len(observation.end_dropped_failures) - len(end_failures)
            ),
        },
        "samples": [
            _selected_sample_to_dict(sample, reasons) for sample, reasons in selected
        ],
        "start_dropped_failures": [failure.to_dict() for failure in start_failures],
        "end_dropped_failures": [failure.to_dict() for failure in end_failures],
    }


def _build_summary(observation: ProcessObservation) -> dict[str, object]:
    """Build the complete observation summary without serializing all samples."""

    return {
        "sample_count": len(observation.samples),
        "sampled_count": observation.sampled_count,
        "started_count": observation.started_count,
        "exited_count": observation.exited_count,
        "pid_reused_count": observation.pid_reused_count,
        "counter_reset_count": observation.counter_reset_count,
        "start_unverified_count": observation.start_unverified_count,
        "exit_unverified_count": observation.exit_unverified_count,
        "context_metrics_unavailable_count": (
            observation.context_metrics_unavailable_count
        ),
        "io_metrics_unavailable_count": observation.io_metrics_unavailable_count,
        "comm_changed_count": observation.comm_changed_count,
        "start_discovered_pid_count": observation.start_discovered_pid_count,
        "end_discovered_pid_count": observation.end_discovered_pid_count,
        "start_dropped_process_count": len(observation.start_dropped_failures),
        "end_dropped_process_count": len(observation.end_dropped_failures),
    }


def _select_samples(
    observation: ProcessObservation,
) -> tuple[list[tuple[ProcessSample, tuple[str, ...]]], int]:
    """Select bounded, deterministic process evidence for persistence."""

    selected: dict[str, tuple[ProcessSample, list[str]]] = {}
    exceptional = sorted(
        (sample for sample in observation.samples if _is_exceptional(sample)),
        key=_exception_sort_key,
    )

    for sample in exceptional:
        _add_selected_sample(
            selected,
            sample,
            _exception_reasons(sample),
        )

        if len(selected) >= PROCESS_EVENT_MAX_SAMPLES:
            break

    _add_ranked_samples(
        selected,
        _rank_cpu_samples(observation.samples),
        limit=PROCESS_EVENT_TOP_CPU_LIMIT,
        reason="top_cpu",
    )
    _add_ranked_samples(
        selected,
        _rank_memory_samples(observation.samples),
        limit=PROCESS_EVENT_TOP_MEMORY_LIMIT,
        reason="top_memory",
    )
    _add_ranked_samples(
        selected,
        _rank_io_samples(observation.samples),
        limit=PROCESS_EVENT_TOP_IO_LIMIT,
        reason="top_io",
    )

    return [(sample, tuple(reasons)) for sample, reasons in selected.values()], len(
        exceptional
    )


def _has_exception_reason(reasons: tuple[str, ...]) -> bool:
    """Return whether selection reasons include exceptional evidence."""

    return any(
        reason.startswith(("status:", "data_quality:", "identity:"))
        for reason in reasons
    )


def _add_ranked_samples(
    selected: dict[str, tuple[ProcessSample, list[str]]],
    samples: Iterable[ProcessSample],
    *,
    limit: int,
    reason: str,
) -> None:
    """Add one bounded ranked sample category to the projection."""

    for sample in list(samples)[:limit]:
        _add_selected_sample(selected, sample, (reason,))


def _add_selected_sample(
    selected: dict[str, tuple[ProcessSample, list[str]]],
    sample: ProcessSample,
    reasons: Iterable[str],
) -> None:
    """Add or annotate one sample while enforcing the global event bound."""

    identity_key = sample.identity.identity_key
    existing = selected.get(identity_key)

    if existing is not None:
        _, existing_reasons = existing

        for reason in reasons:
            if reason not in existing_reasons:
                existing_reasons.append(reason)

        return

    if len(selected) >= PROCESS_EVENT_MAX_SAMPLES:
        return

    selected[identity_key] = (sample, list(reasons))


def _is_exceptional(sample: ProcessSample) -> bool:
    """Return whether a sample carries lifecycle or data-quality evidence."""

    return (
        sample.status is not ProcessSampleStatus.SAMPLED
        or bool(sample.context_regressed_fields)
        or bool(sample.io_regressed_fields)
        or sample.comm_changed
        or sample.ppid_changed
        or sample.effective_uid_changed
        or sample.effective_gid_changed
    )


def _exception_reasons(sample: ProcessSample) -> tuple[str, ...]:
    """Return deterministic selection reasons for exceptional evidence."""

    reasons: list[str] = []

    if sample.status is not ProcessSampleStatus.SAMPLED:
        reasons.append(f"status:{sample.status.value}")

    if sample.context_regressed_fields:
        reasons.append("data_quality:context_regression")

    if sample.io_regressed_fields:
        reasons.append("data_quality:io_regression")

    if sample.comm_changed:
        reasons.append("identity:comm_changed")

    if sample.ppid_changed:
        reasons.append("identity:ppid_changed")

    if sample.effective_uid_changed:
        reasons.append("identity:effective_uid_changed")

    if sample.effective_gid_changed:
        reasons.append("identity:effective_gid_changed")

    return tuple(reasons)


def _exception_sort_key(sample: ProcessSample) -> tuple[int, int]:
    """Sort exceptional samples by severity class and PID."""

    return _STATUS_PRIORITY[sample.status], sample.identity.pid


def _rank_cpu_samples(samples: Iterable[ProcessSample]) -> list[ProcessSample]:
    """Rank sampled processes by single-core-equivalent CPU usage."""

    candidates = [sample for sample in samples if _has_metrics(sample)]

    return sorted(
        candidates,
        key=lambda sample: (
            -_cpu_percent(sample),
            sample.identity.pid,
        ),
    )


def _rank_memory_samples(
    samples: Iterable[ProcessSample],
) -> list[ProcessSample]:
    """Rank sampled processes by end-of-window resident memory evidence."""

    candidates = [sample for sample in samples if _has_metrics(sample)]

    return sorted(
        candidates,
        key=lambda sample: (
            -_resident_memory_bytes(sample),
            sample.identity.pid,
        ),
    )


def _rank_io_samples(samples: Iterable[ProcessSample]) -> list[ProcessSample]:
    """Rank sampled processes by storage read-plus-write throughput."""

    candidates = [
        sample
        for sample in samples
        if _has_metrics(sample)
        and sample.metrics is not None
        and sample.metrics.io is not None
    ]

    return sorted(
        candidates,
        key=lambda sample: (
            -_storage_io_bytes_per_second(sample),
            sample.identity.pid,
        ),
    )


def _has_metrics(sample: ProcessSample) -> bool:
    """Return whether one sample has valid derived metrics."""

    return sample.status is ProcessSampleStatus.SAMPLED and sample.metrics is not None


def _cpu_percent(sample: ProcessSample) -> float:
    """Return single-core-equivalent CPU percent for ranking."""

    assert sample.metrics is not None

    return sample.metrics.cpu.single_core_equivalent_percent


def _resident_memory_bytes(sample: ProcessSample) -> int:
    """Return the best available resident-memory value for ranking."""

    assert sample.metrics is not None

    memory = sample.metrics.memory

    if memory.resident_memory_bytes is not None:
        return memory.resident_memory_bytes

    if memory.vm_rss_kb is not None:
        return memory.vm_rss_kb * 1024

    return 0


def _storage_io_bytes_per_second(sample: ProcessSample) -> float:
    """Return sampled storage read-plus-write throughput for ranking."""

    assert sample.metrics is not None
    assert sample.metrics.io is not None

    io = sample.metrics.io

    return io.read_bytes_per_second + io.write_bytes_per_second


def _selected_sample_to_dict(
    sample: ProcessSample,
    reasons: tuple[str, ...],
) -> dict[str, object]:
    """Serialize one selected sample with its projection rationale."""

    payload = sample.to_dict()
    payload["selection_reasons"] = list(reasons)

    return payload


def _project_failures(
    failures: tuple[ProcessProbeFailure, ...],
) -> tuple[ProcessProbeFailure, ...]:
    """Return a bounded deterministic view of dropped-process failures."""

    return tuple(
        sorted(failures, key=lambda failure: failure.pid)[
            :PROCESS_EVENT_MAX_DROPPED_FAILURES
        ]
    )
