"""Configured journald collector bindings for Sentinel-X."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from sentinel_x.systemd.boot import SystemBootIdReader
from sentinel_x.systemd.journal_checkpoint import SystemdJournalCheckpointStore
from sentinel_x.systemd.journal_observation import (
    StatefulSystemdJournalCollector,
    SystemdJournalBatchReader,
    SystemdJournalCollectorError,
    read_current_boot_id,
)
from sentinel_x.systemd.journal_reader import JournalctlServiceReader


class SystemdJournalCollectorBindingError(SystemdJournalCollectorError):
    """Raised when configured journald collector bindings are invalid."""


class ConfiguredSystemdJournalCollectors:
    """Trusted stateful journald collectors built from typed config bindings."""

    def __init__(
        self,
        bindings: Iterable[tuple[str, str, int]],
        *,
        reader: SystemdJournalBatchReader | None = None,
        boot_id_reader: SystemBootIdReader = read_current_boot_id,
        checkpoint_store: SystemdJournalCheckpointStore | None = None,
    ) -> None:
        normalized = tuple(bindings)
        if not normalized:
            self._collectors: tuple[StatefulSystemdJournalCollector, ...] = ()
            return

        if not callable(boot_id_reader):
            raise TypeError("boot_id_reader must be callable")

        shared_reader: SystemdJournalBatchReader = (
            JournalctlServiceReader() if reader is None else reader
        )
        collector_names: list[str] = []
        unit_names: list[str] = []
        collectors: list[StatefulSystemdJournalCollector] = []

        for binding in normalized:
            if not isinstance(binding, tuple) or len(binding) != 3:
                raise SystemdJournalCollectorBindingError(
                    "systemd journal bindings must be "
                    "(collector_name, unit_name, max_entries) tuples"
                )
            collector_name, unit_name, max_entries = binding
            try:
                collector = StatefulSystemdJournalCollector(
                    collector_name,
                    unit_name,
                    reader=shared_reader,
                    boot_id_reader=boot_id_reader,
                    max_entries=max_entries,
                    checkpoint_store=checkpoint_store,
                )
            except (TypeError, ValueError, SystemdJournalCollectorError) as exc:
                raise SystemdJournalCollectorBindingError(str(exc)) from exc

            collector_names.append(collector.name)
            unit_names.append(collector.unit_name)
            collectors.append(collector)

        if len(set(collector_names)) != len(collector_names):
            raise SystemdJournalCollectorBindingError(
                "systemd journal bindings contain duplicate collector names"
            )
        if len(set(unit_names)) != len(unit_names):
            raise SystemdJournalCollectorBindingError(
                "systemd journal bindings contain duplicate unit names"
            )

        self._collectors = tuple(collectors)

    def collectors(self) -> tuple[StatefulSystemdJournalCollector, ...]:
        """Return configured collectors in declared binding order."""

        return self._collectors

    def handlers(self) -> Mapping[str, Callable[[], object]]:
        """Return trusted handlers consumed by the core registry."""

        return {collector.name: collector.collect for collector in self._collectors}
