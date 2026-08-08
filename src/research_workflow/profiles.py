"""Asset-specific research profile registry."""

from __future__ import annotations

from src.data_access import (
    DATASET_EXCHANGE_FUND_DAILY_BARS,
    DATASET_FUND_ALLOCATION,
    DATASET_FUND_SCALE,
    DATASET_FUND_STATUS,
    DATASET_OPEN_FUND_NAV,
    DATASET_STOCK_DAILY_BARS,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_OPEN_FUND,
    ASSET_TYPE_STOCK,
    Asset,
)

from .models import AssetResearchProfile


PROFILE_ORDINARY_COMPANY = "ordinary_company"
PROFILE_FINANCIAL_INSTITUTION = "financial_institution"
PROFILE_CYCLICAL_RESOURCE = "cyclical_resource"
PROFILE_HIGH_GROWTH_UNPROFITABLE = "high_growth_unprofitable"
PROFILE_EXCHANGE_FUND = "exchange_fund"
PROFILE_OPEN_FUND = "open_fund"


class ResearchProfileRegistry:
    def __init__(
        self,
        profiles: tuple[AssetResearchProfile, ...],
    ) -> None:
        if not profiles:
            raise ValueError("profiles must not be empty")
        by_id = {profile.profile_id: profile for profile in profiles}
        if len(by_id) != len(profiles):
            raise ValueError("profile_id must be unique")
        self._profiles = by_id

    def list_profiles(self) -> tuple[AssetResearchProfile, ...]:
        return tuple(
            self._profiles[key]
            for key in sorted(self._profiles)
        )

    def get(self, profile_id: str) -> AssetResearchProfile | None:
        return self._profiles.get(profile_id)

    def require(self, profile_id: str) -> AssetResearchProfile:
        profile = self.get(profile_id)
        if profile is None:
            raise KeyError(f"Unknown research profile: {profile_id}")
        return profile

    def default_for_asset(
        self,
        asset: Asset,
    ) -> AssetResearchProfile:
        default_id = {
            ASSET_TYPE_STOCK: PROFILE_ORDINARY_COMPANY,
            ASSET_TYPE_EXCHANGE_FUND: PROFILE_EXCHANGE_FUND,
            ASSET_TYPE_OPEN_FUND: PROFILE_OPEN_FUND,
        }.get(asset.asset_type)
        if default_id is None:
            raise ValueError(
                f"No default research profile for "
                f"asset_type: {asset.asset_type}"
            )
        return self.require(default_id)

    def validate_for_asset(
        self,
        profile_id: str,
        asset: Asset,
    ) -> AssetResearchProfile:
        profile = self.require(profile_id)
        if not profile.supports_asset_type(asset.asset_type):
            raise ValueError(
                f"Research profile {profile_id} does not support "
                f"asset_type {asset.asset_type}"
            )
        return profile


def default_research_profiles() -> tuple[
    AssetResearchProfile,
    ...,
]:
    company_datasets = (
        DATASET_STOCK_DAILY_BARS,
        "financial_statements",
        "financial_indicators",
        "disclosures",
        "research_reports",
    )
    company_risks = (
        "business_model",
        "governance",
        "financial_quality",
        "valuation",
    )
    return (
        AssetResearchProfile(
            profile_id=PROFILE_ORDINARY_COMPANY,
            display_name="普通公司",
            supported_asset_types=(ASSET_TYPE_STOCK,),
            required_datasets=company_datasets,
            required_metrics=(
                "cumulative_return",
                "max_drawdown",
                "pe_ttm",
                "roe",
                "roic",
                "fcf_yield",
            ),
            default_benchmark_role="broad_market",
            major_risks=company_risks,
        ),
        AssetResearchProfile(
            profile_id=PROFILE_FINANCIAL_INSTITUTION,
            display_name="金融机构",
            supported_asset_types=(ASSET_TYPE_STOCK,),
            required_datasets=company_datasets,
            required_metrics=(
                "cumulative_return",
                "max_drawdown",
                "pb",
                "roe",
                "capital_adequacy",
                "net_interest_margin",
                "non_performing_loan_ratio",
            ),
            forbidden_metrics=("ev_ebitda", "fcf_yield"),
            default_benchmark_role="financial_sector",
            major_risks=(
                "credit_quality",
                "liquidity",
                "capital_adequacy",
                "interest_rate",
                "governance",
            ),
        ),
        AssetResearchProfile(
            profile_id=PROFILE_CYCLICAL_RESOURCE,
            display_name="周期与资源公司",
            supported_asset_types=(ASSET_TYPE_STOCK,),
            required_datasets=company_datasets
            + ("commodity_benchmark",),
            required_metrics=(
                "cumulative_return",
                "max_drawdown",
                "pb",
                "roic",
                "net_debt",
                "commodity_sensitivity",
            ),
            default_benchmark_role="industry_cycle",
            major_risks=(
                "commodity_cycle",
                "capacity_expansion",
                "cost_curve",
                "balance_sheet",
            ),
        ),
        AssetResearchProfile(
            profile_id=PROFILE_HIGH_GROWTH_UNPROFITABLE,
            display_name="高成长未盈利公司",
            supported_asset_types=(ASSET_TYPE_STOCK,),
            required_datasets=company_datasets,
            required_metrics=(
                "cumulative_return",
                "max_drawdown",
                "ps",
                "revenue_growth",
                "gross_margin",
                "cash_burn",
            ),
            forbidden_metrics=("pe_ttm", "dividend_yield"),
            default_benchmark_role="growth_style",
            major_risks=(
                "cash_runway",
                "dilution",
                "growth_durability",
                "valuation",
            ),
        ),
        AssetResearchProfile(
            profile_id=PROFILE_EXCHANGE_FUND,
            display_name="ETF、LOF 与封闭式基金",
            supported_asset_types=(ASSET_TYPE_EXCHANGE_FUND,),
            required_datasets=(
                DATASET_EXCHANGE_FUND_DAILY_BARS,
                "underlying_index_daily_bars",
            ),
            required_metrics=(
                "cumulative_return",
                "max_drawdown",
                "tracking_error",
                "premium_discount",
                "expense_ratio",
            ),
            forbidden_metrics=(
                "pe_ttm",
                "roe",
                "roic",
                "ev_ebitda",
                "fcf_yield",
            ),
            default_benchmark_role="underlying_index",
            major_risks=(
                "tracking_error",
                "liquidity",
                "premium_discount",
                "underlying_exposure",
            ),
        ),
        AssetResearchProfile(
            profile_id=PROFILE_OPEN_FUND,
            display_name="场外基金",
            supported_asset_types=(ASSET_TYPE_OPEN_FUND,),
            required_datasets=(
                DATASET_OPEN_FUND_NAV,
                DATASET_FUND_STATUS,
                DATASET_FUND_SCALE,
                DATASET_FUND_ALLOCATION,
            ),
            required_metrics=(
                "cumulative_return",
                "max_drawdown",
                "sharpe_ratio",
                "expense_ratio",
                "fund_scale",
            ),
            forbidden_metrics=(
                "pe_ttm",
                "roe",
                "roic",
                "ev_ebitda",
                "fcf_yield",
            ),
            default_benchmark_role="fund_category",
            major_risks=(
                "manager",
                "style_drift",
                "liquidity",
                "redemption",
                "underlying_exposure",
            ),
        ),
    )


def default_profile_registry() -> ResearchProfileRegistry:
    return ResearchProfileRegistry(default_research_profiles())
