"""Fixed-history research comparison tests, separate from the production GP."""
import json
from types import SimpleNamespace
import unittest
import numpy as np
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from scripts.compare_emit_infobax_transport_moments import (
    DEFAULT_CONFIG, paired_seed_summary, prediction_metrics, validate_config,
)


class TransportMomentComparisonTests(unittest.TestCase):
    def test_seed_bootstrap_does_not_treat_repeated_cases_and_histories_as_independent(self):
        validate_config(json.loads(DEFAULT_CONFIG.read_text()))
        config = dict(models=["m0", "transport_moments"], observation_counts=[6, 14], bootstrap=dict(seed=13, samples=5000))
        rows = []
        for seed in range(4):
            for case in ("one", "two"):
                for policy in ("infobax", "random"):
                    for count in (6, 14):
                        for model in config["models"]:
                            delta = float(seed == 3 and model == "transport_moments")
                            rows.append(dict(seed=seed, case=case, history_policy=policy, observations=count, model=model,
                                invalid_output_probability=0.2+0.1*delta, recommendation_available=True,
                                withheld_normalized_rmse=1+delta, withheld_nlpd=1+delta))
        full = paired_seed_summary(rows, config)
        single = paired_seed_summary([r for r in rows if r["case"] == "one" and r["history_policy"] == "infobax"], config)
        self.assertEqual(full, single)
        self.assertEqual(full["14"]["withheld_normalized_rmse"]["seed_count"], 4)

    def test_withheld_scores_exclude_observed_point_residuals(self):
        posterior = SimpleNamespace(query_count=3, mean=np.array([1.0, 2.0, 3.0]), covariance=np.eye(3))
        first = prediction_metrics(posterior, {0}, [1, 2.1, 2.9], [0.1]*3)
        second = prediction_metrics(posterior, {0}, [999, 2.1, 2.9], [0.1]*3)
        self.assertEqual(first["withheld_rmse_mm2"], second["withheld_rmse_mm2"])
        self.assertEqual(first["withheld_nlpd"], second["withheld_nlpd"])
        with self.assertRaises(ValueError): prediction_metrics(posterior, {0, 1, 2}, [1, 2, 3], [0.1]*3)


if __name__ == "__main__":
    unittest.main()
