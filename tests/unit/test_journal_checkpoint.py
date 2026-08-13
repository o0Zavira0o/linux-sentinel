"""Tests for restart-safe atomic journald cursor checkpoints."""

from __future__ import annotations

import json
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sentinel_x.systemd import (
    SYSTEMD_JOURNAL_CHECKPOINT_SCHEMA_VERSION,
    AtomicSystemdJournalCheckpointStore,
    SystemdJournalCheckpoint,
    SystemdJournalCheckpointStorageError,
    SystemdJournalCheckpointValidationError,
)

_BOOT_A = "a" * 32
_NOW = datetime(2026, 8, 13, 18, 30, tzinfo=timezone.utc)
_COLLECTOR = "journal.demo.1234"
_UNIT = "demo.service"


def _checkpoint(
    *,
    cursor: str | None = "cursor-1",
    collector_name: str = _COLLECTOR,
    unit_name: str = _UNIT,
) -> SystemdJournalCheckpoint:
    return SystemdJournalCheckpoint(
        collector_name=collector_name,
        unit_name=unit_name,
        boot_id=_BOOT_A,
        cursor=cursor,
        committed_at=_NOW,
    )


class SystemdJournalCheckpointModelTests(unittest.TestCase):
    """Verify typed checkpoint schema invariants."""

    def test_checkpoint_normalizes_and_serializes(self) -> None:
        checkpoint = SystemdJournalCheckpoint(
            collector_name=" journal.demo.1234 ",
            unit_name="demo.service",
            boot_id="A" * 32,
            cursor="opaque-cursor",
            committed_at=_NOW,
        )

        payload = checkpoint.to_dict()

        self.assertEqual(checkpoint.collector_name, _COLLECTOR)
        self.assertEqual(checkpoint.unit_name, _UNIT)
        self.assertEqual(checkpoint.boot_id, _BOOT_A)
        self.assertEqual(payload["cursor"], "opaque-cursor")
        self.assertEqual(
            payload["schema_version"],
            SYSTEMD_JOURNAL_CHECKPOINT_SCHEMA_VERSION,
        )
        self.assertEqual(payload["committed_at"], _NOW.isoformat())

    def test_checkpoint_allows_null_cursor(self) -> None:
        checkpoint = _checkpoint(cursor=None)
        self.assertIsNone(checkpoint.cursor)

    def test_checkpoint_rejects_invalid_identity_and_state(self) -> None:
        with self.assertRaises(SystemdJournalCheckpointValidationError):
            _checkpoint(collector_name="bad name")
        with self.assertRaises(SystemdJournalCheckpointValidationError):
            _checkpoint(unit_name="bad.target")
        with self.assertRaises(SystemdJournalCheckpointValidationError):
            SystemdJournalCheckpoint(
                collector_name=_COLLECTOR,
                unit_name=_UNIT,
                boot_id="not-a-boot",
                cursor="cursor-1",
                committed_at=_NOW,
            )
        with self.assertRaises(SystemdJournalCheckpointValidationError):
            _checkpoint(cursor="bad\nvalue")
        with self.assertRaises(SystemdJournalCheckpointValidationError):
            SystemdJournalCheckpoint(
                collector_name=_COLLECTOR,
                unit_name=_UNIT,
                boot_id=_BOOT_A,
                cursor="cursor-1",
                committed_at=datetime(2026, 8, 13),
            )
        with self.assertRaises(SystemdJournalCheckpointValidationError):
            SystemdJournalCheckpoint(
                collector_name=_COLLECTOR,
                unit_name=_UNIT,
                boot_id=_BOOT_A,
                cursor="cursor-1",
                committed_at=_NOW,
                schema_version=999,
            )
        with self.assertRaises(SystemdJournalCheckpointValidationError):
            SystemdJournalCheckpoint(
                collector_name=_COLLECTOR,
                unit_name=_UNIT,
                boot_id=_BOOT_A,
                cursor="cursor-1",
                committed_at=_NOW,
                schema_version=True,
            )


