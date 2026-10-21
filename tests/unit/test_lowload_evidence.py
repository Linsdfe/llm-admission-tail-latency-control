"""Boundary tests for dependent-request backlog diagnostics."""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/analysis"))
from lowload_evidence import active_before, diagnostics


class BacklogTests(unittest.TestCase):
    def test_left_limit_excludes_new_arrival_but_includes_equal_end(self):
        rows = [{"actual_send_offset_ms": 0, "end_offset_ms": 100},
                {"actual_send_offset_ms": 100, "end_offset_ms": 200},
                {"actual_send_offset_ms": None, "end_offset_ms": 300}]
        self.assertEqual(active_before(rows, 100), 1)
        self.assertEqual(active_before(rows, 100.001), 1)

    def test_failed_request_cannot_pass_no_backlog_screen(self):
        rows = [{"category": "interactive", "arrival_offset_ms": i*500,
                 "actual_send_offset_ms": i*500+1, "end_offset_ms": i*500+40,
                 "ttft_ms": 20, "terminal": "completed"} for i in range(200)]
        rows += [{"category": "background", "arrival_offset_ms": 250+i*10000,
                  "actual_send_offset_ms": 251+i*10000, "end_offset_ms": 2000+i*10000,
                  "ttft_ms": 30, "terminal": "completed"} for i in range(10)]
        cfg = {"measurement_seconds": 100, "max_quarter_median_increase_ms": 100,
               "max_drain_for_no_persistent_backlog_seconds": 10}
        self.assertTrue(diagnostics(rows, cfg)["no_obvious_persistent_backlog"])
        rows[0]["terminal"] = "timeout"
        self.assertFalse(diagnostics(rows, cfg)["no_obvious_persistent_backlog"])
        rows[0]["terminal"] = "completed"
        rows[-10]["end_offset_ms"] = 12000
        self.assertFalse(diagnostics(rows, cfg)["no_obvious_persistent_backlog"])


if __name__ == "__main__":
    unittest.main()
