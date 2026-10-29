# 均线择时模板：外部证据登记

- `strategy_id`: `ma_timing`（策略模板；用户策略以“模板 + 参数”登记）
- 版本：`v1`
- 声明机制：`price_above_moving_average`、`state_switch_to_cash`、`next_bar_execution`
- 登记日期：`2026-10-29`

本文是开发者审计记录，不是运行配置。窗口长度、标的与是否加过滤带都由用户参数决定，须经本项目验证，不能由以下来源直接背书。

## ma-timing-faber-qtaa

- `source_kind`: `paper`
- 标题：Mebane T. Faber, *A Quantitative Approach to Tactical Asset Allocation*
- `canonical_url`: https://doi.org/10.3905/jwm.2007.674809
- `immutable_ref`: `DOI:10.3905/jwm.2007.674809; SSRN:962461`
- `retrieved_at`: `2026-10-29`（沿用 `sse_market_timing.md` 的登记）
- 许可证：`license_spdx=None`；`license_status=unknown`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `validation`
- `mechanism_ids`: `price_above_moving_average`, `state_switch_to_cash`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 价格在长期均线之上持有风险资产、之下转为现金；信号只在状态改变时换仓。
- `prohibited_transfers`: 不移植论文的 10 个月均线、美国资产类别与历史绩效；论文按月末判断，本模板按日判断须单独验证。
- `applicability`: 支持“价格相对长期均线决定持有或空仓”的机制起点。
- `known_biases`: 美国长样本；回测期内趋势跟随表现较好的阶段可能被高估；交易成本假设偏低。

## ma-timing-bt-selectwhere

- `source_kind`: `repository`
- 标题：`pmorissette/bt` 的 `SelectWhere`、`Rebalance` 与调度算法
- `canonical_url`: https://github.com/pmorissette/bt/tree/5dbfbedb24ec83d7a43c8d55741b72b1642a62a6
- `immutable_ref`: `commit:5dbfbedb24ec83d7a43c8d55741b72b1642a62a6`
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=MIT`；`license_status=verified`（根目录 `LICENSE`）
- `reuse_policy`: `adapt_allowed`
- `evidence_roles`: `code`
- `mechanism_ids`: `state_switch_to_cash`, `next_bar_execution`
- `reviewed_paths`: `bt/algos.py` 中 `SelectWhere`（按布尔指标表选择持仓，注释提醒基本面信号须为时点数据）、`Rebalance`（不在目标中的持仓清仓）；根目录 `LICENSE`
- `borrowable_mechanisms`: 指标状态与调仓执行分层；不在目标权重里的持仓清仓；信号行缺失时不交易。
- `prohibited_transfers`: 不复制其 pandas 数据容器与撮合；bt 默认在同一根 bar 上成交，本项目改为信号日收盘决定、下一交易日开盘成交。
- `applicability`: 仅作为“指标 → 目标持仓 → 调仓”结构的代码对照。
- `known_biases`: 通用回测框架，不含 A 股涨跌停、停牌与印花税。

## ma-timing-zakamulin-jam-2014

- `source_kind`: `paper`
- 标题：Valeriy Zakamulin, *The Real-Life Performance of Market Timing with Moving Average and Time-Series Momentum Rules*
- `canonical_url`: https://doi.org/10.1057/jam.2014.25
- `immutable_ref`: `DOI:10.1057/jam.2014.25`
- `retrieved_at`: `2026-10-29`（沿用 `etf_dual_momentum.md` 的登记）
- 许可证：`license_spdx=None`；`license_status=unknown`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `counterevidence`
- `mechanism_ids`: `price_above_moving_average`, `next_bar_execution`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 用可实际执行的信号时点重算均线择时，检查样本外与真实表现是否显著弱于文献回测。
- `prohibited_transfers`: 不把单一市场结论机械外推为所有均线规则无效。
- `applicability`: 要求均线择时报告相对买入持有的增量、回撤改善与成本拖累，并经滚动样本外检验。
- `known_biases`: 研究对象是美国指数；结论随样本区间变化。