class AtomicSystemdJournalCheckpointStoreTests(unittest.TestCase):
    """Verify bounded private atomic checkpoint persistence."""

    def _store(
        self,
        root: Path,
    ) -> AtomicSystemdJournalCheckpointStore:
        return AtomicSystemdJournalCheckpointStore(
            directory=root,
            instance_name="test-agent",
        )

    def test_missing_load_is_lazy_and_creates_no_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkpoints"
            store = self._store(root)

            loaded = store.load(_COLLECTOR, _UNIT)

            self.assertIsNone(loaded)
            self.assertFalse(root.exists())

    def test_save_load_round_trip_and_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkpoints"
            store = self._store(root)
            checkpoint = _checkpoint()

            store.save(checkpoint)
            loaded = store.load(_COLLECTOR, _UNIT)

            self.assertEqual(loaded, checkpoint)
            instance_mode = stat.S_IMODE(store.directory.stat().st_mode)
            file_mode = stat.S_IMODE(store.path_for(_COLLECTOR).stat().st_mode)
            self.assertEqual(instance_mode, 0o700)
            self.assertEqual(file_mode, 0o600)

    def test_second_save_atomically_replaces_previous_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary) / "checkpoints")
            store.save(_checkpoint(cursor="cursor-1"))
            store.save(_checkpoint(cursor="cursor-2"))

            loaded = store.load(_COLLECTOR, _UNIT)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.cursor, "cursor-2")
            temporary_files = list(store.directory.glob("*.tmp"))
            self.assertEqual(temporary_files, [])

    def test_checksum_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary) / "checkpoints")
            store.save(_checkpoint())
            path = store.path_for(_COLLECTOR)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["cursor"] = "tampered-cursor"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesRegex(
                SystemdJournalCheckpointValidationError,
                "checksum",
            ):
                store.load(_COLLECTOR, _UNIT)

    def test_unknown_or_missing_schema_keys_are_rejected(self) -> None:
        for mutation in ("unknown", "missing"):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    store = self._store(Path(temporary) / "checkpoints")
                    store.save(_checkpoint())
                    path = store.path_for(_COLLECTOR)
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if mutation == "unknown":
                        payload["unexpected"] = True
                    else:
                        del payload["unit_name"]
                    path.write_text(json.dumps(payload), encoding="utf-8")

                    with self.assertRaisesRegex(
                        SystemdJournalCheckpointValidationError,
                        "keys do not match schema",
                    ):
                        store.load(_COLLECTOR, _UNIT)

    def test_oversized_checkpoint_is_rejected_before_json_parse(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary) / "checkpoints")
            store.save(_checkpoint())
            path = store.path_for(_COLLECTOR)
            path.write_bytes(b"x" * 32_769)

            with self.assertRaisesRegex(
                SystemdJournalCheckpointStorageError,
                "exceeds 32768 bytes",
            ):
                store.load(_COLLECTOR, _UNIT)

    def test_symlink_checkpoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = self._store(root / "checkpoints")
            store.directory.mkdir(parents=True)
            store.directory.chmod(0o700)
            target = root / "other.json"
            target.write_text("{}", encoding="utf-8")
            path = store.path_for(_COLLECTOR)
            path.symlink_to(target)

            with self.assertRaisesRegex(
                SystemdJournalCheckpointStorageError,
                "must not be a symlink",
            ):
                store.load(_COLLECTOR, _UNIT)

    def test_dangling_symlink_checkpoint_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary) / "checkpoints")
            store.directory.mkdir(parents=True)
            store.directory.chmod(0o700)
            path = store.path_for(_COLLECTOR)
            path.symlink_to(Path(temporary) / "missing-target")

            with self.assertRaises(SystemdJournalCheckpointStorageError):
                store.load(_COLLECTOR, _UNIT)

    def test_insecure_checkpoint_permissions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary) / "checkpoints")
            store.save(_checkpoint())
            path = store.path_for(_COLLECTOR)
            path.chmod(0o644)

            with self.assertRaisesRegex(
                SystemdJournalCheckpointStorageError,
                "permissions are not private",
            ):
                store.load(_COLLECTOR, _UNIT)

    def test_insecure_instance_directory_permissions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary) / "checkpoints")
            store.save(_checkpoint())
            store.directory.chmod(0o755)

            with self.assertRaisesRegex(
                SystemdJournalCheckpointStorageError,
                "directory permissions are not private",
            ):
                store.load(_COLLECTOR, _UNIT)

    def test_unit_identity_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary) / "checkpoints")
            store.save(_checkpoint())

            with self.assertRaisesRegex(
                SystemdJournalCheckpointValidationError,
                "unit identity",
            ):
                store.load(_COLLECTOR, "other.service")

    def test_checkpoint_root_that_is_file_fails_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "not-a-directory"
            root.write_text("occupied", encoding="utf-8")
            store = self._store(root)

            with self.assertRaises(SystemdJournalCheckpointStorageError):
                store.save(_checkpoint())

    def test_checkpoint_root_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            real = base / "real"
            real.mkdir()
            root = base / "linked"
            root.symlink_to(real, target_is_directory=True)
            store = self._store(root)

            with self.assertRaisesRegex(
                SystemdJournalCheckpointStorageError,
                "root must not be a symlink",
            ):
                store.save(_checkpoint())

    def test_invalid_instance_name_is_rejected_without_filesystem_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkpoints"
            with self.assertRaises(ValueError):
                AtomicSystemdJournalCheckpointStore(
                    directory=root,
                    instance_name="bad name",
                )
            self.assertFalse(root.exists())

    def test_saved_json_has_integrity_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = self._store(Path(temporary) / "checkpoints")
            store.save(_checkpoint())
            payload = json.loads(store.path_for(_COLLECTOR).read_text(encoding="utf-8"))

            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(len(payload["payload_sha256"]), 64)
            self.assertEqual(payload["collector_name"], _COLLECTOR)
            self.assertEqual(payload["unit_name"], _UNIT)


if __name__ == "__main__":
    unittest.main()
