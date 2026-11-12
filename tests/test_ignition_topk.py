"""Synthetic-data tests for scripts.ignition_modeling.topk (no cache or model needed)."""
import unittest

import numpy as np
from sklearn.metrics import average_precision_score

from scripts.ignition_modeling import topk
from scripts.ignition_modeling.evaluation import threshold_tradeoffs


def two_days():
    """Day A: 10 cells, positives at scores .9 and .1; day B: 10 cells, none."""
    dates = np.array(["2019-05-01"] * 10 + ["2019-05-02"] * 10)
    pa = np.array([.9, .8, .7, .6, .5, .4, .3, .2, .15, .1])
    ya = np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 1])
    pb = np.linspace(.05, .5, 10)
    return np.concatenate([ya, np.zeros(10, int)]), np.concatenate([pa, pb]), dates


def random_problem(seed=0, days=40, cells=60, ties=False):
    rng = np.random.default_rng(seed)
    dates = np.repeat(np.datetime64("2019-05-01") + np.arange(days), cells)
    p = rng.beta(.3, 8, days * cells)
    if ties:
        p = np.round(p, 2)
    y = (rng.random(p.size) < p * .2).astype(int)
    return y, p, dates


class TopKTests(unittest.TestCase):
    def test_hand_computed_values_and_zero_positive_days(self):
        y, p, dates = two_days()
        day = topk.per_day_topk(y, p, dates, (0.1, 0.2, 0.5))
        self.assertEqual(day["positives"].tolist(), [2, 0])
        self.assertEqual(day["k"][0.1].tolist(), [1, 1])
        self.assertEqual(day["k"][0.2].tolist(), [2, 2])
        rows = {r["fraction"]: r for r in topk.topk_table(day)}
        self.assertEqual(rows[0.1]["days_zero_positives_excluded_from_mean_recall"], 1)
        # top-1 on day A hits one of two positives; day B has no positives.
        self.assertAlmostEqual(rows[0.1]["pooled_recall"], 0.5)
        self.assertAlmostEqual(rows[0.1]["mean_day_recall"], 0.5)   # only day A counts
        self.assertAlmostEqual(rows[0.1]["pooled_precision"], 1 / 2)  # 1 hit / (1+1) alerts
        self.assertAlmostEqual(rows[0.1]["mean_day_precision"], 0.5)  # (1 + 0) / 2 days
        prevalence = 2 / 20
        self.assertAlmostEqual(rows[0.1]["pooled_lift"], 0.5 / prevalence)
        self.assertAlmostEqual(rows[0.5]["pooled_recall"], 0.5)  # .1 positive is rank 10
        self.assertEqual(rows[0.5]["expected_true_positives"], 1.0)

    def test_k_rounds_up_and_survives_float_error(self):
        self.assertEqual(topk._k_for(0.1, 10), 1)          # 0.1*10 == 1.0000000000000002 is not 2
        self.assertEqual(topk._k_for(0.01, 9673), 97)
        self.assertEqual(topk._k_for(0.05, 9673), 484)
        self.assertEqual(topk._k_for(0.1, 9673), 968)
        self.assertEqual(topk._k_for(0.0001, 10), 1)
        with self.assertRaises(ValueError):
            topk._k_for(0, 10)

    def test_ties_use_expected_value_and_constant_score_is_chance(self):
        dates = np.array(["2019-05-01"] * 10)
        y = np.array([1, 0, 0, 0, 0, 0, 0, 0, 0, 0])
        day = topk.per_day_topk(y, np.full(10, 0.3), dates, (0.2,))
        self.assertAlmostEqual(day["tp"][0.2][0], 0.2)  # 2 of 10 cells flagged -> chance
        # a tie group straddling the cutoff: 1 above, 4 tied (1 positive), k=3
        p = np.array([.9, .5, .5, .5, .5, .1, .1, .1, .1, .1])
        y = np.array([0, 1, 0, 0, 0, 0, 0, 0, 0, 0])
        day = topk.per_day_topk(y, p, dates, (0.3,))
        self.assertAlmostEqual(day["tp"][0.3][0], 2 * 1 / 4)

    def test_row_order_does_not_change_results(self):
        y, p, dates = random_problem(1, ties=True)
        perm = np.random.default_rng(2).permutation(y.size)
        a = topk.topk_table(topk.per_day_topk(y, p, dates))
        b = topk.topk_table(topk.per_day_topk(y[perm], p[perm], dates[perm]))
        self.assertEqual(a, b)

    def test_input_validation(self):
        y, p, dates = two_days()
        with self.assertRaises(ValueError):
            topk.per_day_topk(y, p * 5, dates)
        with self.assertRaises(ValueError):
            topk.per_day_topk(y + 1, p, dates)
        with self.assertRaises(ValueError):
            topk.per_day_topk(y, p, np.array(["2019-05-01T12:00:00"] * 20))
        with self.assertRaises(ValueError):
            topk.per_day_topk(y, p[:-1], dates)


