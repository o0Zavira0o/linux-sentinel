from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from typing import cast

from sentinel_x.lab import (
    SYSTEMD_LAB_RUNTIME_ROOT,
    FaultLabFixtureError,
    SystemdLabFixtureArtifact,
    SystemdLabFixtureSpec,
    build_systemd_lab_fixture,
    validate_lab_fixture_id,
    validate_lab_fixture_unit_name,
)


class SystemdLabFixtureTests(unittest.TestCase):
    def test_fixture_id_accepts_conservative_shape(self) -> None:
        self.assertEqual(validate_lab_fixture_id("alpha-01"), "alpha-01")

    def test_fixture_id_rejects_unsafe_shapes(self) -> None:
        for value in ("ab", "Aaa", "alpha_01", "alpha.01", "alpha 01", "../alpha"):
            with self.subTest(value=value), self.assertRaises(FaultLabFixtureError):
                validate_lab_fixture_id(value)

    def test_spec_derives_fixed_namespace_unit_and_volatile_path(self) -> None:
        spec = SystemdLabFixtureSpec("alpha-01")

        self.assertEqual(spec.unit_name, "sentinel-x-lab-alpha-01.service")
        self.assertEqual(
            spec.runtime_install_path,
            Path("/run/systemd/system/sentinel-x-lab-alpha-01.service"),
        )
        self.assertEqual(spec.runtime_max_seconds, 180)

    def test_runtime_limit_is_bounded_and_rejects_boolean(self) -> None:
        for value in (True, 0, 301, -1):
            with self.subTest(value=value), self.assertRaises(FaultLabFixtureError):
                SystemdLabFixtureSpec("alpha-01", runtime_max_seconds=value)

        self.assertEqual(
            SystemdLabFixtureSpec(
                "alpha-01", runtime_max_seconds=300
            ).runtime_max_seconds,
            300,
        )

    def test_managed_unit_validator_rejects_non_lab_service(self) -> None:
        self.assertEqual(
            validate_lab_fixture_unit_name("sentinel-x-lab-alpha-01.service"),
            "sentinel-x-lab-alpha-01.service",
        )
        for value in (
            "sshd.service",
            "sentinel-x-alpha.service",
            "sentinel-x-lab-alpha.timer",
            "sentinel-x-lab-A.service",
        ):
            with self.subTest(value=value), self.assertRaises(FaultLabFixtureError):
                validate_lab_fixture_unit_name(value)

    def test_render_is_deterministic_for_same_spec(self) -> None:
        spec = SystemdLabFixtureSpec("alpha-01", runtime_max_seconds=90)

        first = build_systemd_lab_fixture(spec)
        second = build_systemd_lab_fixture(spec)

        self.assertEqual(first.unit_text, second.unit_text)
        self.assertEqual(first.sha256, second.sha256)

    def test_render_contains_only_fixed_execution_payloads(self) -> None:
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("alpha-01"))

        self.assertIn("ExecStart=/usr/bin/sleep infinity\n", artifact.unit_text)
        self.assertIn(
            "ExecStartPre=/usr/bin/echo SENTINEL_X_LAB_FIXTURE_READY "
            "fixture_id=alpha-01\n",
            artifact.unit_text,
        )
        self.assertIn(
            "ExecStopPost=/usr/bin/echo SENTINEL_X_LAB_FIXTURE_STOPPED "
            "fixture_id=alpha-01\n",
            artifact.unit_text,
        )
        self.assertNotIn("/bin/sh", artifact.unit_text)
        self.assertNotIn("bash", artifact.unit_text)

    def test_render_has_hardening_and_bounded_lifetime(self) -> None:
        artifact = build_systemd_lab_fixture(
            SystemdLabFixtureSpec("alpha-01", runtime_max_seconds=75)
        )
        required_lines = {
            "RuntimeMaxSec=75s",
            "DynamicUser=yes",
            "NoNewPrivileges=yes",
            "PrivateTmp=yes",
            "PrivateDevices=yes",
            "PrivateNetwork=yes",
            "ProtectSystem=strict",
            "ProtectHome=yes",
            "ProtectKernelTunables=yes",
            "ProtectKernelModules=yes",
            "ProtectControlGroups=yes",
            "RestrictSUIDSGID=yes",
            "RestrictNamespaces=yes",
            "LockPersonality=yes",
            "MemoryDenyWriteExecute=yes",
            "CapabilityBoundingSet=",
            "AmbientCapabilities=",
            "UMask=0077",
        }

        self.assertTrue(required_lines.issubset(set(artifact.unit_text.splitlines())))

    def test_render_intentionally_has_no_install_section(self) -> None:
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("alpha-01"))

        self.assertNotIn("[Install]", artifact.unit_text)
        self.assertNotIn("WantedBy=", artifact.unit_text)

    def test_artifact_hash_covers_exact_unit_text(self) -> None:
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("alpha-01"))

        expected = hashlib.sha256(artifact.unit_text.encode("utf-8")).hexdigest()
        self.assertEqual(artifact.sha256, expected)

    def test_artifact_rejects_hash_mismatch(self) -> None:
        spec = SystemdLabFixtureSpec("alpha-01")
        valid = build_systemd_lab_fixture(spec)

        with self.assertRaises(FaultLabFixtureError):
            SystemdLabFixtureArtifact(
                spec=spec,
                unit_text=valid.unit_text,
                sha256="0" * 64,
            )

    def test_artifact_rejects_noncanonical_unit_text_even_with_valid_hash(self) -> None:
        spec = SystemdLabFixtureSpec("alpha-01")
        unit_text = "[Service]\nExecStart=/usr/bin/true\n"
        digest = hashlib.sha256(unit_text.encode("utf-8")).hexdigest()

        with self.assertRaises(FaultLabFixtureError):
            SystemdLabFixtureArtifact(
                spec=spec,
                unit_text=unit_text,
                sha256=digest,
            )

    def test_artifact_creation_time_is_timezone_aware(self) -> None:
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("alpha-01"))

        self.assertIsInstance(artifact.created_at, datetime)
        self.assertIsNotNone(artifact.created_at.utcoffset())

    def test_artifact_metadata_omits_full_unit_text(self) -> None:
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("alpha-01"))
        payload = artifact.to_dict()

        self.assertEqual(payload["unit_name"], artifact.unit_name)
        self.assertEqual(payload["sha256"], artifact.sha256)
        self.assertNotIn("unit_text", payload)
        self.assertGreater(payload["unit_text_bytes"], 0)

    def test_private_copy_is_exclusive_and_mode_0600(self) -> None:
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("alpha-01"))
        with tempfile.TemporaryDirectory() as directory:
            output = artifact.write_private_copy(directory)

            self.assertEqual(output.read_text(encoding="utf-8"), artifact.unit_text)
            mode = stat.S_IMODE(output.stat().st_mode)
            self.assertEqual(mode, 0o600)
            with self.assertRaises(FaultLabFixtureError):
                artifact.write_private_copy(directory)

    def test_private_copy_rejects_systemd_runtime_tree(self) -> None:
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("alpha-01"))

        if SYSTEMD_LAB_RUNTIME_ROOT.exists():
            with self.assertRaises(FaultLabFixtureError):
                artifact.write_private_copy(SYSTEMD_LAB_RUNTIME_ROOT)

    def test_private_copy_requires_existing_directory(self) -> None:
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("alpha-01"))
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            with self.assertRaises(FaultLabFixtureError):
                artifact.write_private_copy(missing)

    def test_private_copy_rejects_regular_file_as_directory(self) -> None:
        artifact = build_systemd_lab_fixture(SystemdLabFixtureSpec("alpha-01"))
        with tempfile.TemporaryDirectory() as directory:
            regular_file = Path(directory) / "regular"
            regular_file.write_text("x", encoding="utf-8")
            with self.assertRaises(FaultLabFixtureError):
                artifact.write_private_copy(regular_file)

    def test_spec_serialization_is_stable_and_path_is_string(self) -> None:
        spec = SystemdLabFixtureSpec("alpha-01", runtime_max_seconds=42)
        payload = spec.to_dict()

        self.assertEqual(payload["fixture_id"], "alpha-01")
        self.assertEqual(payload["unit_name"], spec.unit_name)
        self.assertEqual(payload["runtime_max_seconds"], 42)
        self.assertEqual(
            payload["runtime_install_path"],
            os.fspath(SYSTEMD_LAB_RUNTIME_ROOT / spec.unit_name),
        )

    def test_builder_rejects_wrong_spec_type(self) -> None:
        with self.assertRaises(FaultLabFixtureError):
            build_systemd_lab_fixture(cast(SystemdLabFixtureSpec, "alpha-01"))


if __name__ == "__main__":
    unittest.main()
