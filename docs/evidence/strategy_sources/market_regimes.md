# 分市场状态表现：外部证据登记（验证方法，非交易策略）

- `strategy_id`: `market_regime_performance`（策略验证方法）
- 版本：`v1`（状态定义版本 `csi300-v1`）
- 声明机制：`trend_state`、`volatility_state`、`valuation_state`、`state_conditional_performance`、`sharpe_difference_test`
- 登记日期：`2026-10-29`

本文是开发者审计记录。代码记录在 `src/backtest/regime_evidence.py`，实现于 `src/backtest/market_regimes.py`。状态维度与阈值由阶段 9 计划决策 6 固定，来源只支持机制，不背书具体窗口。

## regime-faber-qtaa

- `source_kind`: `paper`
- 标题：Mebane T. Faber, *A Quantitative Approach to Tactical Asset Allocation*
- `canonical_url`: https://doi.org/10.3905/jwm.2007.674809
- `immutable_ref`: `DOI:10.3905/jwm.2007.674809`（沿用 `sse_market_timing.md` 的登记）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`
- `mechanism_ids`: `trend_state`
- `borrowable_mechanisms`: 价格相对长期均线的位置区分上升与下降趋势，两种状态下风险资产的收益与回撤明显不同。
- `prohibited_transfers`: 不移植 10 个月均线与美国资产。
- `applicability`: 趋势维度。
- `known_biases`: 美国长样本。

## regime-moreira-muir-2017

- `source_kind`: `paper`
- 标题：Alan Moreira, Tyler Muir, *Volatility-Managed Portfolios*
- `canonical_url`: https://doi.org/10.1111/jofi.12513
- `immutable_ref`: `DOI:10.1111/jofi.12513`（Crossref 核对：The Journal of Finance 72(4), 1611–1644）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`
- `mechanism_ids`: `volatility_state`
- `borrowable_mechanisms`: 波动上升时预期收益并未同比例上升，高波动与低波动时期的风险收益比不同，适合分开观察。
- `prohibited_transfers`: 不实现按波动缩放仓位的策略本身。
- `applicability`: 波动维度。
- `known_biases`: 美国因子与市场组合。

## regime-campbell-shiller-1998

- `source_kind`: `paper`
- 标题：John Y. Campbell, Robert J. Shiller, *Valuation Ratios and the Long-Run Stock Market Outlook*
- `canonical_url`: https://doi.org/10.3905/jpm.24.2.11
- `immutable_ref`: `DOI:10.3905/jpm.24.2.11`（沿用 `valuation_timing.md` 的登记）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`
- `mechanism_ids`: `valuation_state`
- `borrowable_mechanisms`: 估值相对自身历史的位置与此后长期收益相关。
- `prohibited_transfers`: 不把估值状态当作短期信号。
- `applicability`: 估值维度。
- `known_biases`: 美国样本。

## regime-jobson-korkie-1981

- `source_kind`: `paper`
- 标题：J. D. Jobson, Bob M. Korkie, *Performance Hypothesis Testing with the Sharpe and Treynor Measures*
- `canonical_url`: https://doi.org/10.1111/j.1540-6261.1981.tb04891.x
- `immutable_ref`: `DOI:10.1111/j.1540-6261.1981.tb04891.x`（Crossref 检索核对：The Journal of Finance）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`
- `mechanism_ids`: `sharpe_difference_test`
- `borrowable_mechanisms`: 两个相关收益序列的夏普差的渐近检验，方差随相关系数上升而下降；本项目用后续文献更正后的方差式。
- `prohibited_transfers`: 不做特雷诺比率检验。
- `applicability`: 分状态的策略与基准比较、参数邻域里登记参数与邻近参数的比较。
- `known_biases`: 假设收益独立同分布、近似正态。

## regime-pyfolio-periods

- `source_kind`: `repository`
- 标题：`quantopian/pyfolio` 的分时期表现与滚动波动
- `canonical_url`: https://github.com/quantopian/pyfolio/tree/4b901f6d73aa02ceb6d04b7d83502e5c6f2e81aa
- `immutable_ref`: `commit:4b901f6d73aa02ceb6d04b7d83502e5c6f2e81aa`
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=Apache-2.0`；`license_status=verified`（`LICENSE`）
- `reuse_policy`: `adapt_allowed`
- `evidence_roles`: `code`
- `mechanism_ids`: `state_conditional_performance`, `volatility_state`
- `reviewed_paths`: `pyfolio/interesting_periods.py`（按事件与“市场阶段”划定的日期区间）、`pyfolio/timeseries.py` 中 `extract_interesting_date_ranges`（按区间截取收益分别统计）与 `rolling_volatility`；`LICENSE`
- `borrowable_mechanisms`: 把收益按时期分组后分别统计；滚动波动按年化计算。
- `prohibited_transfers`: 不采用人工指定的事后日期区间；本项目用逐日可见的数据规则划分状态，并统计独立段数。
- `applicability`: 分组统计的代码对照。
- `known_biases`: 事后挑选的时期本身带有选择偏差。

## regime-ang-timmermann-2012

- `source_kind`: `paper`
- 标题：Andrew Ang, Allan Timmermann, *Regime Changes and Financial Markets*
- `canonical_url`: https://doi.org/10.3386/w17182
- `immutable_ref`: `DOI:10.3386/w17182`（Crossref 检索核对：NBER 工作论文）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=unknown`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `counterevidence`
- `mechanism_ids`: `state_conditional_performance`, `trend_state`, `volatility_state`
- `borrowable_mechanisms`: 状态本身不可直接观测，识别有误差；样本里每种状态的独立出现次数少，分状态结论很不稳定。
- `prohibited_transfers`: 不引入隐马尔可夫模型。
- `applicability`: 要求每种状态报告天数与独立段数，样本不足只列不读。
- `known_biases`: 综述性质。

## regime-ledoit-wolf-2008

- `source_kind`: `paper`
- 标题：Oliver Ledoit, Michael Wolf, *Robust performance hypothesis testing with the Sharpe ratio*
- `canonical_url`: https://doi.org/10.1016/j.jempfin.2008.03.002
- `immutable_ref`: `DOI:10.1016/j.jempfin.2008.03.002`（Crossref 检索核对：Journal of Empirical Finance）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `counterevidence`
- `mechanism_ids`: `sharpe_difference_test`
- `borrowable_mechanisms`: 收益厚尾或序列相关时 Jobson–Korkie 检验不可靠，应使用异方差自相关稳健或自助法检验。
- `prohibited_transfers`: 自助法检验本轮不实现。
- `applicability`: 显著性结论只作排序与提示，报告里写明检验假设。
- `known_biases`: 稳健方法计算量较大。
