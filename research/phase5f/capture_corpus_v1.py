"""Operator-only Phase-5F.3 live corpus capture harness.

This script is intentionally outside the production package.  It reuses the frozen
Phase-5E.4 protocol-bound runner for mutation/evidence and adds only bounded read-only
systemctl/journal instrumentation for RAW evaluation evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import platform
import subprocess
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from sentinel_x._phase5f.corpus import (
    ADVERSARIAL_CASE_PLAN,
    EMPIRICAL_EXECUTION_ORDER,
    PHASE5F_SIDECAR_SCHEMA_VERSION,
    CorpusCase,
    audit_corpus_v1,
    empirical_relation_for_case,
    write_corpus_v1,
)
from sentinel_x._phase5f.corpus_empirical import build_empirical_corpus_case
from sentinel_x._phase5f.corpus_transformations import derive_adversarial_case
from sentinel_x.dependency.models import DependencyRelation
from sentinel_x.dependency.propagation_experiment import (
    ControlledPropagationEvidenceClass,
    SystemdPropagationPairArtifact,
    SystemdPropagationPairSpec,
    build_systemd_propagation_pair,
    verify_installed_systemd_propagation_pair,
)
from sentinel_x.dependency.propagation_live import ControlledPropagationLivePolicy
from sentinel_x.dependency.protocol_execution import (
    ControlledPropagationProtocolBoundExecution,
    ControlledPropagationProtocolBoundRunner,
)
from sentinel_x.lab.fixture import SystemdLabFixtureSpec
from sentinel_x.lab.models import FaultExperimentManifest, FaultMode, FaultScenario
from sentinel_x.systemd.boot import read_current_boot_id

SYSTEMCTL = "/usr/bin/systemctl"
JOURNALCTL = "/usr/bin/journalctl"
INSTALL = "/usr/bin/install"
SOURCE_ID = "p5f3-corpus-source"
DEPENDENT_ID = "p5f3-corpus-dependent"
SIDECAR_INTERVAL_SECONDS = 0.10
SIDECAR_MAX_ROUNDS = 160
MAX_SYSTEMCTL_OUTPUT_BYTES = 32 * 1024
MAX_JOURNAL_OUTPUT_BYTES = 512 * 1024
JOURNAL_MAX_LINES = 256
POLICY = ControlledPropagationLivePolicy(
    max_sample_gap_usec=250_000,
    sample_interval_seconds=0.05,
    max_samples=64,
    capture_join_timeout_seconds=5.0,
)


def _run_cmd(
    args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed rc={result.returncode}: {args!r}\n"
            f"stdout={result.stdout[-2000:]}\nstderr={result.stderr[-2000:]}"
        )
    return result


def _pair(relation: DependencyRelation) -> SystemdPropagationPairArtifact:
    return build_systemd_propagation_pair(
        SystemdPropagationPairSpec(
            source=SystemdLabFixtureSpec(SOURCE_ID, runtime_max_seconds=120),
            dependent=SystemdLabFixtureSpec(DEPENDENT_ID, runtime_max_seconds=120),
            requirement_relation=relation,
        )
    )


def _manifest(
    artifact: SystemdPropagationPairArtifact, *, token: str
) -> FaultExperimentManifest:
    digest = hashlib.sha256(f"phase5f3:{token}".encode()).hexdigest()
    return FaultExperimentManifest(
        scenario=FaultScenario(
            scenario_id=f"p5f3-{digest[:16]}",
            description="Phase 5F.3 blind corpus controlled capture.",
            target_unit=artifact.source_unit,
            fault_mode=FaultMode.SERVICE_INACTIVE,
            baseline_timeout_seconds=5.0,
            fault_timeout_seconds=5.0,
            recovery_timeout_seconds=10.0,
            evidence_grace_seconds=1.0,
        ),
        experiment_id=f"exp-{digest[:32]}",
        created_at=datetime.now(timezone.utc),
    )


def _stop_pair(artifact: SystemdPropagationPairArtifact, *, check: bool) -> None:
    _run_cmd(
        [
            SYSTEMCTL,
            "--system",
            "--no-ask-password",
            "--no-pager",
            "stop",
            "--",
            artifact.dependent_unit,
            artifact.source_unit,
        ],
        check=check,
    )


def _install_pair(artifact: SystemdPropagationPairArtifact) -> None:
    _stop_pair(artifact, check=False)
    with tempfile.TemporaryDirectory(prefix="sentinel-x-p5f3-corpus-") as staging:
        source_path, dependent_path = artifact.write_private_copies(staging)
        _run_cmd(
            [
                INSTALL,
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                os.fspath(source_path),
                os.fspath(artifact.source_artifact.runtime_install_path),
            ]
        )
        _run_cmd(
            [
                INSTALL,
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                os.fspath(dependent_path),
                os.fspath(artifact.dependent_runtime_install_path),
            ]
        )
    _run_cmd(
        [SYSTEMCTL, "--system", "--no-ask-password", "--no-pager", "daemon-reload"]
    )
    _stop_pair(artifact, check=True)
    verify_installed_systemd_propagation_pair(artifact)


def _cleanup(artifact: SystemdPropagationPairArtifact) -> None:
    _stop_pair(artifact, check=False)
    for path in (
        artifact.dependent_runtime_install_path,
        artifact.source_artifact.runtime_install_path,
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    _run_cmd(
        [SYSTEMCTL, "--system", "--no-ask-password", "--no-pager", "daemon-reload"],
        check=False,
    )


def _systemctl_show(unit: str) -> tuple[int, int, str, str]:
    args = [
        SYSTEMCTL,
        "--system",
        "--no-ask-password",
        "--no-pager",
        "show",
        "--property=Id",
        "--property=LoadState",
        "--property=ActiveState",
        "--property=SubState",
        "--property=MainPID",
        "--property=ExecMainCode",
        "--property=ExecMainStatus",
        "--property=StateChangeTimestampMonotonic",
        "--",
        unit,
    ]
    started = time.monotonic_ns()
    result = _run_cmd(args, check=False)
    duration_usec = (time.monotonic_ns() - started) // 1_000
    stdout = result.stdout
    stderr = result.stderr
    if len(stdout.encode()) > MAX_SYSTEMCTL_OUTPUT_BYTES:
        raise RuntimeError("systemctl sidecar stdout exceeded bound")
    if len(stderr.encode()) > MAX_SYSTEMCTL_OUTPUT_BYTES:
        raise RuntimeError("systemctl sidecar stderr exceeded bound")
    return result.returncode, duration_usec, stdout, stderr


def _capture_journal(
    source_unit: str,
    dependent_unit: str,
    start_epoch: float,
    end_epoch: float,
) -> dict[str, object]:
    since = math.floor(start_epoch) - 1
    until = math.ceil(end_epoch) + 1
    args = [
        JOURNALCTL,
        "--system",
        "--no-pager",
        "--output=json",
        f"--since=@{since}",
        f"--until=@{until}",
        f"--lines={JOURNAL_MAX_LINES}",
        f"--unit={source_unit}",
        f"--unit={dependent_unit}",
    ]
    result = _run_cmd(args, check=False)
    if len(result.stdout.encode()) > MAX_JOURNAL_OUTPUT_BYTES:
        raise RuntimeError("journal sidecar stdout exceeded bound")
    if len(result.stderr.encode()) > MAX_SYSTEMCTL_OUTPUT_BYTES:
        raise RuntimeError("journal sidecar stderr exceeded bound")
    return {
        "command": args,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "max_lines": JOURNAL_MAX_LINES,
        "bounded": True,
    }


def _run_with_sidecar(
    artifact: SystemdPropagationPairArtifact, *, token: str
) -> tuple[ControlledPropagationProtocolBoundExecution, dict[str, object]]:
    stop = threading.Event()
    first_sample_ready = threading.Event()
    samples: list[dict[str, object]] = []
    errors: list[str] = []
    lock = threading.Lock()
    start_wall = datetime.now(timezone.utc)
    start_epoch = start_wall.timestamp()
    start_mono = time.monotonic_ns() // 1_000
    boot_id = read_current_boot_id()

    def sample_loop() -> None:
        try:
            for round_index in range(SIDECAR_MAX_ROUNDS):
                captured_at = datetime.now(timezone.utc).isoformat()
                captured_mono = time.monotonic_ns() // 1_000
                round_rows: list[dict[str, object]] = []
                for unit in (artifact.source_unit, artifact.dependent_unit):
                    returncode, duration_usec, stdout, stderr = _systemctl_show(unit)
                    row = {
                        "round": round_index,
                        "captured_at": captured_at,
                        "captured_monotonic_usec": captured_mono,
                        "unit": unit,
                        "returncode": returncode,
                        "duration_usec": duration_usec,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                    round_rows.append(row)
                    if returncode != 0:
                        errors.append(f"systemctl show failed for {unit}")
                with lock:
                    samples.extend(round_rows)
                first_sample_ready.set()
                if stop.wait(SIDECAR_INTERVAL_SECONDS):
                    return
            errors.append("sidecar sampling capacity exhausted")
        except BaseException as exc:
            errors.append(f"sidecar exception: {type(exc).__name__}: {exc}")
            first_sample_ready.set()

    thread = threading.Thread(
        target=sample_loop, name="phase5f3-readonly-sidecar", daemon=True
    )
    thread.start()
    if not first_sample_ready.wait(timeout=2.0):
        stop.set()
        thread.join(timeout=5.0)
        raise RuntimeError("sidecar did not produce a pre-run sample in time")
    if errors:
        stop.set()
        thread.join(timeout=5.0)
        raise RuntimeError("; ".join(errors))
    try:
        execution = ControlledPropagationProtocolBoundRunner().run(
            artifact,
            _manifest(artifact, token=token),
            POLICY,
        )
    finally:
        stop.set()
        thread.join(timeout=5.0)
    if thread.is_alive():
        raise RuntimeError("sidecar sampling thread did not stop")
    end_mono = time.monotonic_ns() // 1_000
    end_wall = datetime.now(timezone.utc)
    end_epoch = end_wall.timestamp()
    if errors:
        raise RuntimeError("; ".join(errors))

    journal = _capture_journal(
        artifact.source_unit,
        artifact.dependent_unit,
        start_epoch,
        end_epoch,
    )
    if journal["returncode"] != 0:
        raise RuntimeError("journal sidecar command failed")

    raw_capture = {
        "schema_version": PHASE5F_SIDECAR_SCHEMA_VERSION,
        "boot_id": boot_id,
        "source_unit": artifact.source_unit,
        "dependent_unit": artifact.dependent_unit,
        "started_at": start_wall.isoformat(),
        "ended_at": end_wall.isoformat(),
        "started_monotonic_usec": start_mono,
        "ended_monotonic_usec": end_mono,
        "interval_seconds": SIDECAR_INTERVAL_SECONDS,
        "max_rounds": SIDECAR_MAX_ROUNDS,
        "sidecar_error_count": 0,
        "systemctl_samples": samples,
        "journal_excerpt": journal,
    }
    return execution, raw_capture


def _run_without_sidecar(
    artifact: SystemdPropagationPairArtifact, *, token: str
) -> ControlledPropagationProtocolBoundExecution:
    return ControlledPropagationProtocolBoundRunner().run(
        artifact,
        _manifest(artifact, token=token),
        POLICY,
    )


def _coverage_summary(
    execution: ControlledPropagationProtocolBoundExecution,
) -> dict[str, object]:
    coverage = execution.live_run.experiment_record.controlled_coverage_evidence
    return {
        "evidence_class": execution.live_run.experiment_record.evidence_class.value,
        "sampling_coverage": coverage.sampling_coverage.value,
        "largest_observed_gap_usec": coverage.largest_observed_gap_usec,
        "max_sample_gap_usec": coverage.max_sample_gap_usec,
        "sample_count": len(execution.live_run.dependent_assessments),
        "post_recovery_verified": execution.live_run.post_recovery_verified,
        "boot_id": execution.attempt.boot_id,
    }


def _observer_pilot(
    requires_artifact: SystemdPropagationPairArtifact,
    wants_artifact: SystemdPropagationPairArtifact,
) -> dict[str, object]:
    results: dict[str, object] = {}
    common_boot: str | None = None
    for relation_name, artifact, expected in (
        (
            "requires",
            requires_artifact,
            ControlledPropagationEvidenceClass.AFFECTED_ANOMALY_OBSERVED.value,
        ),
        (
            "wants",
            wants_artifact,
            ControlledPropagationEvidenceClass.BOUNDED_NEGATIVE_EVIDENCE.value,
        ),
    ):
        _install_pair(artifact)
        without = _run_without_sidecar(artifact, token=f"pilot-{relation_name}-without")
        _install_pair(artifact)
        with_sidecar, raw = _run_with_sidecar(
            artifact, token=f"pilot-{relation_name}-with"
        )
        without_summary = _coverage_summary(without)
        with_summary = _coverage_summary(with_sidecar)
        if (
            without_summary["evidence_class"] != expected
            or with_summary["evidence_class"] != expected
        ):
            raise RuntimeError(
                "observer pilot changed preregistered qualitative evidence class"
            )
        if (
            without_summary["sampling_coverage"] != "bounded"
            or with_summary["sampling_coverage"] != "bounded"
        ):
            raise RuntimeError(
                "observer pilot requires bounded frozen-run sampling in both arms"
            )
        if (
            not without_summary["post_recovery_verified"]
            or not with_summary["post_recovery_verified"]
        ):
            raise RuntimeError("observer pilot requires verified recovery")
        if raw["sidecar_error_count"] != 0:
            raise RuntimeError("observer pilot sidecar contained errors")
        for boot in (
            without_summary["boot_id"],
            with_summary["boot_id"],
            raw["boot_id"],
        ):
            if common_boot is None:
                common_boot = str(boot)
            elif boot != common_boot:
                raise RuntimeError("observer pilot crossed boot boundary")
        sample_rows = raw["systemctl_samples"]
        if not isinstance(sample_rows, list):
            raise RuntimeError("observer pilot sidecar samples are not a list")
        results[relation_name] = {
            "without_sidecar": without_summary,
            "with_sidecar": with_summary,
            "sidecar_sample_rows": len(sample_rows),
        }
    return {
        "schema_version": "sentinel-x.phase5f-observer-pilot.v1",
        "material_observer_effect_detected": False,
        "qualitative_evidence_class_unchanged": True,
        "frozen_runner_sampling_remained_bounded": True,
        "single_boot": common_boot,
        "relations": results,
        "statistical_no-effect_claim": False,
    }


def _environment(boot_id: str) -> dict[str, object]:
    systemctl_version = _run_cmd([SYSTEMCTL, "--version"]).stdout.splitlines()[0]
    os_release = "unknown"
    path = Path("/etc/os-release")
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                os_release = line.split("=", 1)[1].strip().strip('"')
                break
    return {
        "boot_id": boot_id,
        "host_alias": "phase5f3-fedora-host-1",
        "kernel_release": platform.release(),
        "systemd_manager_version": systemctl_version,
        "os_pretty_name": os_release,
        "systemctl_path": SYSTEMCTL,
        "journalctl_path": JOURNALCTL,
        "capture_sidecar_schema": PHASE5F_SIDECAR_SCHEMA_VERSION,
    }


def _archive_directory(directory: Path) -> tuple[Path, str]:
    archive = directory.with_suffix(".zip")
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                handle.write(path, path.relative_to(directory.parent).as_posix())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    return archive, digest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="/tmp/sentinel-x-phase5f3-corpus-v1",
        help="new output directory; it must not already exist",
    )
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("FAIL: Phase 5F.3 corpus harness must run as root operator")
    for executable in (SYSTEMCTL, JOURNALCTL, INSTALL):
        if not Path(executable).is_file():
            raise SystemExit(f"FAIL: required executable missing: {executable}")

    requires_artifact = _pair(DependencyRelation.REQUIRES)
    wants_artifact = _pair(DependencyRelation.WANTS)
    if requires_artifact.source_unit != wants_artifact.source_unit:
        raise SystemExit("FAIL: controlled pair source drift")
    if requires_artifact.dependent_unit != wants_artifact.dependent_unit:
        raise SystemExit("FAIL: controlled pair dependent drift")
    if (
        requires_artifact.source_artifact.sha256
        != wants_artifact.source_artifact.sha256
    ):
        raise SystemExit("FAIL: controlled source fixture drift")

    target = Path(args.output)
    if target.exists() or target.with_suffix(".zip").exists():
        raise SystemExit("FAIL: output path/archive already exists")

    _cleanup(requires_artifact)
    try:
        pilot = _observer_pilot(requires_artifact, wants_artifact)
        pilot_boot = pilot["single_boot"]
        if not isinstance(pilot_boot, str):
            raise RuntimeError("observer pilot did not bind a boot")

        environment = _environment(pilot_boot)
        empirical_cases: list[CorpusCase] = []
        for case_id in EMPIRICAL_EXECUTION_ORDER:
            relation = empirical_relation_for_case(case_id)
            artifact = (
                requires_artifact
                if relation is DependencyRelation.REQUIRES
                else wants_artifact
            )
            _install_pair(artifact)
            execution, raw_capture = _run_with_sidecar(artifact, token=case_id)
            if (
                execution.attempt.boot_id != pilot_boot
                or raw_capture["boot_id"] != pilot_boot
            ):
                raise RuntimeError(
                    "campaign crossed boot boundary; discard and restart corpus-v1"
                )
            empirical_cases.append(
                build_empirical_corpus_case(
                    case_id=case_id,
                    execution=execution,
                    raw_capture=raw_capture,
                    environment=environment,
                )
            )

        empirical_by_id = {item.source.case_id: item for item in empirical_cases}
        adversarial_cases = [
            derive_adversarial_case(
                empirical_by_id[parent_case_id],
                case_id=case_id,
                scenario_family=family,
            )
            for case_id, family, parent_case_id in ADVERSARIAL_CASE_PLAN
        ]
        cases = [*empirical_cases, *adversarial_cases]
        summary = audit_corpus_v1(cases)
        write_corpus_v1(cases, target, observer_pilot=pilot)
        archive, digest = _archive_directory(target)

        print("PHASE5F3_CORPUS_CAPTURE_PASS")
        print(f"case_count={summary['case_count']}")
        print(f"empirical_case_count={summary['empirical_case_count']}")
        print(f"adversarial_case_count={summary['adversarial_case_count']}")
        print(f"hard_case_count={summary['hard_case_count']}")
        print(f"boot_id={pilot_boot}")
        print(f"corpus_dir={target}")
        print(f"corpus_archive={archive}")
        print(f"corpus_archive_sha256={digest}")
        print("scored_reasoner_outputs_present=False")
    finally:
        _cleanup(requires_artifact)


if __name__ == "__main__":
    main()
