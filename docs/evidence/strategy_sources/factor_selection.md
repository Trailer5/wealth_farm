# 因子选股模板：外部证据登记

- `strategy_id`: `factor_selection`（策略模板）
- 版本：`v1`
- 声明机制：`value_ranking`、`quality_screen`、`point_in_time_fundamentals`、`top_n_equal_weight`
- 登记日期：`2026-10-29`

本文是开发者审计记录，不是运行配置。筛选阈值、排序因子与持仓数由用户参数决定，须经本项目验证。

## factor-fama-french-1993

- `source_kind`: `paper`
- 标题：Eugene F. Fama, Kenneth R. French, *Common Risk Factors in the Returns on Stocks and Bonds*
- `canonical_url`: https://doi.org/10.1016/0304-405X(93)90023-5
- `immutable_ref`: `DOI:10.1016/0304-405X(93)90023-5`（Journal of Financial Economics 33, 3–56）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`
- `mechanism_ids`: `value_ranking`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 按账面市值比、盈利价格比等估值指标分组，低估值组合长期收益较高。
- `prohibited_transfers`: 不移植美国因子溢价大小与分组断点。
- `applicability`: 支持价值排序的机制。
- `known_biases`: 美国样本；因子溢价随时间变化。

## factor-liu-stambaugh-yuan-2019

- `source_kind`: `paper`
- 标题：Jianan Liu, Robert F. Stambaugh, Yu Yuan, *Size and Value in China*
- `canonical_url`: https://doi.org/10.1016/j.jfineco.2019.03.008
- `immutable_ref`: `DOI:10.1016/j.jfineco.2019.03.008`（Crossref 核对：Journal of Financial Economics 134(1), 48–69；CC BY 4.0）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=CC-BY-4.0`；`license_status=verified`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `validation`
- `mechanism_ids`: `value_ranking`, `point_in_time_fundamentals`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: A 股中盈利价格比（E/P）是更有效的价值度量；剔除最小市值股票以避开壳价值污染；财务数据按公告时点使用。
- `prohibited_transfers`: 不移植论文的因子收益数值；本项目没有壳价值剔除，须在局限中说明。
- `applicability`: 直接的 A 股价值证据，支持默认按 E/P 排序。
- `known_biases`: 样本截至 2016 年；小盘与壳价值处理影响结论。

## factor-asness-frazzini-pedersen-qmj

- `source_kind`: `paper`
- 标题：Clifford S. Asness, Andrea Frazzini, Lasse Heje Pedersen, *Quality Minus Junk*
- `canonical_url`: https://doi.org/10.1007/s11142-018-9470-2
- `immutable_ref`: `DOI:10.1007/s11142-018-9470-2`（Crossref 核对：Review of Accounting Studies 24, 34–112）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`
- `mechanism_ids`: `quality_screen`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 质量由盈利能力、增长与安全性（低杠杆）组成；高质量股票在控制估值后有正超额。
- `prohibited_transfers`: 不移植其综合质量分的构造与权重；本项目只用 ROE、利润增长与资产负债率阈值。
- `applicability`: 支持“先筛质量、再按估值排序”的结构。
- `known_biases`: 以美国与发达市场为主。

## factor-bt-selectn

- `source_kind`: `repository`
- 标题：`pmorissette/bt` 的 `SetStat`、`SelectN`、`WeighEqually` 与 `RunMonthly`
- `canonical_url`: https://github.com/pmorissette/bt/tree/5dbfbedb24ec83d7a43c8d55741b72b1642a62a6
- `immutable_ref`: `commit:5dbfbedb24ec83d7a43c8d55741b72b1642a62a6`
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=MIT`；`license_status=verified`
- `reuse_policy`: `adapt_allowed`
- `evidence_roles`: `code`
- `mechanism_ids`: `top_n_equal_weight`, `point_in_time_fundamentals`
- `reviewed_paths`: `bt/algos.py` 中 `SetStat`（带滞后读取指标）、`SelectN`（去掉缺失值后按指标排序取前 N）、`WeighEqually`、`SelectWhere` 的时点数据警告；根目录 `LICENSE`
- `borrowable_mechanisms`: 缺失值先剔除再排序；排序、选择与加权分层；指标按滞后读取。
- `prohibited_transfers`: 不复制数据容器与撮合；本项目按公告日而不是固定滞后决定财报可见。
- `applicability`: 作为“排序取前 N 等权”的代码对照。
- `known_biases`: 通用框架不处理退市、停牌与涨跌停。

## factor-mclean-pontiff-2016

- `source_kind`: `paper`
- 标题：R. David McLean, Jeffrey Pontiff, *Does Academic Research Destroy Stock Return Predictability?*
- `canonical_url`: https://doi.org/10.1111/jofi.12365
- `immutable_ref`: `DOI:10.1111/jofi.12365`（Crossref 核对：Journal of Finance 71(1), 5–32）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `counterevidence`
- `mechanism_ids`: `value_ranking`, `quality_screen`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 已发表的选股变量样本外收益约低 26%、发表后约低 58%；回测超额须打折看待。
- `prohibited_transfers`: 不把折扣比例直接套用于 A 股。
- `applicability`: 要求因子选股报告尝试次数、样本外与基准对比，不能只看样本内超额。
- `known_biases`: 美国 97 个变量的平均结论。
