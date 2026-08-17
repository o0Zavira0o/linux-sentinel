from __future__ import annotations

import runpy
import threading
import unittest
from pathlib import Path
from typing import Callable, cast


class Phase5FCaptureHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        namespace = runpy.run_path(str(Path("research/phase5f/capture_corpus_v1.py")))
        cls.wait_for_sample = staticmethod(
            cast(Callable[..., None], namespace["_wait_for_sidecar_sample_through"])
        )

    def test_post_fault_gate_accepts_completed_round_at_fault_end(self) -> None:
        condition = threading.Condition()
        samples: list[dict[str, object]] = [{"captured_monotonic_usec": 1_000_000}]
        self.wait_for_sample(
            condition,
            samples,
            [],
            1_000_000,
            timeout_seconds=0.0,
        )

    def test_post_fault_gate_rejects_sample_stream_ending_before_fault(self) -> None:
        condition = threading.Condition()
        samples: list[dict[str, object]] = [{"captured_monotonic_usec": 999_999}]
        with self.assertRaisesRegex(RuntimeError, "through fault end"):
            self.wait_for_sample(
                condition,
                samples,
                [],
                1_000_000,
                timeout_seconds=0.0,
            )

    def test_post_fault_gate_surfaces_sidecar_error_before_acceptance(self) -> None:
        condition = threading.Condition()
        samples: list[dict[str, object]] = [{"captured_monotonic_usec": 1_000_001}]
        with self.assertRaisesRegex(RuntimeError, "systemctl show failed"):
            self.wait_for_sample(
                condition,
                samples,
                ["systemctl show failed for unit.service"],
                1_000_000,
                timeout_seconds=0.0,
            )


if __name__ == "__main__":
    unittest.main()
