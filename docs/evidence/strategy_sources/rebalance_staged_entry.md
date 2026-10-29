# 定期再平衡与分批建仓模板：外部证据登记

- `strategy_id`: `fixed_weight_rebalance`、`staged_entry`（两个策略模板共用本登记）
- 版本：`v1`
- 声明机制：
  - `fixed_weight_rebalance`: `constant_mix_rebalance`、`calendar_schedule`
  - `staged_entry`: `staged_capital_deployment`、`calendar_schedule`
- 登记日期：`2026-10-29`

本文是开发者审计记录，不是运行配置。目标权重、频率与分批期数由用户参数决定。分批建仓用“期初资金分 N 期投入、未投入部分按现金计息”表达定投，与“一次性买入”对照；工资式持续追加资金没有建模。

## rebalance-perold-sharpe-1988

- `source_kind`: `paper`
- 标题：André F. Perold, William F. Sharpe, *Dynamic Strategies for Asset Allocation*
- `canonical_url`: https://doi.org/10.2469/faj.v44.n1.16
- `immutable_ref`: `DOI:10.2469/faj.v44.n1.16`（Crossref 核对：Financial Analysts Journal 44(1)）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `counterevidence`
- `mechanism_ids`: `constant_mix_rebalance`, `calendar_schedule`, `staged_capital_deployment`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 恒定比例（定期再平衡）在震荡市优于买入持有、在单边趋势市劣于买入持有；不同动态策略的收益形态由其“买跌卖涨”或“追涨杀跌”属性决定。
- `prohibited_transfers`: 不把恒定比例当作无条件更优；论文为机制分析，不提供 A 股参数。
- `applicability`: 支持再平衡的机制，同时说明其失效环境（单边趋势），作为再平衡与分批建仓的机制与反证来源。
- `known_biases`: 理论分析与示意数据。

## staged-entry-constantinides-1979

- `source_kind`: `paper`
- 标题：George M. Constantinides, *A Note on the Suboptimality of Dollar-Cost Averaging as an Investment Policy*
- `canonical_url`: https://doi.org/10.2307/2330513
- `immutable_ref`: `DOI:10.2307/2330513`（Crossref 检索核对，Journal of Financial and Quantitative Analysis）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `counterevidence`
- `mechanism_ids`: `staged_capital_deployment`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 预先固定的分批投入在理论上劣于可随信息调整的策略；分批的主要作用是降低择时遗憾而非提高期望收益。
- `prohibited_transfers`: 不据此禁止分批建仓，只要求与一次性买入同时报告。
- `applicability`: 要求分批建仓的回测同时给出一次性买入对照，并在结论中说明期望收益通常更低。
- `known_biases`: 理论结论；未考虑行为约束与流动性需求。

## rebalance-bt-algos

- `source_kind`: `repository`
- 标题：`pmorissette/bt` 的 `RunMonthly`/`RunQuarterly`/`RunYearly`、`WeighSpecified`、`Rebalance`、`RebalanceOverTime`、`CapitalFlow`
- `canonical_url`: https://github.com/pmorissette/bt/tree/5dbfbedb24ec83d7a43c8d55741b72b1642a62a6
- `immutable_ref`: `commit:5dbfbedb24ec83d7a43c8d55741b72b1642a62a6`
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=MIT`；`license_status=verified`
- `reuse_policy`: `adapt_allowed`
- `evidence_roles`: `code`
- `mechanism_ids`: `constant_mix_rebalance`, `calendar_schedule`, `staged_capital_deployment`
- `reviewed_paths`: `bt/algos.py` 中 `RunMonthly`/`RunQuarterly`/`RunYearly`（按期间变化触发）、`WeighSpecified`、`Rebalance`、`RebalanceOverTime`（把权重变化分 n 期完成）、`CapitalFlow`（注释记录外部现金流对收益计算的已知缺陷）；根目录 `LICENSE`
- `borrowable_mechanisms`: 期间切换触发调仓；固定权重；目标权重分 n 期逐步到达。
- `prohibited_transfers`: 不采用 `CapitalFlow` 的外部现金流记账（其文档自述存在收益计算缺陷）；本项目用“期初资金分批投入”表达定投，避免外部现金流扭曲收益率。
- `applicability`: 作为日历调仓与分批到达目标权重的代码对照。
- `known_biases`: 通用框架不含 A 股交易限制。
