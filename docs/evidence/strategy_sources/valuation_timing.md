# 估值分位择时模板：外部证据登记

- `strategy_id`: `valuation_percentile_timing`（策略模板）
- 版本：`v1`
- 声明机制：`valuation_mean_reversion`、`trailing_percentile_rank`、`hysteresis_thresholds`、`point_in_time_valuation`
- 登记日期：`2026-10-29`

本文是开发者审计记录，不是运行配置。分位阈值、回看年数与估值序列由用户参数决定，须经本项目验证。

## valuation-timing-campbell-shiller-1998

- `source_kind`: `paper`
- 标题：John Y. Campbell, Robert J. Shiller, *Valuation Ratios and the Long-Run Stock Market Outlook*
- `canonical_url`: https://doi.org/10.3905/jpm.24.2.11
- `immutable_ref`: `DOI:10.3905/jpm.24.2.11`（Crossref 核对：The Journal of Portfolio Management 24(2), 11–26）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `validation`
- `mechanism_ids`: `valuation_mean_reversion`, `trailing_percentile_rank`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 估值比率处于历史高位时长期收益预期较低、低位时较高；用相对自身历史的位置而不是绝对数值判断。
- `prohibited_transfers`: 不移植美国 CAPE 数值与预测系数；论文讨论的是多年期收益，不支持月度频繁进出。
- `applicability`: 支持“估值分位影响中长期预期收益”的机制，时间尺度应为中长期。
- `known_biases`: 美国长样本；估值序列本身受会计口径与成分变化影响。

## valuation-timing-bt-selectwhere

- `source_kind`: `repository`
- 标题：`pmorissette/bt` 的 `SelectWhere`、`RunMonthly` 与 `Rebalance`
- `canonical_url`: https://github.com/pmorissette/bt/tree/5dbfbedb24ec83d7a43c8d55741b72b1642a62a6
- `immutable_ref`: `commit:5dbfbedb24ec83d7a43c8d55741b72b1642a62a6`
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=MIT`；`license_status=verified`
- `reuse_policy`: `adapt_allowed`
- `evidence_roles`: `code`
- `mechanism_ids`: `hysteresis_thresholds`, `point_in_time_valuation`
- `reviewed_paths`: `bt/algos.py` 中 `SelectWhere`（外部指标表驱动持仓；注释明确基本面信号只有在每个历史日期“当时已知”时才无前视，修订数据会引入前视）、`RunMonthly`、`Rebalance`；根目录 `LICENSE`
- `borrowable_mechanisms`: 外部指标与价格分开传入；指标缺失的日期不交易；按指标状态持有或清仓。
- `prohibited_transfers`: 不复制数据容器；bt 不处理指标发布日，本项目按估值点的 `available_at` 决定何时可见。
- `applicability`: 作为“外部指标驱动持仓且须时点可得”的代码对照；滞回阈值由本项目实现。
- `known_biases`: 通用框架示例不含 A 股交易限制。

## valuation-timing-goyal-welch-2008

- `source_kind`: `paper`
- 标题：Ivo Welch, Amit Goyal, *A Comprehensive Look at the Empirical Performance of Equity Premium Prediction*
- `canonical_url`: https://doi.org/10.1093/rfs/hhm014
- `immutable_ref`: `DOI:10.1093/rfs/hhm014`（Crossref 核对：Review of Financial Studies 21(4), 1455–1508）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `counterevidence`
- `mechanism_ids`: `valuation_mean_reversion`, `trailing_percentile_rank`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 估值类预测变量的样本外表现常常不如历史均值；必须做样本外检验并与买入持有比较。
- `prohibited_transfers`: 不据此断言 A 股估值择时无效，只作为验证门槛。
- `applicability`: 要求估值择时给出滚动样本外结果，且样本外不劣于简单基准才可前进。
- `known_biases`: 美国样本；以回归预测为主，不直接评估阈值进出规则。
