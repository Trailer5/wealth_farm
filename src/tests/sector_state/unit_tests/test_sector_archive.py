import math
import unittest
from datetime import date, timedelta
from types import SimpleNamespace

from src.research_contracts import Confidence
from src.sector_data import IndustryMember, SwIndustry
from src.sector_state import SectorArchiveService

AS_OF = "2026-10-23"
L1 = SwIndustry("801120", "食品饮料", 1, None, 6)
L3 = SwIndustry("851251", "白酒Ⅲ", 3, "801125", 6)
MEMBERS = [f"60000{index}" for index in range(6)]


def _business_days(end: str, count: int) -> list[str]:
    days = []
    current = date.fromisoformat(end)
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current -= timedelta(days=1)
    return sorted(days)


DAYS = _business_days("2026-10-23", 700)


class _SectorData:
    def __init__(self, industry, *, valuation=True):
        self.item = industry
        self.valuation = valuation

    def industry(self, code, *, allow_remote=True):
        return self.item if code == self.item.code else None

    def members(self, code, *, allow_remote=True):
        return tuple(IndustryMember(symbol, symbol, code, None) for symbol in MEMBERS)

    def index_asset(self, code, *, allow_remote=True):
        return SimpleNamespace(asset_id=f"asset:sw-{code}")

    def valuation_history(self, code, *, start_date, end_date, allow_remote=True):
        month_ends = sorted({day[:7]: day for day in DAYS}.values())
        return {
            "points": [
                {
                    "trade_date": day,
                    "pe": 20 + index % 15,
                    "pb": 4 + 0.1 * (index % 10),
                    "amount_share_percent": 3 + 0.2 * (index % 8),
                }
                for index, day in enumerate(month_ends)
            ],
            "provider_errors": {},
        }


class _CrossSection:
    def valuation_history(self, symbols, *, start_date, end_date, allow_remote=True):
        return {
            "series": {
                symbol: [
                    {
                        "trade_date": day,
                        "close": 10 * (1 + 0.0004 * index * (offset + 1) + 0.05 * math.sin(index / 9)),
                        "pe_ttm": 15 + offset + 0.01 * index,
                        "pb": 2 + 0.1 * offset,
                    }
                    for index, day in enumerate(DAYS)
                ]
                for offset, symbol in enumerate(symbols)
            },
            "provider_errors": {},
        }

    def latest_financials(self, as_of_date, *, allow_remote=True):
        rows = [
            {
                "symbol": symbol,
                "report_period": "2026-06-30",
                "revenue": 100.0,
                "revenue_yoy_percent": 10.0,
                "net_profit": 20.0 if index else -5.0,
                "net_profit_yoy_percent": 25.0,
                "roe_percent": 12.0 + index,
                "gross_margin_percent": 50.0,
            }
            for index, symbol in enumerate(MEMBERS)
        ]
        rows.append({"symbol": "000001", "report_period": "2026-06-30", "revenue": 1e9})
        return {"rows": rows, "provider_errors": {}, "basis_note": "test"}

    def report_period(self, period, *, as_of_date=None, allow_remote=True):
        if period == "2026-06-30":
            return {"rows": self.latest_financials(as_of_date)["rows"], "provider_error": None}
        fields = {
            "2025-03-31": {"net_profit": 10.0},
            "2025-06-30": {"roe_percent": 10.0, "net_profit": 20.0},
            "2025-12-31": {"roe_percent": 20.0, "net_profit": 40.0},
            "2026-03-31": {"net_profit": 8.0},
        }.get(period)
        rows = [] if fields is None else [
            {"symbol": symbol, "report_period": period, **fields} for symbol in MEMBERS
        ]
        return {"rows": rows, "provider_error": None}


class _DataAccess:
    def get(self, request):
        sector = request.asset.asset_id is not None
        records = tuple(
            {
                "trade_date": day,
                "close": 4000 * (1 + (0.0006 if sector else 0.0002) * index + 0.01 * math.sin(index / 7)),
            }
            for index, day in enumerate(DAYS)
        )
        return SimpleNamespace(records=records)


def _service(industry):
    return SectorArchiveService(
        sector_data=_SectorData(industry),
        cross_section=_CrossSection(),
        data_access=_DataAccess(),
    )


class SectorArchiveTest(unittest.TestCase):
    def test_level_one_archive_has_all_dimensions(self):
        first = _service(L1).build(AS_OF, code=L1.code, allow_remote=False)
        second = _service(L1).build(AS_OF, code=L1.code, allow_remote=False)

        assessment = first.assessment
        self.assertEqual(
            [item.name for item in assessment.dimensions],
            ["估值", "相对强弱", "盈利", "拥挤度", "宽度"],
        )
        self.assertEqual(assessment.subject_id, "sector:sw:801120")
        self.assertEqual(assessment.assessment_id, second.assessment.assessment_id)
        self.assertEqual(assessment.confidence, Confidence.HIGH)
        self.assertIn("申万食品饮料", assessment.label)
        earnings = first.metrics["盈利"]
        self.assertEqual(earnings["members_reported"], 6)
        self.assertEqual(earnings["loss_making"], 1)
        self.assertAlmostEqual(earnings["revenue_yoy"], 0.1, places=6)
        self.assertAlmostEqual(earnings["roe_ttm_median_percent"], 24.5)
        self.assertAlmostEqual(earnings["net_profit_quarter_yoy"], 47 / 60 - 1, places=6)
        self.assertAlmostEqual(earnings["profit_concentration"]["top_share"], round(8 / 19, 4))
        self.assertAlmostEqual(earnings["profit_concentration"]["rest_growth"], 0.25, places=6)
        self.assertEqual(earnings["members_not_counted"], [])
        self.assertEqual(first.metrics["估值"]["basis"], "申万行业指数估值（月末与最新）")
        self.assertGreater(first.metrics["相对强弱"]["excess_250d"], 0)
        self.assertTrue(any("资金流" in item for item in first.gaps))

    def test_level_three_uses_member_valuation_and_reports_crowding_gap(self):
        archive = _service(L3).build(AS_OF, code=L3.code, allow_remote=False)

        names = [item.name for item in archive.assessment.dimensions]
        self.assertNotIn("拥挤度", names)
        self.assertIn("估值", names)
        self.assertIn("中位数", archive.metrics["估值"]["basis"])
        self.assertEqual(archive.assessment.confidence, Confidence.MEDIUM)
        self.assertTrue(any(item.startswith("拥挤度") for item in archive.gaps))

    def test_unknown_industry_raises(self):
        with self.assertRaises(KeyError):
            _service(L1).build(AS_OF, code="999999", allow_remote=False)


if __name__ == "__main__":
    unittest.main()
