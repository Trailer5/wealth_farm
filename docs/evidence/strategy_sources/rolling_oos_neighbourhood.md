# 滚动样本外与参数邻域：外部证据登记（验证方法，非交易策略）

- `strategy_id`: `rolling_oos_neighbourhood`（策略验证方法）
- 版本：`v1`
- 声明机制：`expanding_window_oos`、`embargo_gap`、`oos_benchmark_comparison`、`selection_overfitting`、`parameter_neighbourhood`、`sharpe_difference_test`
- 登记日期：`2026-10-29`

切片七补充：邻近参数“明显变差”改为数据决定——登记参数与邻近参数在同一区间的日超额收益配对，Jobson–Korkie 统计量（更正后的方差式）达到单侧 5%（1.645）才算变差。该检验的来源与反证（`regime-jobson-korkie-1981`、`regime-ledoit-wolf-2008`）登记在 [market_regimes.md](market_regimes.md)，代码记录同时列入本方法。

本文是开发者审计记录。代码记录在 `src/backtest/validation_evidence.py`，实现于 `src/backtest/validation.py` 与 `src/research_orchestration/strategy_evaluate.py`。

## oos-bergmeir-benitez-2012

- `source_kind`: `paper`
- 标题：Christoph Bergmeir, José M. Benítez, *On the use of cross-validation for time series predictor evaluation*
- `canonical_url`: https://doi.org/10.1016/j.ins.2011.12.028
- `immutable_ref`: `DOI:10.1016/j.ins.2011.12.028`（Crossref 检索核对：Information Sciences）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`
- `mechanism_ids`: `expanding_window_oos`, `embargo_gap`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 时间序列评估要按时间顺序切分，训练段只用过去的数据；相邻段之间留出间隔以减少序列相关带来的信息泄漏。
- `prohibited_transfers`: 不移植其预测模型与误差度量。
- `applicability`: 扩展窗口逐段样本外与段间间隔的方法来源。
- `known_biases`: 研究对象是预测误差而非交易收益。

## oos-bailey-borwein-lopez-de-prado-zhu-pbo

- `source_kind`: `paper`
- 标题：David H. Bailey, Jonathan Borwein, Marcos López de Prado, Qiji Jim Zhu, *The Probability of Backtest Overfitting*
- `canonical_url`: https://doi.org/10.2139/ssrn.2326253
- `immutable_ref`: `DOI:10.2139/ssrn.2326253`（Crossref 检索核对）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=unknown`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `validation`
- `mechanism_ids`: `selection_overfitting`, `oos_benchmark_comparison`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 样本内最优的参数组在样本外往往落到中位以下；必须用样本外表现而不是样本内最优结果判断。
- `prohibited_transfers`: 组合对称交叉验证（CSCV）本轮不实现。
- `applicability`: 说明为什么只看逐段样本外并与简单基准比较。
- `known_biases`: 需要足够多的参数组合才能估计过拟合概率。

## oos-white-2000

- `source_kind`: `paper`
- 标题：Halbert White, *A Reality Check for Data Snooping*
- `canonical_url`: https://doi.org/10.1111/1468-0262.00152
- `immutable_ref`: `DOI:10.1111/1468-0262.00152`（Crossref 检索核对：Econometrica）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `validation`
- `mechanism_ids`: `oos_benchmark_comparison`, `selection_overfitting`, `parameter_neighbourhood`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 在一组规格中挑出的最好规则，须与基准比较并考虑整组规格的分布，而不是只看最好的那个。
- `prohibited_transfers`: 自助法检验本轮不实现。
- `applicability`: 支持“与简单基准比较”和“看参数邻域整体而不是单点”。
- `known_biases`: 规格集合由研究者定义，集合外的试错不计入。

## oos-sklearn-timeseriessplit

- `source_kind`: `repository`
- 标题：`scikit-learn/scikit-learn` 的 `TimeSeriesSplit`
- `canonical_url`: https://github.com/scikit-learn/scikit-learn/tree/d685424bec8f8585d8017c1aab1db7535d3fa03d
- `immutable_ref`: `commit:d685424bec8f8585d8017c1aab1db7535d3fa03d`
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=BSD-3-Clause`；`license_status=verified`（`COPYING`）
- `reuse_policy`: `adapt_allowed`
- `evidence_roles`: `code`
- `mechanism_ids`: `expanding_window_oos`, `embargo_gap`
- `reviewed_paths`: `sklearn/model_selection/_split.py` 中 `TimeSeriesSplit`（训练集逐段累积、测试段等长、`gap` 在训练末尾与测试开头之间剔除样本、`max_train_size` 可改为滚动窗口）；`COPYING`
- `borrowable_mechanisms`: 测试段从样本末尾向前等长排列；训练段截止于测试开始前 `gap` 个样本。
- `prohibited_transfers`: 不依赖 numpy；按交易日历与年数切分而不是按样本数等分。
- `applicability`: 扩展窗口与间隔的代码对照。
- `known_biases`: 为机器学习样本设计，不处理交易成本与持仓延续。

## oos-backtesting-py-heatmap

- `source_kind`: `repository`
- 标题：`kernc/backtesting.py` 的参数优化热力图
- `canonical_url`: https://github.com/kernc/backtesting.py/tree/ca2e2611621e472542ba90f7243a1fa06a7d7108
- `immutable_ref`: `commit:ca2e2611621e472542ba90f7243a1fa06a7d7108`
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=AGPL-3.0`；`license_status=verified`（`LICENSE.md`）
- `reuse_policy`: `reference_only`
- `evidence_roles`: `code`
- `mechanism_ids`: `parameter_neighbourhood`
- `reviewed_paths`: `backtesting/lib.py` 中 `plot_heatmaps`（按参数对展示优化结果，引导查看最优点附近是否平坦）与 `MultiBacktest.optimize`（`return_heatmap=True` 返回每组参数的结果）
- `borrowable_mechanisms`: 看最优参数附近的结果分布，而不只看最优点。
- `prohibited_transfers`: AGPL 代码不复制、不改写、不依赖；本项目只做逐参数上下一档，不做网格优化。
- `applicability`: 参数邻域检查的实现对照。
- `known_biases`: 热力图本身不给出“敏感”的判定标准。

## oos-inoue-kilian-2005

- `source_kind`: `paper`
- 标题：Atsushi Inoue, Lutz Kilian, *In-Sample or Out-of-Sample Tests of Predictability: Which One Should We Use?*
- `canonical_url`: https://doi.org/10.1081/etc-200040785
- `immutable_ref`: `DOI:10.1081/etc-200040785`（Crossref 检索核对：Econometric Reviews）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `counterevidence`
- `mechanism_ids`: `expanding_window_oos`, `oos_benchmark_comparison`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 样本外检验的功效常低于样本内检验，样本外不显著不等于没有效果；切分点的选择也会影响结论。
- `prohibited_transfers`: 不据此放弃样本外检验。
- `applicability`: 样本外结论只作“不劣于基准”的门槛，并报告逐段结果与切分参数。
- `known_biases`: 以回归预测为对象。