class BootstrapTests(unittest.TestCase):
    def test_weighted_ap_equals_sklearn_on_repeated_rows(self):
        for ties in (False, True):
            y, p, dates = random_problem(3, days=12, cells=50, ties=ties)
            ap, n_days = topk.weighted_ap_function(y, p, dates)
            self.assertAlmostEqual(ap(np.ones(n_days)), average_precision_score(y, p), places=12)
            counts = np.random.default_rng(4).integers(0, 4, n_days)
            repeated = np.repeat(np.arange(y.size), np.repeat(counts, 50))
            self.assertAlmostEqual(ap(counts), average_precision_score(y[repeated], p[repeated]),
                                   places=12)

    def test_weighted_ap_with_undrawn_top_scoring_date_is_finite_and_exact(self):
        y, p, dates = random_problem(10, days=6, cells=40)
        top_day = int(np.argmax(p) // 40)
        counts = np.ones(6, dtype=int)
        counts[top_day] = 0
        ap, _ = topk.weighted_ap_function(y, p, dates)
        keep = np.repeat(counts > 0, 40)
        self.assertAlmostEqual(ap(counts), average_precision_score(y[keep], p[keep]), places=12)

    def test_counts_are_seeded_blocked_and_sum_to_n_days(self):
        a = topk.bootstrap_counts(30, 50, seed=385)
        self.assertTrue(np.array_equal(a, topk.bootstrap_counts(30, 50, seed=385)))
        self.assertFalse(np.array_equal(a, topk.bootstrap_counts(30, 50, seed=386)))
        self.assertTrue((a.sum(axis=1) == 30).all())
        b = topk.bootstrap_counts(30, 50, seed=385, block_days=7)
        self.assertTrue((b.sum(axis=1) == 30).all())

    def test_bootstrap_summaries_match_direct_resample(self):
        y, p, dates = random_problem(5, days=20, cells=40)
        day = topk.per_day_topk(y, p, dates, (0.1,))
        counts = topk.bootstrap_counts(20, 5, seed=385)
        boot = topk._summaries(day, 0.1, counts)
        order = np.argsort(dates, kind="stable")
        y, p, dates = y[order], p[order], dates[order]
        for i in range(counts.shape[0]):
            picks = np.repeat(np.arange(20), counts[i])
            rows = np.concatenate([np.arange(d * 40, d * 40 + 40) for d in picks])
            # rebuild with unique dates so repeated days stay separate days
            new_dates = np.datetime64("2019-05-01") + np.repeat(np.arange(len(picks)), 40)
            direct = topk.topk_table(topk.per_day_topk(y[rows], p[rows], new_dates, (0.1,)))[0]
            self.assertAlmostEqual(boot["pooled_recall"][i], direct["pooled_recall"], places=12)
            self.assertAlmostEqual(boot["mean_day_recall"][i], direct["mean_day_recall"], places=12)
            self.assertAlmostEqual(boot["mean_day_lift"][i], direct["mean_day_lift"], places=12)

    def test_report_is_reproducible_and_interval_brackets_point(self):
        y, p, dates = random_problem(6, days=60, cells=80)
        counts = topk.bootstrap_counts(60, 200, seed=385)
        a = topk.system_report(y, p, dates, (0.05,), counts)
        b = topk.system_report(y, p, dates, (0.05,), counts)
        self.assertEqual(a, b)
        iv = a["bootstrap"]["average_precision"]
        self.assertLessEqual(iv["ci95_low"], iv["point"])
        self.assertGreaterEqual(iv["ci95_high"], iv["point"])
        self.assertAlmostEqual(iv["point"], average_precision_score(y, p), places=12)


class BaselineAndAlertTests(unittest.TestCase):
    def test_climatology_uses_only_training_rows(self):
        train_ids, train_y = np.array([0, 0, 1, 1, 2]), np.array([1, 0, 0, 0, 0])
        scores = topk.climatology_scores(train_ids, train_y, np.array([2, 1, 0, 3]), 4)
        np.testing.assert_allclose(scores, [0, 0, .5, 0])  # cell 3 unseen -> 0
        with self.assertRaises(ValueError):
            topk.climatology_scores(train_ids, train_y, np.array([9]), 4)

    def test_constant_rate(self):
        np.testing.assert_allclose(topk.constant_rate_scores(np.array([1, 0, 0, 0]), 3), .25)

    def test_cutoffs_depend_on_fit_split_only_and_rule_matches_existing_tradeoffs(self):
        y, p, dates = random_problem(7)
        y2, p2, d2 = random_problem(8)
        c1 = topk.cutoffs_from_fit_split(p)
        opts = topk.alert_options(y, p, dates, others={"other": (y2, p2, d2)})
        self.assertEqual(opts["cutoffs_from_fit_split"], c1)
        opts2 = topk.alert_options(y, p, dates, others={"other": (y2[::-1], p2[::-1] * .5, d2)})
        self.assertEqual(opts2["cutoffs_from_fit_split"], c1)
        cutoff = c1[0.05]
        mine = topk.cutoff_rule_metrics(y2, p2, d2, cutoff)
        theirs = threshold_tradeoffs(y2, p2, [cutoff], dates=d2)[0]
        self.assertEqual(mine["n_alerts"], theirs["n_alerts"])
        self.assertAlmostEqual(mine["recall"], theirs["recall"])
        self.assertAlmostEqual(mine["alerts_per_day_mean"], theirs["alerts_per_day"])
        self.assertAlmostEqual(mine["lift"], theirs["precision"] / (y2.sum() / y2.size))

    def test_markdown_renders_all_systems(self):
        y, p, dates = random_problem(9, days=10, cells=30)
        counts = topk.bootstrap_counts(10, 20)
        reports = {"model": topk.system_report(y, p, dates, (0.1,), counts),
                   "rate": topk.system_report(y, np.full(y.size, y.mean()), dates, (0.1,), counts,
                                              with_reliability=False)}
        text = topk.markdown_tables("synthetic", reports)
        self.assertIn("| rate | 10% |", text)
        self.assertIn("mean-of-days recall", text)


if __name__ == "__main__":
    unittest.main()
