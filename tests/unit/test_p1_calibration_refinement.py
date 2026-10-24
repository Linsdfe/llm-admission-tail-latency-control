"""Model-free tests of the expanded split/sample registration."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("refinement", ROOT / "scripts/replay/79b_run_p1_calibration_refinement.py")
refinement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refinement)


class RefinementTests(unittest.TestCase):
    def test_registered_split_counts_and_frozen_sources(self):
        result = refinement.model_free_checks(refinement.base.read(refinement.CONFIG_PATH))
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["calibration_descriptors"], 120)
        self.assertEqual(result["development_validation_descriptors"], 120)

    def test_same_prompts_are_reproducible_and_new_ids_are_disjoint(self):
        cfg = refinement.base.read(refinement.CONFIG_PATH)
        generated = list(refinement.plans(cfg))
        self.assertEqual(generated, list(refinement.plans(cfg)))
        fresh_ids = {r["req_id"] for _, _, rs in generated for r in rs}
        old_cfg = refinement.base.read(ROOT / "configs/p1-development-workload-v1.json")
        old_ids = {r["req_id"] for split in ("calibration", "development")
                   for seed in old_cfg[split]["seeds"]
                   for r in refinement.build_candidates(split, seed, config=old_cfg)}
        self.assertFalse(fresh_ids & old_ids)

    def test_changed_output_limit_is_rejected_before_model_run(self):
        cfg = refinement.base.read(refinement.CONFIG_PATH)
        cfg["max_output_tokens"]["background"] = 256
        with self.assertRaises(AssertionError):
            refinement.model_free_checks(cfg)

    def test_final_split_is_disabled_in_new_configuration(self):
        cfg = refinement.base.read(refinement.CONFIG_PATH)
        with self.assertRaises(ValueError):
            refinement.build_candidates("final", 31001, config=cfg)


if __name__ == "__main__":
    unittest.main()
