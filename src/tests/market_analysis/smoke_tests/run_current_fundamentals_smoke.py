"""Live current-only validation for index and industry fundamentals."""

from __future__ import annotations

import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))

    from src.data_src.akshare import AkShareDataSource
    from src.data_src.nbs import NBSMacroDataSource
    from src.market_analysis import (
        IndexFundamentalCalculator,
        IndexValuationMethodComparisonCalculator,
        IndexValuationSnapshot,
        IndustryFundamentalSnapshot,
        MacroObservation,
        MarketIndustryAssessmentCalculator,
    )

    as_of = date.today().isoformat()
    akshare = AkShareDataSource()
    valuation_rows = [
        row
        for row in akshare.get_csindex_official_valuation("000300")
        if str(row["trade_date"]) <= as_of
    ]
    yield_rows = [
        row
        for row in akshare.get_china_ten_year_yield_history(
            f"{date.today().year - 1}-01-01"
        )
        if str(row["trade_date"]) <= as_of
    ]
    if not valuation_rows or not yield_rows:
        raise RuntimeError("current valuation or ten-year yield is missing")
    valuation_row = valuation_rows[-1]
    yield_row = yield_rows[-1]
    cpi_record = NBSMacroDataSource().get_latest_cpi_yoy()
    if str(cpi_record["available_at"]) > as_of:
        raise RuntimeError("latest CPI release is later than as_of")

    snapshots = tuple(
        IndexValuationSnapshot(
            asset_id="index:cn_a:sh:000300",
            as_of=str(valuation_row["trade_date"]),
            pe=Decimal(str(valuation_row[field])),
            pb=None,
            dividend_yield=Decimal(
                str(valuation_row[dividend_field])
            )
            / Decimal("100"),
            available_at=str(valuation_row["trade_date"]),
            methodology=methodology,
            sources=(str(valuation_row["source"]),),
        )
        for field, dividend_field, methodology in (
            ("pe1", "dividend_yield1_percent", "csindex_official_1"),
            ("pe2", "dividend_yield2_percent", "csindex_official_2"),
        )
    )
    assessment = IndexFundamentalCalculator().assess(
        snapshots[0],
        as_of=as_of,
        ten_year_yield=MacroObservation.from_ten_year_yield_record(
            yield_row
        ),
        cpi=MacroObservation.from_current_cpi_record(cpi_record),
    )
    method_comparison = (
        IndexValuationMethodComparisonCalculator().compare(snapshots)
    )

    industry_rows = akshare.get_sw_industry_daily_metrics(
        _prior_calendar_day(as_of),
        as_of,
    )
    industry_dates = sorted(
        {str(row["trade_date"]) for row in industry_rows}
    )
    if len(industry_dates) < 2:
        raise RuntimeError("two Shenwan industry dates are required")
    prior_date, current_date = industry_dates[-2:]
    prior = tuple(
        IndustryFundamentalSnapshot.from_sw_record(row)
        for row in industry_rows
        if row["trade_date"] == prior_date
    )
    current = tuple(
        IndustryFundamentalSnapshot.from_sw_record(row)
        for row in industry_rows
        if row["trade_date"] == current_date
    )
    industries = MarketIndustryAssessmentCalculator().assess(
        prior,
        current,
    )
    contribution_by_code = {
        item.industry_code: item.contribution
        for item in industries.contributions
    }
    top_code = industries.ranking("contribution").industry_codes[0]
    top_snapshot = next(
        item for item in industries.snapshots if item.industry_code == top_code
    )
    print(
        json.dumps(
            {
                "as_of": as_of,
                "valuation_date": snapshots[0].as_of,
                "earnings_yield": float(assessment.earnings_yield),
                "dividend_yield": float(assessment.dividend_yield),
                "real_yield_cpi_proxy": float(
                    assessment.real_yield_cpi_proxy
                ),
                "equity_risk_premium": float(
                    assessment.equity_risk_premium
                ),
                "pe_method_range": float(method_comparison.pe_range),
                "industry_date": industries.as_of,
                "industry_count": len(industries.snapshots),
                "top_contribution_industry": top_snapshot.industry_name,
                "top_contribution": float(
                    contribution_by_code[top_code]
                ),
                "cpi_source_url": cpi_record["source_url"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


def _prior_calendar_day(value: str) -> str:
    year, month, day = (int(part) for part in value.split("-"))
    return date.fromordinal(date(year, month, day).toordinal() - 7).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
