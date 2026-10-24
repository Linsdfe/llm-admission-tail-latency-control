"""Model-free checks for split isolation and paired development arrivals."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
from p1_workload import build_candidates, make_scenario, materialize_trace, render_candidate, FAMILIES


class P1WorkloadTests(unittest.TestCase):
    def test_final_path_and_unregistered_seeds_are_rejected(self):
        for split, seed in (("final", 31001), ("test", 31001), ("development", 11001), ("calibration", 21001)):
            with self.assertRaises(ValueError):
                build_candidates(split, seed)

    def test_template_families_and_content_are_split_isolated(self):
        cal = build_candidates("calibration", 11001)
        dev = build_candidates("development", 21001)
        self.assertEqual(len(cal), 24)
        self.assertEqual(len(dev), 230)
        self.assertFalse({r["template_family"] for r in cal} & {r["template_family"] for r in dev})
        self.assertFalse({render_candidate(r, 0) for r in cal} & {render_candidate(r, 0) for r in dev})
        self.assertEqual(cal, build_candidates("calibration", 11001))

    def test_all_buckets_covered_in_calibration(self):
        cal = build_candidates("calibration", 11001)
        for category in ("interactive", "background"):
            for bucket in range(3):
                self.assertEqual(sum(r["category"] == category and r["input_token_bucket"] == bucket for r in cal), 4)

    def test_materializer_uses_actual_callback_and_refuses_infeasible_range(self):
        descriptors = build_candidates("calibration", 11001, interactive_count=1, background_count=0)
        calls = []
        def fake_tokens(prompt):
            calls.append(prompt)
            return list(range(32 + prompt.count("补充") * 10))
        rows = materialize_trace(descriptors, fake_tokens)
        self.assertEqual(rows[0]["input_tokens"], 32)
        self.assertEqual(rows[0]["prompt"], calls[0])
        self.assertFalse(rows[0]["ignore_eos"])
        with self.assertRaises(ValueError):
            materialize_trace(descriptors, lambda prompt: list(range(300)))

    def test_scenarios_pair_content_and_sparse_is_predeclared_subset(self):
        descriptors = build_candidates("development", 21001)
        # Arrivals and pairing can be checked without pretending to know tokens.
        rows = [dict(row, prompt=render_candidate(row, 0), content_sha256=row["req_id"]) for row in descriptors]
        original = deepcopy(rows)
        burst, steady, sparse = [make_scenario(rows, name) for name in ("main_bursty_mixed", "boundary_steady", "boundary_sparse")]
        self.assertEqual(rows, original)
        strip = lambda trace: sorted(({k: v for k, v in r.items() if k != "arrival_offset_ms"} for r in trace), key=lambda r: r["req_id"])
        self.assertEqual(strip(burst), strip(steady))
        self.assertEqual(len(burst), 230)
        self.assertEqual(len(sparse), 210)
        self.assertEqual(sorted(r["category_index"] for r in sparse if r["category"] == "background"), list(range(0, 30, 3)))
        self.assertEqual({r["input_token_bucket"] for r in sparse if r["category"] == "background"}, {0, 1, 2})
        self.assertEqual(max(r["arrival_offset_ms"] for r in burst), 99500)
        self.assertEqual([r["arrival_offset_ms"] for r in burst if r["category"] == "background"][:3], [250, 300, 350])
        self.assertEqual([r["arrival_offset_ms"] for r in sparse if r["category"] == "background"], list(range(250, 90251, 10000)))


if __name__ == "__main__":
    unittest.main()
