"""Checks that the repair preserves tasks/data and isolates unseen pilot seeds."""
from collections import Counter
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/replay"))
import p1_workload as v1
import p1_workload_v2 as v2


class PromptRepairTests(unittest.TestCase):
    def test_fresh_development_seeds_and_cells(self):
        cfg = v2.load_config()
        rows = v1.build_candidates("development", 22001, config=cfg)
        self.assertEqual(len(rows), 24)
        self.assertEqual(set(Counter((r["category"], r["input_token_bucket"]) for r in rows).values()), {4})
        with self.assertRaises(ValueError):
            v1.build_candidates("final", 32001, config=cfg)
        with self.assertRaises(ValueError):
            v1.build_candidates("development", 21001, config=cfg)

    def test_every_family_preserves_original_task_records_and_numbers(self):
        cfg = v2.load_config()
        rows = v1.build_candidates("development", 22001, config=cfg) + v1.build_candidates("calibration", 11001)
        for descriptor in rows:
            for units in (0, 3):
                source = dict(descriptor, prompt=v1.render_candidate(descriptor, units))
                parts = v2.extract_task_parts(source)
                repaired = v2.render_repaired_prompt(source)
                self.assertIn(parts["task"], repaired)
                self.assertIn(parts["intro"], repaired)
                self.assertIn(parts["records"], repaired)
                self.assertTrue(repaired.endswith("<|im_start|>assistant\n"))
                self.assertEqual(v2.repair_metadata(source)["repair_task_sha256"], v1.sha_text(parts["task"]))
                if parts["records"]:
                    self.assertLess(repaired.index(parts["records"]), repaired.index(parts["task"]))

    def test_materializer_calls_actual_tokenizer_and_refuses_bad_lengths(self):
        cfg = v2.load_config()
        desc = v1.build_candidates("development", 22001, interactive_count=1, background_count=0, config=cfg)
        calls = []
        def tokenizer(prompt):
            calls.append(prompt)
            return list(range(50))
        rows = v2.materialize_v2(desc, tokenizer, cfg)
        self.assertEqual(rows[0]["input_tokens"], 50)
        self.assertEqual(rows[0]["template_version"], v2.TEMPLATE_VERSION)
        self.assertFalse(rows[0]["ignore_eos"])
        self.assertFalse(rows[0]["repair_uses_completion_or_answer"])
        self.assertEqual(rows[0]["prompt"], calls[0])
        with self.assertRaises(ValueError):
            v2.materialize_v2(desc, lambda prompt: list(range(300)), cfg)

    def test_unknown_prompt_format_or_final_source_is_refused(self):
        with self.assertRaises(ValueError):
            v2.render_repaired_prompt({"split": "final", "category": "interactive", "prompt": "bad"})
        with self.assertRaises(ValueError):
            v2.render_repaired_prompt({"split": "development", "category": "interactive", "prompt": "bad"})


if __name__ == "__main__":
    unittest.main()
