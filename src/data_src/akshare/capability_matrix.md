# AkShare 功能矩阵

本文件记录 AkShare 能力边界和本项目封装状态。本文件是数据源适配器参考文档，允许放在 `src/data_src/akshare/` 目录下。

## 封装原则

高频、稳定、会进入项目数据表的接口必须封装为明确函数。

低频、探索性、接口数量过多或字段不稳定的接口不逐个封装，必须通过 `AkShareDataSource.call_api()` 透传调用。

AkShare 数据来自公开网页和公开数据源。项目不得把 AkShare 数据视为官方披露源。

## 当前已封装

| 能力 | AkShare 接口 | 项目函数 | 用途 |
| --- | --- | --- | --- |
| A 股历史 K 线 | `stock_zh_a_hist` | `get_a_share_history` / `get_a_share_daily_bars` | A 股日线、周线、月线 |
| A 股实时行情 | `stock_zh_a_spot_em` | `get_a_share_spot` | 盘中快照和基础行情观察 |
| A 股分钟线 | `stock_zh_a_hist_min_em` | `get_a_share_minute_bars` | 分钟级补充数据 |
| ETF 实时行情 | `fund_etf_spot_em` | `get_etf_spot` | 场内基金实时快照 |
| ETF 历史行情 | `fund_etf_hist_em` | `get_etf_daily_bars` | 场内基金日线 |
| 开放式基金净值列表 | `fund_open_fund_daily_em` | `get_open_fund_daily_navs` | 场外基金最新净值 |
| 开放式基金净值历史 | `fund_open_fund_info_em` | `get_open_fund_nav_history` | 场外基金历史净值 |
| 货币基金净值列表 | `fund_money_fund_daily_em` | `get_money_fund_daily_navs` | 货币基金净值 |
| 利润表 | `stock_profit_sheet_by_report_em` | `get_stock_profit_sheet` | A 股财报结构化数据 |
| 资产负债表 | `stock_balance_sheet_by_report_em` | `get_stock_balance_sheet` | A 股财报结构化数据 |
| 现金流量表 | `stock_cash_flow_sheet_by_report_em` | `get_stock_cash_flow_sheet` | A 股财报结构化数据 |
| 财务指标 | `stock_financial_analysis_indicator_em` | `get_stock_financial_indicators` | A 股财务指标 |
| 财报预约披露 | `stock_report_disclosure` | `get_stock_report_disclosure` | 财报披露跟踪 |
| 行业板块列表 | `stock_board_industry_name_em` | `get_industry_board_names` | 行业分类 |
| 行业板块成分 | `stock_board_industry_cons_em` | `get_industry_board_constituents` | 行业成分股 |
| 概念板块列表 | `stock_board_concept_name_em` | `get_concept_board_names` | 概念分类 |
| 概念板块成分 | `stock_board_concept_cons_em` | `get_concept_board_constituents` | 概念成分股 |

## 通过 `call_api()` 调用

以下能力暂不逐个封装。需要使用时必须先用 `call_api()` 验证字段、稳定性和价值，再决定是否升级为明确函数。

| 能力 | 典型接口 | 备注 |
| --- | --- | --- |
| 资金流 | `stock_hsgt_*`、`stock_fund_flow_*` | 接口多且口径复杂，先按研究任务使用 |
| 龙虎榜 | `stock_lhb_*` | 适合事件研究，不是短期数据底座主线 |
| 涨跌停 | `stock_zt_pool_*` | 适合市场情绪和短线研究 |
| 融资融券 | `stock_margin_*` | 后续可作为风险和资金面指标 |
| 股东数据 | `stock_*_holder*` | 需要和官方报告交叉验证 |
| 基金持股 | `stock_report_fund_hold*` | 适合组合穿透和机构持仓研究 |
| 基金排行 | `fund_*_rank*` | 适合筛选，不作为净值主表 |
| 基金规模 | `fund_*_aum*`、`fund_scale_*` | 后续进入基金档案模块 |
| 基金资产配置 | `fund_report_asset_allocation_*` | 后续进入基金披露模块 |
| 基金行业配置 | `fund_report_industry_allocation_*` | 后续进入基金披露模块 |
| 指数数据 | `index_*` | 后续作为基准和组合归因模块 |
| 债券和利率 | `bond_*`、`interest_rate_*` | 后续作为估值折现率来源 |
| 宏观数据 | `macro_*` | 后续作为宏观背景和周期判断 |
| 港股、美股、期货、期权、外汇、加密货币 | 多模块接口 | 当前项目短期聚焦 A 股与基金 |

## 不作为主数据源

财报 PDF、审计报告和正式公告必须使用官方披露源。AkShare 的结构化财务数据可以作为辅助数据源，不得替代巨潮资讯网、交易所官网和基金公司公告。

场外基金估值只能作为估计值。场外基金估值不得等同于正式净值。
