import math
import random
import unittest
from statistics import NormalDist

from src.backtest.significance import (
    expected_max_z,
    min_backtest_years,
    sharpe_moments,
    significance,
)
from src.backtest.significance_evidence import assess_evidence

N = NormalDist()


def _returns(count=2520, seed=7):
    rng = random.Random(seed)
    return [0.0004 + 0.01 * rng.gauss(0, 1) + (0.03 if i % 250 == 0 else 0.0) for i in range(count)]


def _by_hand(returns, trial_sharpes):
    """Independent hand calculation of DSR following Bailey & Lopez de Prado (2014)."""

    n = len(returns)
    mean = sum(returns) / n
    sd = (sum((r - mean) ** 2 for r in returns) / (n - 1)) ** 0.5
    sr = mean / sd
    m2 = sum((r - mean) ** 2 for r in returns) / n
    g3 = sum((r - mean) ** 3 for r in returns) / n / m2 ** 1.5
    g4 = sum((r - mean) ** 4 for r in returns) / n / m2 ** 2
    daily = [s / 252 ** 0.5 for s in trial_sharpes]
    k = len(daily)
    if k > 1:
        avg = sum(daily) / k
        spread = (sum((s - avg) ** 2 for s in daily) / (k - 1)) ** 0.5
        z = (1 - 0.5772156649) * N.inv_cdf(1 - 1 / k) + 0.5772156649 * N.inv_cdf(1 - 1 / (k * math.e))
        threshold = spread * z
    else:
        threshold = 0.0
    sigma = ((1 - g3 * sr + (g4 - 1) / 4 * sr ** 2) / (n - 1)) ** 0.5
    return N.cdf((sr - threshold) / sigma)


class SignificanceTest(unittest.TestCase):
    def test_method_passes_the_source_gate(self):
        self.assertTrue(assess_evidence().implementation_ready, assess_evidence())

    def test_twenty_trials_match_the_hand_calculation_and_deflate(self):
        returns = _returns()
        rng = random.Random(1)
        trials = [round(0.4 + 0.3 * rng.gauss(0, 1), 3) for _ in range(20)]
        dsrs = []
        for count in range(1, 21):
            result = significance(returns, trials[:count])
            self.assertEqual(result["trials"], count)
            self.assertAlmostEqual(result["dsr"], round(_by_hand(returns, trials[:count]), 4), places=4)
            dsrs.append(result["dsr"])
        self.assertEqual(dsrs[0], significance(returns, trials[:1])["psr"])
        self.assertLess(dsrs[-1], dsrs[0])
        self.assertLess(dsrs[19], dsrs[4])

    def test_moments_and_expected_maximum(self):
        moments = sharpe_moments([1.0, -1.0] * 50)
        self.assertAlmostEqual(moments.skew, 0.0)
        self.assertAlmostEqual(moments.kurtosis, 1.0)
        self.assertEqual(expected_max_z(1), 0.0)
        self.assertAlmostEqual(
            expected_max_z(100),
            (1 - 0.5772156649) * N.inv_cdf(0.99) + 0.5772156649 * N.inv_cdf(1 - 1 / (100 * math.e)),
            places=6,
        )
        self.assertLess(min_backtest_years(5, 1.0), min_backtest_years(50, 1.0))
        self.assertIsNone(min_backtest_years(1, 1.0))

    def test_flat_returns_are_reported_not_raised(self):
        self.assertIn("error", significance([0.0] * 10, [0.5]))


if __name__ == "__main__":
    unittest.main()
