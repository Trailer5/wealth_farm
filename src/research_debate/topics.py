"""Standing world topics scanned for layer-1 debates when nobody has brought a view yet.

Each topic names the lenses best placed to form a view on it and the news queries that surface
fresh developments (``en:`` / ``zh:`` prefixes select the language). The list is deliberately
short: it is a blind-spot checklist, not a news feed. Design notes:
docs/modules/5-研究产物/观点辩论模块.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorldTopic:
    topic_id: str
    title: str
    lenses: tuple[str, ...]
    queries: tuple[str, ...]


WORLD_TOPICS: tuple[WorldTopic, ...] = (
    WorldTopic("us_rates_liquidity", "美联储利率、美元与全球流动性", ("dalio", "marks"),
               ("en:Federal Reserve rate decision dollar liquidity", "zh:美联储 降息 美元")),
    WorldTopic("china_policy_growth", "中国稳增长政策、财政与货币", ("dalio", "policy_analyst"),
               ("en:China stimulus PBOC fiscal policy", "zh:稳增长 政策 央行 财政")),
    WorldTopic("us_china_tech", "中美科技与贸易管制（芯片、关税、实体清单）", ("policy_analyst", "industry_analyst"),
               ("en:export controls China chips tariffs entity list", "zh:出口管制 实体清单 关税 芯片")),
    WorldTopic("ai_capex", "AI 资本开支与算力周期", ("industry_analyst", "soros"),
               ("en:AI capex hyperscaler data center spending", "zh:AI 资本开支 算力 数据中心")),
    WorldTopic("geopolitics_energy", "地缘冲突、能源与航运", ("policy_analyst", "dalio"),
               ("en:geopolitical conflict oil prices shipping", "zh:地缘冲突 油价 航运")),
    WorldTopic("commodities_inflation", "大宗商品与通胀", ("dalio", "industry_analyst"),
               ("en:commodity prices inflation copper gold", "zh:大宗商品 通胀 铜价 金价")),
    WorldTopic("a_share_sentiment", "A 股情绪、资金与估值", ("marks", "soros"),
               ("en:China A-shares rally selloff turnover", "zh:A股 成交额 两融 资金")),
    WorldTopic("rmb_capital_flows", "人民币汇率与跨境资本", ("dalio", "policy_analyst"),
               ("en:yuan exchange rate capital flows", "zh:人民币汇率 跨境资本")),
)
