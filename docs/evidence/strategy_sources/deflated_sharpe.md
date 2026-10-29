# 折扣夏普比率与最短回测长度：外部证据登记（方法，非交易策略）

- `strategy_id`: `deflated_sharpe_significance`（回测显著性方法）
- 版本：`v1`
- 声明机制：`probabilistic_sharpe_ratio`、`non_normal_sharpe_variance`、`expected_maximum_sharpe`、`deflated_sharpe_ratio`、`minimum_track_record_length`、`minimum_backtest_length`
- 登记日期：`2026-10-29`

本文是开发者审计记录。代码记录在 `src/backtest/significance_evidence.py`，实现于 `src/backtest/significance.py`。

## dsr-bailey-lopez-de-prado-2014

- `source_kind`: `paper`
- 标题：David H. Bailey, Marcos López de Prado, *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality*
- `canonical_url`: https://doi.org/10.3905/jpm.2014.40.5.094
- `immutable_ref`: `DOI:10.3905/jpm.2014.40.5.094`（Crossref 核对：The Journal of Portfolio Management 40(5), 94–107）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`
- `mechanism_ids`: `expected_maximum_sharpe`, `deflated_sharpe_ratio`, `non_normal_sharpe_variance`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 在 N 次尝试中挑出的最佳夏普，其零假设下的期望最大值约为 尝试间夏普标准差 ×〔(1−γ)Φ⁻¹(1−1/N) + γΦ⁻¹(1−1/(Ne))〕；以此为门槛计算考虑偏度与峰度的概率夏普，即折扣夏普比率。
- `prohibited_transfers`: 不复制论文的数值示例；独立试验数的估计（按尝试间相关性折算）本项目暂不实现，直接用不同参数组合数，结果偏保守。
- `applicability`: 回测显著性的主方法。
- `known_biases`: 只能折扣系统内登记的尝试；研究者在系统外的试错无法计入。

## psr-bailey-lopez-de-prado-2012

- `source_kind`: `paper`
- 标题：David Bailey, Marcos López de Prado, *The Sharpe Ratio Efficient Frontier*
- `canonical_url`: https://doi.org/10.21314/JOR.2012.255
- `immutable_ref`: `DOI:10.21314/JOR.2012.255`（Crossref 核对：Journal of Risk 15(2), 3–44）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`
- `mechanism_ids`: `probabilistic_sharpe_ratio`, `non_normal_sharpe_variance`, `minimum_track_record_length`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 夏普估计量的标准差 √((1 − γ₃·SR + (γ₄−1)/4·SR²)/(n−1))；概率夏普 Φ((SR−SR*)/σ)；达到给定置信度所需的最短记录长度。
- `prohibited_transfers`: 不把论文的有效前沿与配置方法带入本项目。
- `applicability`: 概率夏普与最短记录长度的公式来源。
- `known_biases`: 假设收益独立同分布；日收益的自相关会让标准差偏小。

## minbtl-bailey-borwein-lopez-de-prado-zhu-2014

- `source_kind`: `paper`
- 标题：David H. Bailey, Jonathan M. Borwein, Marcos López de Prado, Qiji Jim Zhu, *Pseudo-Mathematics and Financial Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample Performance*
- `canonical_url`: https://doi.org/10.1090/noti1105
- `immutable_ref`: `DOI:10.1090/noti1105`（Crossref 检索核对：Notices of the American Mathematical Society）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `validation`
- `mechanism_ids`: `minimum_backtest_length`, `expected_maximum_sharpe`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 最短回测长度 MinBTL ≈（期望最大标准正态 / 观测年化夏普）² 年：尝试越多，需要越长的样本才能让观测夏普不被纯运气解释。
- `prohibited_transfers`: 不移植论文的示例参数。
- `applicability`: 给出“样本是否够长”的判断。
- `known_biases`: 零假设为真实夏普为零、尝试相互独立。

## dsr-rubenbriones-psr

- `source_kind`: `repository`
- 标题：`rubenbriones/Probabilistic-Sharpe-Ratio`
- `canonical_url`: https://github.com/rubenbriones/Probabilistic-Sharpe-Ratio/tree/7c65ea513df575a93a3a474b6e3c2a0a9e724607
- `immutable_ref`: `commit:7c65ea513df575a93a3a474b6e3c2a0a9e724607`
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=unknown`（检索时仓库没有 LICENSE 文件）
- `reuse_policy`: `reference_only`
- `evidence_roles`: `code`
- `mechanism_ids`: `probabilistic_sharpe_ratio`, `non_normal_sharpe_variance`, `expected_maximum_sharpe`, `deflated_sharpe_ratio`, `minimum_track_record_length`
- `reviewed_paths`: `src/sharpe_ratio_stats.py`（`estimated_sharpe_ratio_stdev`、`probabilistic_sharpe_ratio`、`min_track_record_length`、`num_independent_trials`、`expected_maximum_sr`、`deflated_sharpe_ratio`）
- `borrowable_mechanisms`: 用来核对公式与本项目独立实现的一致性（峰度取非超额值、尝试间夏普标准差用样本标准差、欧拉常数 0.5772156649）。
- `prohibited_transfers`: 没有许可证，不复制、改写或依赖其代码；本项目用标准库 `statistics.NormalDist` 独立实现。
- `applicability`: 公式对照。
- `known_biases`: 示例仓库，测试覆盖有限。

## dsr-harvey-liu-zhu-2016

- `source_kind`: `paper`
- 标题：Campbell R. Harvey, Yan Liu, Heqing Zhu, *… and the Cross-Section of Expected Returns*
- `canonical_url`: https://doi.org/10.1093/rfs/hhv059
- `immutable_ref`: `DOI:10.1093/rfs/hhv059`（Crossref 核对：Review of Financial Studies 29(1), 5–68）
- `retrieved_at`: `2026-10-29`
- 许可证：`license_spdx=None`；`license_status=restricted`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `counterevidence`
- `mechanism_ids`: `expected_maximum_sharpe`, `deflated_sharpe_ratio`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 多重检验下显著性门槛应远高于常规（t 约 3 以上）；不同多重检验方法给出的门槛不同，尝试数只计入可观察的部分。
- `prohibited_transfers`: 不把其因子研究门槛直接当作策略门槛。
- `applicability`: 说明折扣只是下限：系统外的试错、文献中已被挑选过的规则都未计入，显著性仍可能被高估。
- `known_biases`: 研究对象是横截面因子。
