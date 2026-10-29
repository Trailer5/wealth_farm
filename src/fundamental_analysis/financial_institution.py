"""Financial-institution metrics from reported banking fields."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from src.master_data import ASSET_TYPE_STOCK, MasterDataService

from .models import (
    FundamentalInputEvidence,
    FundamentalMetricResult,
    MetricStatus,
)
from .sqlite_repository import SQLiteFundamentalMetricRepository


FINANCIAL_INSTITUTION_VERSION = "financial_institution_v1"
_REQUIRED_FIELDS = (
    "book_value_per_share",
    "roe",
    "capital_adequacy",
    "net_interest_margin",
    "non_performing_loan_ratio",
)
_EXPECTED_UNITS = {
    "book_value_per_share": "CNY_per_share",
    "roe": "ratio",
    "capital_adequacy": "ratio",
    "net_interest_margin": "ratio",
    "non_performing_loan_ratio": "ratio",
}


class FinancialInstitutionAnalysisService:
    def __init__(
        self,
        *,
        master_data: MasterDataService,
        repository: SQLiteFundamentalMetricRepository,
    ) -> None:
        self.master_data = master_data
        self.repository = repository
        self.repository.initialize()

    def analyze(
        self,
        *,
        asset_id: str,
        as_of_date: str,
        market_price: Decimal,
        indicator_records: list[dict[str, object]],
        target_report_period: str | None = None,
    ) -> tuple[FundamentalMetricResult, ...]:
        asset = self.master_data.get_asset(asset_id)
        if asset is None:
            raise KeyError(f"Unknown asset_id: {asset_id}")
        if asset.asset_type != ASSET_TYPE_STOCK:
            raise ValueError(
                "Financial institution metrics require a stock"
            )
        if market_price <= 0:
            raise ValueError("market_price must be positive")
        by_period: dict[
            str,
            dict[str, dict[str, object]],
        ] = defaultdict(dict)
        for record in indicator_records:
            field_name = str(record.get("field_name") or "")
            report_period = str(
                record.get("report_period") or ""
            )
            published_at = str(
                record.get("published_at") or ""
            )
            if (
                field_name in _REQUIRED_FIELDS
                and report_period
                and published_at
                and published_at <= as_of_date
            ):
                unit = str(record.get("unit") or "")
                if unit != _EXPECTED_UNITS[field_name]:
                    raise ValueError(
                        f"{field_name} requires unit "
                        f"{_EXPECTED_UNITS[field_name]}"
                    )
                current = by_period[report_period].get(field_name)
                if current is None:
                    by_period[report_period][field_name] = record
                    continue
                current_rank = (
                    int(current.get("revision", 1)),
                    str(current.get("published_at") or ""),
                )
                candidate_rank = (
                    int(record.get("revision", 1)),
                    published_at,
                )
                if candidate_rank > current_rank:
                    by_period[report_period][field_name] = record
                elif (
                    candidate_rank == current_rank
                    and str(record.get("normalized_value"))
                    != str(current.get("normalized_value"))
                ):
                    raise ValueError(
                        "Ambiguous bank metric values for "
                        f"{field_name} in {report_period}"
                    )
        complete_periods = [
            period
            for period, fields in by_period.items()
            if set(_REQUIRED_FIELDS).issubset(fields)
        ]
        if target_report_period is not None:
            if target_report_period not in complete_periods:
                raise ValueError(
                    "target_report_period does not contain all "
                    "required bank metrics"
                )
            report_period = target_report_period
        elif complete_periods:
            report_period = max(complete_periods)
        else:
            raise ValueError(
                "No report period contains all required bank metrics"
            )
        fields = by_period[report_period]
        book_value = _value(fields["book_value_per_share"])
        if book_value <= 0:
            raise ValueError(
                "book_value_per_share must be positive"
            )
        months = Decimal(int(report_period[5:7]))
        values = {
            "pb": market_price / book_value,
            # Disclosed ROE is year-to-date; annualize so interim periods compare with annual thresholds.
            "roe": _value(fields["roe"]) * Decimal(12) / months,
            "capital_adequacy": _value(
                fields["capital_adequacy"]
            ),
            "net_interest_margin": _value(
                fields["net_interest_margin"]
            ),
            "non_performing_loan_ratio": _value(
                fields["non_performing_loan_ratio"]
            ),
        }
        computed_at = datetime.now(timezone.utc).isoformat()
        results = tuple(
            FundamentalMetricResult(
                asset_id=asset_id,
                metric_name=metric_name,
                as_of_date=as_of_date,
                report_period=report_period,
                status=MetricStatus.AVAILABLE,
                value=value,
                unit=(
                    "multiple" if metric_name == "pb" else "ratio"
                ),
                formula=(
                    "market_price/book_value_per_share"
                    if metric_name == "pb"
                    else "provider_reported_ytd*12/months"
                    if metric_name == "roe"
                    else "provider_reported"
                ),
                numerator=(
                    market_price
                    if metric_name == "pb"
                    else value
                ),
                denominator=(
                    book_value
                    if metric_name == "pb"
                    else Decimal("1")
                ),
                reason=None,
                calculation_version=(
                    FINANCIAL_INSTITUTION_VERSION
                ),
                parameters={"currency": "CNY"},
                inputs=(
                    _evidence(fields["book_value_per_share"])
                    if metric_name == "pb"
                    else _evidence(fields[metric_name])
                ,),
                computed_at=computed_at,
            )
            for metric_name, value in values.items()
        )
        self.repository.upsert_results(list(results))
        return results


def _value(record: dict[str, object]) -> Decimal:
    value = record.get("normalized_value")
    if value is None:
        raise ValueError(
            f"Missing normalized value for {record.get('field_name')}"
        )
    return Decimal(str(value))


def _evidence(
    record: dict[str, object],
) -> FundamentalInputEvidence:
    return FundamentalInputEvidence(
        field_name=str(record["field_name"]),
        value=_value(record),
        report_period=str(record["report_period"]),
        source=str(record["source"]),
        unit=str(record["unit"]),
        currency=(
            str(record["currency"])
            if record.get("currency") is not None
            else None
        ),
    )
