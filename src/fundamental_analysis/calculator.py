"""Exact valuation and financial-quality calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .models import (
    FUNDAMENTAL_CALCULATION_VERSION,
    FundamentalInputEvidence,
    FundamentalMetricResult,
    FundamentalMetricSet,
    FundamentalSnapshot,
    MetricStatus,
)


class FundamentalCalculationError(ValueError):
    """Raised when core market inputs are inconsistent."""


@dataclass(frozen=True)
class FundamentalMetricParameters:
    reported_market_cap: Decimal | None = None
    market_cap_tolerance: Decimal = Decimal("0.01")
    default_tax_rate: Decimal | None = None

    def __post_init__(self) -> None:
        if self.reported_market_cap is not None:
            if self.reported_market_cap <= 0:
                raise ValueError(
                    "reported_market_cap must be positive"
                )
        if not Decimal("0") <= self.market_cap_tolerance <= Decimal("1"):
            raise ValueError(
                "market_cap_tolerance must be between 0 and 1"
            )
        if self.default_tax_rate is not None and not (
            Decimal("0") <= self.default_tax_rate <= Decimal("1")
        ):
            raise ValueError(
                "default_tax_rate must be between 0 and 1"
            )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "reported_market_cap": (
                format(self.reported_market_cap, "f")
                if self.reported_market_cap is not None
                else None
            ),
            "market_cap_tolerance": format(
                self.market_cap_tolerance,
                "f",
            ),
            "default_tax_rate": (
                format(self.default_tax_rate, "f")
                if self.default_tax_rate is not None
                else None
            ),
        }


class FundamentalMetricCalculator:
    def calculate(
        self,
        snapshot: FundamentalSnapshot,
        *,
        parameters: FundamentalMetricParameters | None = None,
    ) -> FundamentalMetricSet:
        config = parameters or FundamentalMetricParameters()
        computed_at = datetime.now(timezone.utc).isoformat()
        results: list[FundamentalMetricResult] = []
        market_cap = None
        if snapshot.shares_outstanding is None:
            results.append(
                self._missing(
                    snapshot,
                    "market_cap",
                    "CNY",
                    "market_price * shares_outstanding",
                    ("market_price", "shares_outstanding"),
                    "shares_outstanding is missing",
                    config,
                    computed_at,
                )
            )
        else:
            market_cap = (
                snapshot.market_price
                * snapshot.shares_outstanding
            )
            if config.reported_market_cap is not None:
                difference = abs(
                    market_cap - config.reported_market_cap
                ) / config.reported_market_cap
                if difference > config.market_cap_tolerance:
                    raise FundamentalCalculationError(
                        "Calculated market cap differs from reported "
                        f"market cap by {difference:.4%}"
                    )
            results.append(
                self._available(
                    snapshot,
                    "market_cap",
                    market_cap,
                    "CNY",
                    "market_price * shares_outstanding",
                    market_cap,
                    Decimal("1"),
                    ("market_price", "shares_outstanding"),
                    config,
                    computed_at,
                )
            )

        fields = snapshot.fields
        enterprise_value = None
        if market_cap is not None:
            missing_ev = [
                name
                for name in (
                    "total_debt",
                    "cash_and_equivalents",
                )
                if name not in fields
            ]
            if missing_ev:
                results.append(
                    self._missing(
                        snapshot,
                        "enterprise_value",
                        "CNY",
                        "market_cap + total_debt - cash",
                        (
                            "market_price",
                            "shares_outstanding",
                            "total_debt",
                            "cash_and_equivalents",
                        ),
                        f"Missing inputs: {', '.join(missing_ev)}",
                        config,
                        computed_at,
                    )
                )
            else:
                enterprise_value = (
                    market_cap
                    + fields["total_debt"]
                    - fields["cash_and_equivalents"]
                )
                results.append(
                    self._available(
                        snapshot,
                        "enterprise_value",
                        enterprise_value,
                        "CNY",
                        "market_cap + total_debt - cash",
                        enterprise_value,
                        Decimal("1"),
                        (
                            "market_price",
                            "shares_outstanding",
                            "total_debt",
                            "cash_and_equivalents",
                        ),
                        config,
                        computed_at,
                    )
                )

        results.extend(
            self._valuation_metrics(
                snapshot,
                market_cap,
                enterprise_value,
                config,
                computed_at,
            )
        )
        results.extend(
            self._quality_metrics(
                snapshot,
                config,
                computed_at,
            )
        )
        results.extend(self._cash_metrics(snapshot, config, computed_at))
        results.sort(key=lambda metric: metric.metric_name)
        return FundamentalMetricSet(
            snapshot=snapshot,
            metrics=tuple(results),
        )

    def _valuation_metrics(
        self,
        snapshot: FundamentalSnapshot,
        market_cap: Decimal | None,
        enterprise_value: Decimal | None,
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> list[FundamentalMetricResult]:
        results: list[FundamentalMetricResult] = []
        if market_cap is None:
            for name, denominator in (
                ("pe_ttm", "earnings"),
                ("pe_ttm_recurring", "recurring_earnings"),
                ("pb", "total_equity"),
                ("ps_ttm", "revenue"),
                ("pcf_ttm", "operating_cash_flow"),
            ):
                results.append(
                    self._missing(
                        snapshot,
                        name,
                        "multiple",
                        f"market_cap / {denominator}",
                        (
                            "market_price",
                            "shares_outstanding",
                            denominator,
                        ),
                        "market_cap is unavailable",
                        config,
                        computed_at,
                    )
                )
        else:
            for name, denominator in (
                ("pe_ttm", "earnings"),
                ("pe_ttm_recurring", "recurring_earnings"),
                ("pb", "total_equity"),
                ("ps_ttm", "revenue"),
                ("pcf_ttm", "operating_cash_flow"),
            ):
                results.append(
                    self._positive_ratio(
                        snapshot,
                        name,
                        market_cap,
                        denominator,
                        "multiple",
                        f"market_cap / {denominator}",
                        (
                            "market_price",
                            "shares_outstanding",
                            denominator,
                        ),
                        config,
                        computed_at,
                    )
                )
        if enterprise_value is None:
            for name, denominator in (
                ("ev_ebitda", "ebitda"),
                ("ev_fcf", "free_cash_flow"),
            ):
                results.append(
                    self._missing(
                        snapshot,
                        name,
                        "multiple",
                        f"enterprise_value / {denominator}",
                        (
                            "market_price",
                            "shares_outstanding",
                            "total_debt",
                            "cash_and_equivalents",
                            denominator,
                        ),
                        "enterprise_value is unavailable",
                        config,
                        computed_at,
                    )
                )
        else:
            for name, denominator in (
                ("ev_ebitda", "ebitda"),
                ("ev_fcf", "free_cash_flow"),
            ):
                results.append(
                    self._positive_ratio(
                        snapshot,
                        name,
                        enterprise_value,
                        denominator,
                        "multiple",
                        f"enterprise_value / {denominator}",
                        (
                            "market_price",
                            "shares_outstanding",
                            "total_debt",
                            "cash_and_equivalents",
                            denominator,
                        ),
                        config,
                        computed_at,
                    )
                )
        for name, numerator in (
            ("earnings_yield", "earnings"),
            ("fcf_yield", "free_cash_flow"),
            ("dividend_yield", "annual_dividends"),
        ):
            if market_cap is None:
                results.append(
                    self._missing(
                        snapshot,
                        name,
                        "ratio",
                        f"{numerator} / market_cap",
                        (
                            numerator,
                            "market_price",
                            "shares_outstanding",
                        ),
                        "market_cap is unavailable",
                        config,
                        computed_at,
                    )
                )
            else:
                results.append(
                    self._ratio(
                        snapshot,
                        name,
                        numerator,
                        market_cap,
                        "ratio",
                        f"{numerator} / market_cap",
                        (
                            numerator,
                            "market_price",
                            "shares_outstanding",
                        ),
                        config,
                        computed_at,
                    )
                )
        return results

    def _quality_metrics(
        self,
        snapshot: FundamentalSnapshot,
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> list[FundamentalMetricResult]:
        results = [
            self._average_balance_ratio(
                snapshot,
                "roe",
                "earnings",
                "total_equity",
                "earnings / average_equity",
                config,
                computed_at,
            ),
            self._average_balance_ratio(
                snapshot,
                "roa",
                "earnings",
                "total_assets",
                "earnings / average_assets",
                config,
                computed_at,
            ),
            self._average_balance_ratio(
                snapshot,
                "roe_recurring",
                "recurring_earnings",
                "total_equity",
                "recurring_earnings / average_equity",
                config,
                computed_at,
            ),
            self._positive_ratio(
                snapshot,
                "non_recurring_share",
                snapshot.fields.get("non_recurring_profit"),
                "earnings",
                "ratio",
                "non_recurring_profit / earnings",
                ("non_recurring_profit", "earnings"),
                config,
                computed_at,
            ),
        ]
        results.append(
            self._roic(snapshot, config, computed_at)
        )
        for name, numerator in (
            ("gross_margin", "gross_profit"),
            ("operating_margin", "operating_profit"),
            ("net_margin", "earnings"),
        ):
            results.append(
                self._positive_ratio(
                    snapshot,
                    name,
                    snapshot.fields.get(numerator),
                    "revenue",
                    "ratio",
                    f"{numerator} / revenue",
                    (numerator, "revenue"),
                    config,
                    computed_at,
                )
            )
        for name, field_name in (
            ("revenue_growth", "revenue"),
            ("net_profit_growth", "earnings"),
            ("recurring_profit_growth", "recurring_earnings"),
            (
                "operating_cash_flow_growth",
                "operating_cash_flow",
            ),
            ("free_cash_flow_growth", "free_cash_flow"),
        ):
            results.append(
                self._growth(
                    snapshot,
                    name,
                    field_name,
                    config,
                    computed_at,
                )
            )
        results.append(
            self._positive_ratio(
                snapshot,
                "debt_to_assets",
                snapshot.fields.get("total_liabilities"),
                "total_assets",
                "ratio",
                "total_liabilities / total_assets",
                ("total_liabilities", "total_assets"),
                config,
                computed_at,
            )
        )
        if (
            "total_debt" in snapshot.fields
            and "cash_and_equivalents" in snapshot.fields
        ):
            net_debt = (
                snapshot.fields["total_debt"]
                - snapshot.fields["cash_and_equivalents"]
            )
            results.append(
                self._available(
                    snapshot,
                    "net_debt",
                    net_debt,
                    "CNY",
                    "total_debt - cash_and_equivalents",
                    net_debt,
                    Decimal("1"),
                    ("total_debt", "cash_and_equivalents"),
                    config,
                    computed_at,
                )
            )
        else:
            results.append(
                self._missing(
                    snapshot,
                    "net_debt",
                    "CNY",
                    "total_debt - cash_and_equivalents",
                    ("total_debt", "cash_and_equivalents"),
                    "Debt or cash input is missing",
                    config,
                    computed_at,
                )
            )
        interest = snapshot.fields.get("interest_expense")
        ebit = snapshot.fields.get("ebit")
        if ebit is None or interest is None:
            results.append(
                self._missing(
                    snapshot,
                    "interest_coverage",
                    "multiple",
                    "ebit / abs(interest_expense)",
                    ("ebit", "interest_expense"),
                    "EBIT or interest expense is missing",
                    config,
                    computed_at,
                )
            )
        elif interest == 0:
            results.append(
                self._not_applicable(
                    snapshot,
                    "interest_coverage",
                    "multiple",
                    "ebit / abs(interest_expense)",
                    ("ebit", "interest_expense"),
                    "Interest expense is zero",
                    config,
                    computed_at,
                )
            )
        else:
            results.append(
                self._available(
                    snapshot,
                    "interest_coverage",
                    ebit / abs(interest),
                    "multiple",
                    "ebit / abs(interest_expense)",
                    ebit,
                    abs(interest),
                    ("ebit", "interest_expense"),
                    config,
                    computed_at,
                )
            )
        return results

    def _roic(
        self,
        snapshot: FundamentalSnapshot,
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> FundamentalMetricResult:
        required = (
            "ebit",
            "total_equity",
            "total_debt",
            "cash_and_equivalents",
        )
        missing = [
            name
            for name in required
            if name not in snapshot.fields
            or name not in snapshot.prior_fields
            and name != "ebit"
        ]
        tax_rate = snapshot.fields.get("effective_tax_rate")
        if tax_rate is None:
            tax_rate = config.default_tax_rate
        if missing or tax_rate is None:
            reason = (
                f"Missing inputs: {', '.join(missing)}"
                if missing
                else "effective_tax_rate is missing"
            )
            return self._missing(
                snapshot,
                "roic",
                "ratio",
                "nopat / average_invested_capital",
                required + ("effective_tax_rate",),
                reason,
                config,
                computed_at,
            )
        current_capital = (
            snapshot.fields["total_equity"]
            + snapshot.fields["total_debt"]
            - snapshot.fields["cash_and_equivalents"]
        )
        prior_capital = (
            snapshot.prior_fields["total_equity"]
            + snapshot.prior_fields["total_debt"]
            - snapshot.prior_fields["cash_and_equivalents"]
        )
        average_capital = (
            current_capital + prior_capital
        ) / Decimal("2")
        if average_capital <= 0:
            return self._not_applicable(
                snapshot,
                "roic",
                "ratio",
                "nopat / average_invested_capital",
                required + ("effective_tax_rate",),
                "Average invested capital is not positive",
                config,
                computed_at,
            )
        nopat = snapshot.fields["ebit"] * (
            Decimal("1") - tax_rate
        )
        return self._available(
            snapshot,
            "roic",
            nopat / average_capital,
            "ratio",
            "nopat / average_invested_capital",
            nopat,
            average_capital,
            required + ("effective_tax_rate",),
            config,
            computed_at,
        )

    def _cash_metrics(
        self,
        snapshot: FundamentalSnapshot,
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> list[FundamentalMetricResult]:
        """R&D intensity and, for cash-consuming companies, runway and reliance on financing."""

        fields = snapshot.fields
        results = [
            self._positive_ratio(
                snapshot,
                "rd_intensity",
                fields.get("research_expense"),
                "revenue",
                "ratio",
                "research_expense / revenue",
                ("research_expense", "revenue"),
                config,
                computed_at,
            )
        ]
        fcf = fields.get("free_cash_flow")
        burn_inputs = ("operating_cash_flow", "capital_expenditure")
        results.append(
            self._positive_ratio(
                snapshot,
                "cash_burn_ratio",
                None if fcf is None else -fcf,
                "cash_and_equivalents",
                "ratio",
                "cash_burn / cash_and_equivalents",
                (*burn_inputs, "cash_and_equivalents"),
                config,
                computed_at,
            )
        )
        if fcf is None:
            results.append(
                self._missing(
                    snapshot, "cash_burn", "CNY", "-(operating_cash_flow - capital_expenditure)",
                    burn_inputs, "operating_cash_flow is missing", config, computed_at,
                )
            )
        else:
            results.append(
                self._available(
                    snapshot, "cash_burn", -fcf, "CNY", "-(operating_cash_flow - capital_expenditure)",
                    -fcf, Decimal("1"), burn_inputs, config, computed_at,
                )
            )
        for name, numerator_name, formula in (
            ("cash_runway_years", "cash_and_equivalents", "cash_and_equivalents / cash_burn"),
            ("financing_dependence", "financing_cash_flow", "financing_cash_flow / cash_burn"),
        ):
            unit = "years" if name == "cash_runway_years" else "ratio"
            inputs = (numerator_name, *burn_inputs)
            numerator = fields.get(numerator_name)
            if fcf is None or numerator is None:
                results.append(
                    self._missing(
                        snapshot, name, unit, formula, inputs,
                        f"{numerator_name} or cash_burn is missing", config, computed_at,
                    )
                )
            elif fcf >= 0:
                results.append(
                    self._not_applicable(
                        snapshot, name, unit, formula, inputs,
                        "free cash flow is not negative; the company does not burn cash", config, computed_at,
                    )
                )
            else:
                results.append(
                    self._available(
                        snapshot, name, numerator / -fcf, unit, formula,
                        numerator, -fcf, inputs, config, computed_at,
                    )
                )
        return results

    def _average_balance_ratio(
        self,
        snapshot: FundamentalSnapshot,
        name: str,
        numerator_name: str,
        balance_name: str,
        formula: str,
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> FundamentalMetricResult:
        numerator = snapshot.fields.get(numerator_name)
        current = snapshot.fields.get(balance_name)
        prior = snapshot.prior_fields.get(balance_name)
        if numerator is None or current is None or prior is None:
            return self._missing(
                snapshot,
                name,
                "ratio",
                formula,
                (numerator_name, balance_name),
                "Current or prior balance input is missing",
                config,
                computed_at,
            )
        average = (current + prior) / Decimal("2")
        if average <= 0:
            return self._not_applicable(
                snapshot,
                name,
                "ratio",
                formula,
                (numerator_name, balance_name),
                "Average balance is not positive",
                config,
                computed_at,
            )
        return self._available(
            snapshot,
            name,
            numerator / average,
            "ratio",
            formula,
            numerator,
            average,
            (numerator_name, balance_name),
            config,
            computed_at,
        )

    def _growth(
        self,
        snapshot: FundamentalSnapshot,
        name: str,
        field_name: str,
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> FundamentalMetricResult:
        current = snapshot.fields.get(field_name)
        prior = snapshot.prior_fields.get(field_name)
        if current is None or prior is None:
            return self._missing(
                snapshot,
                name,
                "ratio",
                f"{field_name} / prior_{field_name} - 1",
                (field_name,),
                "Current or prior input is missing",
                config,
                computed_at,
            )
        if prior <= 0:
            return self._not_applicable(
                snapshot,
                name,
                "ratio",
                f"{field_name} / prior_{field_name} - 1",
                (field_name,),
                "Prior value is not positive",
                config,
                computed_at,
            )
        return self._available(
            snapshot,
            name,
            current / prior - Decimal("1"),
            "ratio",
            f"{field_name} / prior_{field_name} - 1",
            current - prior,
            prior,
            (field_name,),
            config,
            computed_at,
        )

    def _positive_ratio(
        self,
        snapshot: FundamentalSnapshot,
        name: str,
        numerator: Decimal | None,
        denominator_name: str,
        unit: str,
        formula: str,
        inputs: tuple[str, ...],
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> FundamentalMetricResult:
        denominator = snapshot.fields.get(denominator_name)
        if numerator is None or denominator is None:
            return self._missing(
                snapshot,
                name,
                unit,
                formula,
                inputs,
                f"{denominator_name} or numerator is missing",
                config,
                computed_at,
            )
        if denominator <= 0:
            return self._not_applicable(
                snapshot,
                name,
                unit,
                formula,
                inputs,
                f"{denominator_name} is not positive",
                config,
                computed_at,
            )
        return self._available(
            snapshot,
            name,
            numerator / denominator,
            unit,
            formula,
            numerator,
            denominator,
            inputs,
            config,
            computed_at,
        )

    def _ratio(
        self,
        snapshot: FundamentalSnapshot,
        name: str,
        numerator_name: str,
        denominator: Decimal,
        unit: str,
        formula: str,
        inputs: tuple[str, ...],
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> FundamentalMetricResult:
        numerator = snapshot.fields.get(numerator_name)
        if numerator is None:
            return self._missing(
                snapshot,
                name,
                unit,
                formula,
                inputs,
                f"{numerator_name} is missing",
                config,
                computed_at,
            )
        return self._available(
            snapshot,
            name,
            numerator / denominator,
            unit,
            formula,
            numerator,
            denominator,
            inputs
            + ("market_price", "shares_outstanding"),
            config,
            computed_at,
        )

    def _available(
        self,
        snapshot: FundamentalSnapshot,
        name: str,
        value: Decimal,
        unit: str,
        formula: str,
        numerator: Decimal,
        denominator: Decimal,
        input_names: tuple[str, ...],
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> FundamentalMetricResult:
        return self._result(
            snapshot,
            name,
            MetricStatus.AVAILABLE,
            value,
            unit,
            formula,
            numerator,
            denominator,
            None,
            input_names,
            config,
            computed_at,
        )

    def _missing(
        self,
        snapshot: FundamentalSnapshot,
        name: str,
        unit: str,
        formula: str,
        input_names: tuple[str, ...],
        reason: str,
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> FundamentalMetricResult:
        return self._result(
            snapshot,
            name,
            MetricStatus.MISSING_INPUT,
            None,
            unit,
            formula,
            None,
            None,
            reason,
            input_names,
            config,
            computed_at,
        )

    def _not_applicable(
        self,
        snapshot: FundamentalSnapshot,
        name: str,
        unit: str,
        formula: str,
        input_names: tuple[str, ...],
        reason: str,
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> FundamentalMetricResult:
        return self._result(
            snapshot,
            name,
            MetricStatus.NOT_APPLICABLE,
            None,
            unit,
            formula,
            None,
            None,
            reason,
            input_names,
            config,
            computed_at,
        )

    def _result(
        self,
        snapshot: FundamentalSnapshot,
        name: str,
        status: MetricStatus,
        value: Decimal | None,
        unit: str,
        formula: str,
        numerator: Decimal | None,
        denominator: Decimal | None,
        reason: str | None,
        input_names: tuple[str, ...],
        config: FundamentalMetricParameters,
        computed_at: str,
    ) -> FundamentalMetricResult:
        return FundamentalMetricResult(
            asset_id=snapshot.asset_id,
            metric_name=name,
            as_of_date=snapshot.as_of_date,
            report_period=snapshot.report_period,
            status=status,
            value=value,
            unit=unit,
            formula=formula,
            numerator=numerator,
            denominator=denominator,
            reason=reason,
            calculation_version=FUNDAMENTAL_CALCULATION_VERSION,
            parameters={
                **config.to_dict(),
                "market_price": format(
                    snapshot.market_price,
                    "f",
                ),
                "shares_outstanding": (
                    format(snapshot.shares_outstanding, "f")
                    if snapshot.shares_outstanding is not None
                    else None
                ),
                "currency": snapshot.currency,
                "flow_basis": snapshot.flow_basis,
            },
            inputs=_inputs(snapshot, input_names),
            computed_at=computed_at,
        )


def _inputs(
    snapshot: FundamentalSnapshot,
    names: tuple[str, ...],
) -> tuple[FundamentalInputEvidence, ...]:
    result: list[FundamentalInputEvidence] = []
    for name in names:
        if name == "market_price":
            result.append(
                FundamentalInputEvidence(
                    field_name="market_price",
                    value=snapshot.market_price,
                    report_period=snapshot.as_of_date,
                    source="market_data",
                    unit=f"{snapshot.currency}_per_share",
                    currency=snapshot.currency,
                )
            )
        elif (
            name == "shares_outstanding"
            and snapshot.shares_outstanding is not None
        ):
            result.extend(
                snapshot.evidence.get(
                    "shares_outstanding",
                    (
                        FundamentalInputEvidence(
                            field_name="shares_outstanding",
                            value=snapshot.shares_outstanding,
                            report_period=snapshot.report_period,
                            source="financial_data",
                            unit="shares",
                        ),
                    ),
                )
            )
        else:
            result.extend(snapshot.evidence.get(name, ()))
            result.extend(snapshot.prior_evidence.get(name, ()))
    unique: dict[
        tuple[str, str, str, str],
        FundamentalInputEvidence,
    ] = {}
    for item in result:
        unique[
            (
                item.field_name,
                item.report_period,
                item.source,
                format(item.value, "f"),
            )
        ] = item
    return tuple(unique.values())
