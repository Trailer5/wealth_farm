"""Assemble TTM and point-in-time inputs from financial bundles."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from src.financial_data import (
    DATASET_STOCK_BALANCE_SHEETS,
    DATASET_STOCK_CASH_FLOW_STATEMENTS,
    DATASET_STOCK_FINANCIAL_INDICATORS,
    DATASET_STOCK_INCOME_STATEMENTS,
    FinancialResearchBundle,
    ttm_input_periods,
)

from .models import (
    FundamentalInputEvidence,
    FundamentalSnapshot,
)


class FundamentalSnapshotError(ValueError):
    """Raised when a financial bundle cannot form a snapshot."""


class FundamentalSnapshotAssembler:
    def assemble(
        self,
        bundle: FinancialResearchBundle,
        *,
        market_price: Decimal,
        shares_override: Decimal | None = None,
        currency: str = "CNY",
        target_report_period: str | None = None,
    ) -> FundamentalSnapshot:
        if market_price <= 0:
            raise FundamentalSnapshotError(
                "market_price must be positive"
            )
        index = _FinancialRecordIndex(
            bundle.records_by_dataset,
            as_of_date=bundle.as_of_date,
        )
        report_period = target_report_period or index.latest_period(
            DATASET_STOCK_INCOME_STATEMENTS
        )
        if report_period is None:
            raise FundamentalSnapshotError(
                "Income statement records are required"
            )
        if (
            target_report_period is not None
            and target_report_period
            not in index.periods(DATASET_STOCK_INCOME_STATEMENTS)
        ):
            raise FundamentalSnapshotError(
                "target_report_period is not available"
            )
        if report_period > bundle.as_of_date:
            raise FundamentalSnapshotError(
                "report_period exceeds bundle as_of_date"
            )

        fields: dict[str, Decimal] = {}
        prior_fields: dict[str, Decimal] = {}
        evidence: dict[
            str,
            tuple[FundamentalInputEvidence, ...],
        ] = {}
        prior_evidence: dict[
            str,
            tuple[FundamentalInputEvidence, ...],
        ] = {}
        for dataset in (
            DATASET_STOCK_INCOME_STATEMENTS,
            DATASET_STOCK_CASH_FLOW_STATEMENTS,
        ):
            for field_name in index.fields(dataset):
                result = index.ttm_value(
                    dataset,
                    field_name,
                    report_period,
                )
                if result is not None:
                    fields[field_name], evidence[field_name] = result
                prior_period = _prior_period(report_period)
                prior_result = index.ttm_value(
                    dataset,
                    field_name,
                    prior_period,
                )
                if prior_result is not None:
                    prior_fields[field_name] = prior_result[0]
                    prior_evidence[field_name] = prior_result[1]

        for dataset in (
            DATASET_STOCK_BALANCE_SHEETS,
            DATASET_STOCK_FINANCIAL_INDICATORS,
        ):
            for field_name in index.fields(dataset):
                current = index.exact_value(
                    dataset,
                    field_name,
                    report_period,
                )
                if current is not None:
                    fields[field_name], evidence[field_name] = current
                prior = index.exact_value(
                    dataset,
                    field_name,
                    _prior_period(report_period),
                )
                if prior is not None:
                    prior_fields[field_name] = prior[0]
                    prior_evidence[field_name] = prior[1]

        _apply_alias(
            fields,
            evidence,
            "revenue",
            ("total_revenue", "revenue"),
        )
        _apply_alias(
            fields,
            evidence,
            "earnings",
            ("parent_net_profit", "net_profit"),
        )
        _derive_fields(fields, evidence)
        _derive_prior_fields(prior_fields, prior_evidence)
        _validate_currencies(evidence, currency)

        shares = shares_override
        if shares is None:
            shares = fields.get("shares_outstanding")
        if shares is not None and shares <= 0:
            raise FundamentalSnapshotError(
                "shares_outstanding must be positive"
            )
        if shares_override is not None:
            evidence["shares_outstanding"] = (
                FundamentalInputEvidence(
                    field_name="shares_outstanding",
                    value=shares_override,
                    report_period=report_period,
                    source="user_override",
                    unit="shares",
                ),
            )
        return FundamentalSnapshot(
            asset_id=bundle.asset.asset_id,
            as_of_date=bundle.as_of_date,
            report_period=report_period,
            currency=currency,
            market_price=market_price,
            shares_outstanding=shares,
            fields=fields,
            prior_fields=prior_fields,
            evidence=evidence,
            prior_evidence=prior_evidence,
            flow_basis=(
                "annual"
                if report_period.endswith("-12-31")
                else "ttm"
            ),
        )


class _FinancialRecordIndex:
    def __init__(
        self,
        records_by_dataset: dict[
            str,
            tuple[dict[str, Any], ...],
        ],
        *,
        as_of_date: str,
    ) -> None:
        date.fromisoformat(as_of_date)
        self._records: dict[
            tuple[str, str, str],
            dict[str, Any],
        ] = {}
        self._fields: dict[str, set[str]] = {}
        for dataset, records in records_by_dataset.items():
            for record in records:
                published_at = str(
                    record.get("published_at") or ""
                ).strip()
                if not published_at or published_at > as_of_date:
                    continue
                date.fromisoformat(published_at)
                key = (
                    dataset,
                    str(record["field_name"]),
                    str(record["report_period"]),
                )
                current = self._records.get(key)
                if (
                    current is None
                    or int(record.get("revision", 1))
                    > int(current.get("revision", 1))
                ):
                    self._records[key] = dict(record)
                self._fields.setdefault(dataset, set()).add(
                    str(record["field_name"])
                )

    def fields(self, dataset: str) -> tuple[str, ...]:
        return tuple(sorted(self._fields.get(dataset, set())))

    def latest_period(self, dataset: str) -> str | None:
        periods = self.periods(dataset)
        return max(periods) if periods else None

    def periods(self, dataset: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    period
                    for current_dataset, _, period in self._records
                    if current_dataset == dataset
                }
            )
        )

    def exact_value(
        self,
        dataset: str,
        field_name: str,
        report_period: str,
    ) -> tuple[
        Decimal,
        tuple[FundamentalInputEvidence, ...],
    ] | None:
        record = self._records.get(
            (dataset, field_name, report_period)
        )
        if record is None:
            return None
        value = Decimal(str(record["normalized_value"]))
        return value, (_evidence(record),)

    def ttm_value(
        self,
        dataset: str,
        field_name: str,
        report_period: str,
    ) -> tuple[
        Decimal,
        tuple[FundamentalInputEvidence, ...],
    ] | None:
        try:
            components = ttm_input_periods(report_period)
        except ValueError:
            return None
        current = self.exact_value(
            dataset,
            field_name,
            components.current_cumulative_period,
        )
        if current is None:
            return None
        if components.prior_comparable_period is None:
            return current
        annual = self.exact_value(
            dataset,
            field_name,
            components.previous_annual_period,
        )
        comparable = self.exact_value(
            dataset,
            field_name,
            components.prior_comparable_period,
        )
        if annual is None or comparable is None:
            return None
        return (
            annual[0] + current[0] - comparable[0],
            annual[1] + current[1] + comparable[1],
        )


def _evidence(record: dict[str, Any]) -> FundamentalInputEvidence:
    return FundamentalInputEvidence(
        field_name=str(record["field_name"]),
        value=Decimal(str(record["normalized_value"])),
        report_period=str(record["report_period"]),
        source=str(record["source"]),
        unit=str(record["unit"]),
        currency=(
            str(record["currency"])
            if record.get("currency") is not None
            else None
        ),
    )


def _prior_period(report_period: str) -> str:
    parsed = date.fromisoformat(report_period)
    return date(
        parsed.year - 1,
        parsed.month,
        parsed.day,
    ).isoformat()


def _apply_alias(
    fields: dict[str, Decimal],
    evidence: dict[str, tuple[FundamentalInputEvidence, ...]],
    target: str,
    candidates: tuple[str, ...],
) -> None:
    for candidate in candidates:
        if candidate in fields:
            fields[target] = fields[candidate]
            evidence[target] = evidence[candidate]
            return


def _derive_fields(
    fields: dict[str, Decimal],
    evidence: dict[str, tuple[FundamentalInputEvidence, ...]],
) -> None:
    debt_parts = [
        name
        for name in (
            "short_term_debt",
            "long_term_debt",
            "bonds_payable",
            "current_portion_long_debt",
        )
        if name in fields
    ]
    if debt_parts:
        fields["total_debt"] = sum(
            (fields[name] for name in debt_parts),
            Decimal("0"),
        )
        evidence["total_debt"] = tuple(
            item
            for name in debt_parts
            for item in evidence.get(name, ())
        )
    if (
        "revenue" in fields
        and "operating_cost" in fields
    ):
        fields["gross_profit"] = (
            fields["revenue"] - fields["operating_cost"]
        )
        evidence["gross_profit"] = (
            evidence.get("revenue", ())
            + evidence.get("operating_cost", ())
        )
    if fields.get("revenue", Decimal("0")) > 0:
        for margin_name, numerator_name in (
            ("gross_margin", "gross_profit"),
            ("operating_margin", "operating_profit"),
            ("net_margin", "earnings"),
        ):
            if numerator_name in fields:
                fields[margin_name] = (
                    fields[numerator_name] / fields["revenue"]
                )
                evidence[margin_name] = (
                    evidence.get(numerator_name, ())
                    + evidence.get("revenue", ())
                )
    if "ebit" not in fields and "operating_profit" in fields:
        fields["ebit"] = fields["operating_profit"]
        evidence["ebit"] = evidence.get(
            "operating_profit",
            (),
        )
    if (
        "ebitda" not in fields
        and "ebit" in fields
        and "depreciation_amortization" in fields
    ):
        fields["ebitda"] = (
            fields["ebit"]
            + abs(fields["depreciation_amortization"])
        )
        evidence["ebitda"] = (
            evidence.get("ebit", ())
            + evidence.get("depreciation_amortization", ())
        )
    if "operating_cash_flow" in fields:
        capex = abs(fields.get("capital_expenditure", Decimal("0")))
        fields["free_cash_flow"] = (
            fields["operating_cash_flow"] - capex
        )
        evidence["free_cash_flow"] = (
            evidence.get("operating_cash_flow", ())
            + evidence.get("capital_expenditure", ())
        )
    if (
        "income_tax" in fields
        and fields.get("pretax_profit", Decimal("0")) > 0
    ):
        fields["effective_tax_rate"] = (
            fields["income_tax"] / fields["pretax_profit"]
        )
        evidence["effective_tax_rate"] = (
            evidence.get("income_tax", ())
            + evidence.get("pretax_profit", ())
        )


def _derive_prior_fields(
    fields: dict[str, Decimal],
    evidence: dict[str, tuple[FundamentalInputEvidence, ...]],
) -> None:
    if "total_revenue" in fields and "revenue" not in fields:
        fields["revenue"] = fields["total_revenue"]
        evidence["revenue"] = evidence.get("total_revenue", ())
    if "parent_net_profit" in fields:
        fields["earnings"] = fields["parent_net_profit"]
        evidence["earnings"] = evidence.get(
            "parent_net_profit",
            (),
        )
    elif "net_profit" in fields:
        fields["earnings"] = fields["net_profit"]
        evidence["earnings"] = evidence.get("net_profit", ())
    if "operating_cash_flow" in fields:
        fields["free_cash_flow"] = (
            fields["operating_cash_flow"]
            - abs(fields.get("capital_expenditure", Decimal("0")))
        )
        evidence["free_cash_flow"] = (
            evidence.get("operating_cash_flow", ())
            + evidence.get("capital_expenditure", ())
        )
    debt_parts = [
        fields[name]
        for name in (
            "short_term_debt",
            "long_term_debt",
            "bonds_payable",
            "current_portion_long_debt",
        )
        if name in fields
    ]
    if debt_parts:
        fields["total_debt"] = sum(
            debt_parts,
            Decimal("0"),
        )
        evidence["total_debt"] = tuple(
            item
            for name in (
                "short_term_debt",
                "long_term_debt",
                "bonds_payable",
                "current_portion_long_debt",
            )
            for item in evidence.get(name, ())
        )


def _validate_currencies(
    evidence: dict[str, tuple[FundamentalInputEvidence, ...]],
    expected_currency: str,
) -> None:
    currencies = {
        item.currency
        for items in evidence.values()
        for item in items
        if item.currency is not None
    }
    if currencies and currencies != {expected_currency}:
        raise FundamentalSnapshotError(
            f"Financial currencies {sorted(currencies)} do not "
            f"match market currency {expected_currency}"
        )
