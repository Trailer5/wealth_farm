"""Frozen Plan 15 assets and repeatable research tasks."""

from __future__ import annotations

from dataclasses import replace

from src.fund_research import (
    ETFExpenseSchedule,
    official_etf_expense_schedule,
)
from src.master_data import (
    ASSET_TYPE_EXCHANGE_FUND,
    ASSET_TYPE_INDEX,
    ASSET_TYPE_STOCK,
    Asset,
    MasterDataService,
)
from src.research_workflow import (
    PROFILE_CYCLICAL_RESOURCE,
    PROFILE_EXCHANGE_FUND,
    PROFILE_FINANCIAL_INSTITUTION,
    PROFILE_ORDINARY_COMPANY,
    ResearchHorizon,
    ResearchQuestionType,
    ResearchTaskFactory,
    ResearchTaskSpec,
)


PLAN15_ACCEPTANCE_VERSION = "plan15_v1"
PLAN15_AS_OF_DATE = "2026-08-03"
PLAN15_BAR_START = "2026-07-01"
PLAN15_BAR_END = "2026-07-24"
PLAN15_FINANCIAL_START = "2023-01-01"
PLAN15_FINANCIAL_END = "2026-06-30"


def register_plan15_assets(
    master_data: MasterDataService,
) -> dict[str, Asset]:
    definitions = {
        "maotai": Asset(
            asset_id="asset:plan15-maotai",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600519",
            display_name="贵州茅台",
            market="cn_a",
            exchange="sh",
        ),
        "gold_etf": Asset(
            asset_id="asset:plan15-gold-etf",
            asset_type=ASSET_TYPE_EXCHANGE_FUND,
            standard_symbol="518880",
            display_name="黄金ETF",
            market="cn_a",
            exchange="sh",
        ),
        "csi300_etf": Asset(
            asset_id="asset:plan15-csi300-etf",
            asset_type=ASSET_TYPE_EXCHANGE_FUND,
            standard_symbol="510300",
            display_name="沪深300ETF",
            market="cn_a",
            exchange="sh",
        ),
        "pingan_bank": Asset(
            asset_id="asset:plan15-pingan-bank",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="000001",
            display_name="平安银行",
            market="cn_a",
            exchange="sz",
        ),
        "zijin": Asset(
            asset_id="asset:plan15-zijin",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="601899",
            display_name="紫金矿业",
            market="cn_a",
            exchange="sh",
        ),
        "csi300_index": Asset(
            asset_id="asset:plan15-csi300-index",
            asset_type=ASSET_TYPE_INDEX,
            standard_symbol="000300",
            display_name="沪深300",
            market="cn_a",
            exchange="sh",
        ),
        "wuliangye": Asset(
            asset_id="asset:plan15-wuliangye",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="000858",
            display_name="五粮液",
            market="cn_a",
            exchange="sz",
        ),
        "luzhou_laojiao": Asset(
            asset_id="asset:plan15-luzhou-laojiao",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="000568",
            display_name="泸州老窖",
            market="cn_a",
            exchange="sz",
        ),
        "shanxi_fenjiu": Asset(
            asset_id="asset:plan15-shanxi-fenjiu",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600809",
            display_name="山西汾酒",
            market="cn_a",
            exchange="sh",
        ),
        "tsingtao": Asset(
            asset_id="asset:plan15-tsingtao",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600600",
            display_name="青岛啤酒",
            market="cn_a",
            exchange="sh",
        ),
        "cmb": Asset(
            asset_id="asset:plan15-cmb",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600036",
            display_name="招商银行",
            market="cn_a",
            exchange="sh",
        ),
        "industrial_bank": Asset(
            asset_id="asset:plan15-industrial-bank",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="601166",
            display_name="兴业银行",
            market="cn_a",
            exchange="sh",
        ),
        "spdb": Asset(
            asset_id="asset:plan15-spdb",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600000",
            display_name="浦发银行",
            market="cn_a",
            exchange="sh",
        ),
        "jiangxi_copper": Asset(
            asset_id="asset:plan15-jiangxi-copper",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600362",
            display_name="江西铜业",
            market="cn_a",
            exchange="sh",
        ),
        "shandong_gold": Asset(
            asset_id="asset:plan15-shandong-gold",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="600547",
            display_name="山东黄金",
            market="cn_a",
            exchange="sh",
        ),
        "cmoc": Asset(
            asset_id="asset:plan15-cmoc",
            asset_type=ASSET_TYPE_STOCK,
            standard_symbol="603993",
            display_name="洛阳钼业",
            market="cn_a",
            exchange="sh",
        ),
    }
    return {
        name: master_data.register_asset(
            asset,
            source="plan15:asset_catalog",
            raw_symbol=asset.standard_symbol,
        )
        for name, asset in definitions.items()
    }


def build_plan15_tasks(
    master_data: MasterDataService,
    assets: dict[str, Asset],
) -> dict[str, ResearchTaskSpec]:
    factory = ResearchTaskFactory(master_data)
    definitions = {
        "maotai": (
            PROFILE_ORDINARY_COMPANY,
            ResearchQuestionType.INVESTMENT,
            ResearchHorizon.MEDIUM_LONG,
        ),
        "gold_etf": (
            PROFILE_EXCHANGE_FUND,
            ResearchQuestionType.CYCLE_READING,
            ResearchHorizon.LONG,
        ),
        "csi300_etf": (
            PROFILE_EXCHANGE_FUND,
            ResearchQuestionType.COMPARISON,
            ResearchHorizon.MEDIUM_LONG,
        ),
        "pingan_bank": (
            PROFILE_FINANCIAL_INSTITUTION,
            ResearchQuestionType.RISK_REVIEW,
            ResearchHorizon.MEDIUM_LONG,
        ),
        "zijin": (
            PROFILE_CYCLICAL_RESOURCE,
            ResearchQuestionType.CYCLE_READING,
            ResearchHorizon.LONG,
        ),
    }
    return {
        name: replace(
            factory.create(
                asset_id=assets[name].asset_id,
                profile_id=profile_id,
                question_type=question_type,
                horizon=horizon,
                as_of_date=PLAN15_AS_OF_DATE,
                benchmark_asset_id=assets[
                    "csi300_index"
                ].asset_id,
                peer_asset_ids=(
                    (
                        assets["wuliangye"].asset_id,
                        assets["luzhou_laojiao"].asset_id,
                        assets["shanxi_fenjiu"].asset_id,
                    )
                    if name == "maotai"
                    else (
                        (
                            assets["cmb"].asset_id,
                            assets["industrial_bank"].asset_id,
                            assets["spdb"].asset_id,
                        )
                        if name == "pingan_bank"
                        else (
                            (
                                assets["jiangxi_copper"].asset_id,
                                assets["shandong_gold"].asset_id,
                                assets["cmoc"].asset_id,
                            )
                            if name == "zijin"
                            else ()
                        )
                    )
                ),
            ),
            task_id=f"research:plan15:{name}",
        )
        for name, (
            profile_id,
            question_type,
            horizon,
        ) in definitions.items()
    }


def plan15_etf_expense_schedules(
    assets: dict[str, Asset],
) -> dict[str, ETFExpenseSchedule]:
    return {
        name: official_etf_expense_schedule(
            asset_id=assets[name].asset_id,
            standard_symbol=assets[name].standard_symbol,
        )
        for name in ("gold_etf", "csi300_etf")
    }
