"""Point-in-time index fundamentals and Shenwan industry assessments."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping

from .industry_contribution import (
    IndustryContribution,
    IndustryContributionCalculator,
    IndustryMarketMetric,
)


ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class IndexValuationSnapshot:
    """Normalized index valuation values; yields use ratio units."""

    asset_id: str
    as_of: str
    pe: Decimal | None
    pb: Decimal | None
    dividend_yield: Decimal | None
    available_at: str
    methodology: str
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.asset_id, "asset_id")
        _parse_date(self.as_of, "as_of")
        _parse_date(self.available_at, "available_at")
        _validate_optional_decimal(self.pe, "pe", positive=True)
        _validate_optional_decimal(self.pb, "pb", positive=True)
        _validate_optional_decimal(
            self.dividend_yield,
            "dividend_yield",
            non_negative=True,
        )
        _require_text(self.methodology, "methodology")
        object.__setattr__(
            self,
            "sources",
            _validate_sources(self.sources),
        )


@dataclass(frozen=True)
class MacroObservation:
    """A macro value with an explicit point-in-time availability date."""

    name: str
    reference_period: str
    available_at: str
    value: Decimal
    unit: str
    source: str

    def __post_init__(self) -> None:
        _require_text(self.name, "name")
        _require_text(self.reference_period, "reference_period")
        _parse_date(self.available_at, "available_at")
        _validate_decimal(self.value, "value")
        if self.unit.strip().lower() not in {"ratio", "percent", "%"}:
            raise ValueError("unit must be ratio, percent, or %")
        _require_text(self.source, "source")

    @property
    def ratio_value(self) -> Decimal:
        if self.unit.strip().lower() == "ratio":
            return self.value
        return self.value / HUNDRED

    @classmethod
    def from_current_cpi_record(
        cls,
        record: Mapping[str, object],
    ) -> "MacroObservation":
        if record.get("historical_backtest_eligible") is not False:
            raise ValueError(
                "current CPI record must be marked ineligible for backtest"
            )
        return cls(
            name="china_cpi_yoy",
            reference_period=str(record.get("reference_period") or ""),
            available_at=str(record.get("available_at") or ""),
            value=_required_decimal(
                record.get("cpi_yoy_percent"),
                "cpi_yoy_percent",
            ),
            unit="percent",
            source=str(record.get("source") or ""),
        )

    @classmethod
    def from_ten_year_yield_record(
        cls,
        record: Mapping[str, object],
    ) -> "MacroObservation":
        trade_date = str(record.get("trade_date") or "")
        return cls(
            name="china_10y_government_yield",
            reference_period=trade_date,
            available_at=trade_date,
            value=_required_decimal(
                record.get("ten_year_yield_percent"),
                "ten_year_yield_percent",
            ),
            unit="percent",
            source=str(record.get("source") or ""),
        )


@dataclass(frozen=True)
class IndexFundamentalAssessment:
    """Derived index fundamentals; every calculated yield is a ratio."""

    asset_id: str
    as_of: str
    earnings_yield: Decimal | None
    dividend_yield: Decimal | None
    pb: Decimal | None
    shareholder_yield_proxy: Decimal | None
    real_yield_cpi_proxy: Decimal | None
    equity_risk_premium: Decimal | None
    methodology: str
    formulae: tuple[tuple[str, str], ...]
    macro_observations: tuple[MacroObservation, ...]
    sources: tuple[str, ...]


class IndexFundamentalCalculator:
    """Calculate transparent point-in-time index fundamental proxies."""

    def assess(
        self,
        valuation: IndexValuationSnapshot,
        *,
        as_of: str | None = None,
        ten_year_yield: MacroObservation | None = None,
        cpi: MacroObservation | None = None,
    ) -> IndexFundamentalAssessment:
        assessment_date = as_of or valuation.as_of
        cutoff = _parse_date(assessment_date, "as_of")
        if _parse_date(valuation.as_of, "valuation.as_of") > cutoff:
            raise ValueError("valuation observation must not exceed as_of")
        if _parse_date(
            valuation.available_at,
            "valuation.available_at",
        ) > cutoff:
            raise ValueError(
                "valuation available_at must not be later than as_of"
            )
        for observation in (ten_year_yield, cpi):
            if observation is not None and _parse_date(
                observation.available_at,
                f"{observation.name}.available_at",
            ) > cutoff:
                raise ValueError(
                    f"future macro observation rejected: {observation.name}"
                )

        earnings_yield = (
            ONE / valuation.pe if valuation.pe is not None else None
        )
        shareholder_yield_proxy = (
            earnings_yield + valuation.dividend_yield
            if earnings_yield is not None
            and valuation.dividend_yield is not None
            else None
        )
        ten_year_ratio = (
            ten_year_yield.ratio_value
            if ten_year_yield is not None
            else None
        )
        cpi_ratio = cpi.ratio_value if cpi is not None else None
        real_yield_cpi_proxy = (
            ten_year_ratio - cpi_ratio
            if ten_year_ratio is not None and cpi_ratio is not None
            else None
        )
        equity_risk_premium = (
            earnings_yield - ten_year_ratio
            if earnings_yield is not None and ten_year_ratio is not None
            else None
        )
        formulae = (
            ("earnings_yield", "1 / PE; ratio"),
            (
                "shareholder_yield_proxy",
                "earnings_yield + dividend_yield; proxy only; ratio",
            ),
            (
                "real_yield_cpi_proxy",
                "10Y government yield - CPI; proxy only; ratio",
            ),
            (
                "equity_risk_premium",
                "earnings_yield - 10Y government yield; ratio",
            ),
        )
        macro_sources = tuple(
            observation.source
            for observation in (ten_year_yield, cpi)
            if observation is not None
        )
        return IndexFundamentalAssessment(
            asset_id=valuation.asset_id,
            as_of=assessment_date,
            earnings_yield=earnings_yield,
            dividend_yield=valuation.dividend_yield,
            pb=valuation.pb,
            shareholder_yield_proxy=shareholder_yield_proxy,
            real_yield_cpi_proxy=real_yield_cpi_proxy,
            equity_risk_premium=equity_risk_premium,
            methodology=valuation.methodology,
            formulae=formulae,
            macro_observations=tuple(
                observation
                for observation in (ten_year_yield, cpi)
                if observation is not None
            ),
            sources=_deduplicate((*valuation.sources, *macro_sources)),
        )


@dataclass(frozen=True)
class IndexFundamentalTrendPoint:
    as_of: str
    earnings_yield: Decimal | None
    pb: Decimal | None
    dividend_yield: Decimal | None


@dataclass(frozen=True)
class IndexFundamentalTrend:
    asset_id: str
    methodology: str
    start_date: str
    end_date: str
    points: tuple[IndexFundamentalTrendPoint, ...]
    earnings_yield_change: Decimal | None
    pb_change: Decimal | None
    dividend_yield_change: Decimal | None
    sources: tuple[str, ...]


class IndexFundamentalTrendCalculator:
    """Describe one valuation methodology through time."""

    def calculate(
        self,
        snapshots: tuple[IndexValuationSnapshot, ...],
        *,
        as_of: str,
    ) -> IndexFundamentalTrend:
        cutoff = _parse_date(as_of, "as_of")
        if len(snapshots) < 2:
            raise ValueError("trend requires at least two snapshots")
        asset_ids = {item.asset_id for item in snapshots}
        methodologies = {item.methodology for item in snapshots}
        if len(asset_ids) != 1 or len(methodologies) != 1:
            raise ValueError(
                "trend snapshots must share asset_id and methodology"
            )
        ordered = tuple(sorted(snapshots, key=lambda item: item.as_of))
        dates = [item.as_of for item in ordered]
        if len(set(dates)) != len(dates):
            raise ValueError("trend snapshot dates must be unique")
        for item in ordered:
            if _parse_date(item.as_of, "snapshot.as_of") > cutoff:
                raise ValueError("future valuation snapshot rejected")
            if _parse_date(
                item.available_at,
                "snapshot.available_at",
            ) > cutoff:
                raise ValueError("unavailable valuation snapshot rejected")
        points = tuple(
            IndexFundamentalTrendPoint(
                as_of=item.as_of,
                earnings_yield=ONE / item.pe if item.pe is not None else None,
                pb=item.pb,
                dividend_yield=item.dividend_yield,
            )
            for item in ordered
        )
        first, last = points[0], points[-1]
        return IndexFundamentalTrend(
            asset_id=ordered[0].asset_id,
            methodology=ordered[0].methodology,
            start_date=first.as_of,
            end_date=last.as_of,
            points=points,
            earnings_yield_change=_optional_change(
                first.earnings_yield,
                last.earnings_yield,
            ),
            pb_change=_optional_change(first.pb, last.pb),
            dividend_yield_change=_optional_change(
                first.dividend_yield,
                last.dividend_yield,
            ),
            sources=_deduplicate(
                source for item in ordered for source in item.sources
            ),
        )


@dataclass(frozen=True)
class IndexValuationMethodRow:
    methodology: str
    pe: Decimal | None
    earnings_yield: Decimal | None
    pb: Decimal | None
    dividend_yield: Decimal | None
    sources: tuple[str, ...]


@dataclass(frozen=True)
class IndexValuationMethodComparison:
    asset_id: str
    as_of: str
    rows: tuple[IndexValuationMethodRow, ...]
    pe_range: Decimal | None
    earnings_yield_range: Decimal | None
    pb_range: Decimal | None
    dividend_yield_range: Decimal | None
    limitation: str


class IndexValuationMethodComparisonCalculator:
    """Compare aligned methods without selecting one as ground truth."""

    def compare(
        self,
        snapshots: tuple[IndexValuationSnapshot, ...],
    ) -> IndexValuationMethodComparison:
        if len(snapshots) < 2:
            raise ValueError("method comparison requires two snapshots")
        asset_ids = {item.asset_id for item in snapshots}
        as_of_dates = {item.as_of for item in snapshots}
        methodologies = {item.methodology for item in snapshots}
        if len(asset_ids) != 1 or len(as_of_dates) != 1:
            raise ValueError(
                "method comparison requires one asset and observation date"
            )
        if len(methodologies) != len(snapshots):
            raise ValueError("methodologies must be unique")
        as_of = next(iter(as_of_dates))
        for item in snapshots:
            if item.available_at > as_of:
                raise ValueError("future methodology observation rejected")
        rows = tuple(
            IndexValuationMethodRow(
                methodology=item.methodology,
                pe=item.pe,
                earnings_yield=(
                    ONE / item.pe if item.pe is not None else None
                ),
                pb=item.pb,
                dividend_yield=item.dividend_yield,
                sources=item.sources,
            )
            for item in sorted(
                snapshots,
                key=lambda value: value.methodology,
            )
        )
        return IndexValuationMethodComparison(
            asset_id=next(iter(asset_ids)),
            as_of=as_of,
            rows=rows,
            pe_range=_value_range(row.pe for row in rows),
            earnings_yield_range=_value_range(
                row.earnings_yield for row in rows
            ),
            pb_range=_value_range(row.pb for row in rows),
            dividend_yield_range=_value_range(
                row.dividend_yield for row in rows
            ),
            limitation=(
                "Method differences reflect weighting and constituent "
                "treatment; no method is designated as ground truth."
            ),
        )


@dataclass(frozen=True)
class IndustryFundamentalSnapshot:
    """Normalized Shenwan current-cross-section values in ratio units."""

    industry_code: str
    industry_name: str
    trade_date: str
    return_ratio: Decimal
    float_market_cap: Decimal
    pe: Decimal | None
    pb: Decimal | None
    dividend_yield: Decimal | None
    amount_share: Decimal | None
    methodology: str
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_text(self.industry_code, "industry_code")
        _require_text(self.industry_name, "industry_name")
        _parse_date(self.trade_date, "trade_date")
        _validate_decimal(self.return_ratio, "return_ratio")
        _validate_decimal(
            self.float_market_cap,
            "float_market_cap",
            positive=True,
        )
        _validate_optional_decimal(self.pe, "pe", positive=True)
        _validate_optional_decimal(self.pb, "pb", positive=True)
        _validate_optional_decimal(
            self.dividend_yield,
            "dividend_yield",
            non_negative=True,
        )
        _validate_optional_decimal(
            self.amount_share,
            "amount_share",
            non_negative=True,
        )
        _require_text(self.methodology, "methodology")
        object.__setattr__(
            self,
            "sources",
            _validate_sources(self.sources),
        )

    @classmethod
    def from_sw_record(
        cls,
        record: Mapping[str, object],
    ) -> "IndustryFundamentalSnapshot":
        """Normalize one existing provider row without changing the provider."""

        source = str(record.get("source") or "")
        return cls(
            industry_code=str(record.get("index_code") or ""),
            industry_name=str(record.get("index_name") or ""),
            trade_date=str(record.get("trade_date") or ""),
            return_ratio=_required_decimal(
                record.get("return_percent"),
                "return_percent",
            )
            / HUNDRED,
            float_market_cap=_required_decimal(
                record.get("float_market_cap"),
                "float_market_cap",
            ),
            pe=_optional_decimal(record.get("pe")),
            pb=_optional_decimal(record.get("pb")),
            dividend_yield=_optional_percent_ratio(
                record.get("dividend_yield_percent")
            ),
            amount_share=_optional_percent_ratio(
                record.get("amount_share_percent")
            ),
            methodology="shenwan_market_representation_current_cross_section",
            sources=(source,),
        )


@dataclass(frozen=True)
class IndustryMetricRanking:
    metric: str
    order: str
    industry_codes: tuple[str, ...]


@dataclass(frozen=True)
class MarketIndustryAssessment:
    """Current market cross-section, not a specific index attribution."""

    as_of: str
    snapshots: tuple[IndustryFundamentalSnapshot, ...]
    contributions: tuple[IndustryContribution, ...]
    rankings: tuple[IndustryMetricRanking, ...]
    methodology: str
    sources: tuple[str, ...]
    limitation: str

    def ranking(self, metric: str) -> IndustryMetricRanking:
        for ranking in self.rankings:
            if ranking.metric == metric:
                return ranking
        raise KeyError(metric)


class MarketIndustryAssessmentCalculator:
    """Combine current Shenwan metrics with prior-cap contribution weights."""

    def __init__(
        self,
        contribution_calculator: IndustryContributionCalculator | None = None,
    ) -> None:
        self.contribution_calculator = (
            contribution_calculator or IndustryContributionCalculator()
        )

    def assess(
        self,
        prior: tuple[IndustryFundamentalSnapshot, ...],
        current: tuple[IndustryFundamentalSnapshot, ...],
    ) -> MarketIndustryAssessment:
        prior_metrics = tuple(_to_contribution_metric(item) for item in prior)
        current_metrics = tuple(
            _to_contribution_metric(item) for item in current
        )
        contributions = self.contribution_calculator.calculate(
            prior_metrics,
            current_metrics,
        )
        current_by_code = {
            item.industry_code: item for item in current
        }
        if len(current_by_code) != len(current):
            raise ValueError("duplicate current industry_code")
        as_of = contributions[0].trade_date
        if any(item.trade_date != as_of for item in current):
            raise ValueError("current snapshots must contain one trade date")

        contribution_values = {
            item.industry_code: item.contribution
            for item in contributions
        }
        rankings = (
            _rank("contribution", "descending", contribution_values),
            _rank_optional("pe", "ascending", current_by_code, "pe"),
            _rank_optional("pb", "ascending", current_by_code, "pb"),
            _rank_optional(
                "dividend_yield",
                "descending",
                current_by_code,
                "dividend_yield",
            ),
            _rank_optional(
                "amount_share",
                "descending",
                current_by_code,
                "amount_share",
            ),
        )
        sources = _deduplicate(
            source
            for item in (*prior, *current)
            for source in item.sources
        )
        return MarketIndustryAssessment(
            as_of=as_of,
            snapshots=tuple(current),
            contributions=contributions,
            rankings=rankings,
            methodology=(
                "prior-day common-industry float-cap weights times current "
                "industry return; PE/PB ascending and yield/amount share/"
                "contribution descending"
            ),
            sources=sources,
            limitation=(
                "Current Shenwan market cross-section only; it is not "
                "historical attribution for any specific index."
            ),
        )


def _to_contribution_metric(
    item: IndustryFundamentalSnapshot,
) -> IndustryMarketMetric:
    return IndustryMarketMetric(
        industry_code=item.industry_code,
        industry_name=item.industry_name,
        trade_date=item.trade_date,
        return_ratio=item.return_ratio,
        float_market_cap=item.float_market_cap,
    )


def _rank_optional(
    metric: str,
    order: str,
    snapshots: dict[str, IndustryFundamentalSnapshot],
    attribute: str,
) -> IndustryMetricRanking:
    values = {
        code: value
        for code, snapshot in snapshots.items()
        if (value := getattr(snapshot, attribute)) is not None
    }
    return _rank(metric, order, values)


def _rank(
    metric: str,
    order: str,
    values: dict[str, Decimal],
) -> IndustryMetricRanking:
    if order not in {"ascending", "descending"}:
        raise ValueError("ranking order must be ascending or descending")
    ranked = sorted(
        values,
        key=lambda code: (
            -values[code] if order == "descending" else values[code],
            code,
        ),
    )
    return IndustryMetricRanking(metric, order, tuple(ranked))


def _parse_date(value: str, field_name: str) -> date:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO date") from exc


def _require_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")


def _validate_decimal(
    value: Decimal,
    field_name: str,
    *,
    positive: bool = False,
) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be a finite Decimal")
    if positive and value <= ZERO:
        raise ValueError(f"{field_name} must be positive")


def _validate_optional_decimal(
    value: Decimal | None,
    field_name: str,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> None:
    if value is None:
        return
    _validate_decimal(value, field_name, positive=positive)
    if non_negative and value < ZERO:
        raise ValueError(f"{field_name} must be non-negative")


def _validate_sources(sources: tuple[str, ...]) -> tuple[str, ...]:
    values = tuple(sources)
    if not values:
        raise ValueError("sources must not be empty")
    for source in values:
        _require_text(source, "source")
    return values


def _required_decimal(value: object, field_name: str) -> Decimal:
    converted = _optional_decimal(value)
    if converted is None:
        raise ValueError(f"{field_name} must not be missing")
    return converted


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        converted = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("value must be decimal-compatible") from exc
    if not converted.is_finite():
        raise ValueError("value must be finite")
    return converted


def _optional_percent_ratio(value: object) -> Decimal | None:
    converted = _optional_decimal(value)
    return converted / HUNDRED if converted is not None else None


def _optional_change(
    start: Decimal | None,
    end: Decimal | None,
) -> Decimal | None:
    return end - start if start is not None and end is not None else None


def _value_range(values: Iterable[Decimal | None]) -> Decimal | None:
    available = [value for value in values if value is not None]
    return max(available) - min(available) if len(available) >= 2 else None


def _deduplicate(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))
