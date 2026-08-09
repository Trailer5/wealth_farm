# ETF 双动量趋势：外部证据登记

- `strategy_id`: `etf_dual_momentum_trend`
- 版本：`v1`
- 声明机制：`relative_momentum_ranking`、`absolute_momentum_filter`、`defensive_fallback`、`lagged_periodic_rebalance`、`inverse_volatility_weighting`
- 登记日期：`2026-08-09`

本文是开发者审计记录，不是运行配置。当前实现的 126/252/200/60 日窗口、ETF 范围、`top_n` 和波动率权重均须由本项目验证，不能由以下来源直接背书。

## etf-dm-antonacci-ssrn-2042750

- `source_kind`: `paper`
- 标题：Gary Antonacci, *Risk Premia Harvesting Through Dual Momentum*
- `canonical_url`: https://doi.org/10.2139/ssrn.2042750
- `immutable_ref`: `SSRN:2042750`（页面记录最后修订于 2017-05-23）
- `retrieved_at`: `2026-08-09`
- 许可证：`license_spdx=None`；`license_status=unknown`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `validation`
- `mechanism_ids`: `relative_momentum_ranking`, `absolute_momentum_filter`, `defensive_fallback`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 先做相对动量选择，再用相对短债/现金的绝对动量门槛决定风险资产或防御资产；按风险模块组织候选集。
- `prohibited_transfers`: 不复制论文表格、文字或参数；不把论文的资产模块、美国短债替代物和历史绩效直接移植到中国 ETF。
- `applicability`: 支持双动量的机制起点，不支持本项目具体日窗口、交易成本、ETF universe 或逆波动权重。
- `known_biases`: 资产与样本市场差异；历史回测和参数选择偏差；指数可获得性与 ETF 实盘可交易性差异。

## etf-dm-pwb-toolbox

- `source_kind`: `repository`
- 标题：`paperswithbacktest/pwb-toolbox` README 中的 toy dual-momentum 示例
- `canonical_url`: https://github.com/paperswithbacktest/pwb-toolbox
- `immutable_ref`: `git-tree:16ad974b7aaa79d75ad0ecdac45ff1c84d378c47`
- `retrieved_at`: `2026-08-09`
- 许可证：`license_spdx=MIT`；`license_status=verified`（仓库 `LICENSE.txt`）
- `reuse_policy`: `adapt_allowed`
- `evidence_roles`: `code`
- `mechanism_ids`: `absolute_momentum_filter`, `defensive_fallback`, `lagged_periodic_rebalance`
- `reviewed_paths`: `README.md`, `LICENSE.txt`
- `borrowable_mechanisms`: 以先前 bar 形成信号、月度换仓去重、风险/安全资产显式切换、缺失动量值防护。
- `prohibited_transfers`: 不复制其 SPY/BIL universe、252 日参数、Backtrader/PWB 数据依赖、即时成交假设或示例绩效；示例没有实现本项目的跨 ETF 排名和逆波动权重。
- `applicability`: 可作为信号滞后和防御切换的代码审阅样本，仅按 MIT 条款独立改写。
- `known_biases`: README 是最小教学示例，不是经完整成本和多市场验证的生产策略；当前固定的是 tree SHA，不是完整 commit SHA。

如以后直接依赖该仓库，须先补完整 commit SHA 并重新审阅依赖面，之后才可改为 `dependency_allowed`；当前仅允许人工改写。

## etf-dm-bt-inverse-volatility

- `source_kind`: `repository`
- 标题：`pmorissette/bt` 的 `WeighInvVol` 与定期调仓算法
- `canonical_url`: https://github.com/pmorissette/bt/tree/bd5650ad213760907ec38311c5c9468819084196
- `immutable_ref`: `commit:bd5650ad213760907ec38311c5c9468819084196`
- `retrieved_at`: `2026-08-09`
- 许可证：`license_spdx=MIT`；`license_status=verified`
- `reuse_policy`: `adapt_allowed`
- `evidence_roles`: `code`
- `mechanism_ids`: `inverse_volatility_weighting`, `lagged_periodic_rebalance`
- `reviewed_paths`: `bt/algos.py` 中 `WeighInvVol`、`RunMonthly`、`Rebalance`，以及根目录 `LICENSE`
- `borrowable_mechanisms`: 用回看窗口估算波动率、取倒数后归一化权重、把调仓日判断与目标权重计算分层。
- `prohibited_transfers`: 不复制其数据容器、交易对象、默认窗口或撮合假设；本项目继续独立处理ETF停牌、费用、滑点和下一交易日执行。
- `applicability`: 为逆波动率权重和定期调仓提供可审阅的MIT代码机制对照，不证明该权重优于等权。
- `known_biases`: 逆波动率忽略相关性并可能集中于低波动资产；历史波动率窗口和协方差结构会发生变化。

## etf-dm-zakamulin-jam-2014

- `source_kind`: `paper`
- 标题：Valeriy Zakamulin, *The Real-Life Performance of Market Timing with Moving Average and Time-Series Momentum Rules*
- `canonical_url`: https://doi.org/10.1057/jam.2014.25
- `immutable_ref`: `DOI:10.1057/jam.2014.25`
- `retrieved_at`: `2026-08-09`
- 许可证：`license_spdx=None`；`license_status=unknown`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `counterevidence`
- `mechanism_ids`: `absolute_momentum_filter`, `lagged_periodic_rebalance`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 将实际可执行信号与文献回测分开，专门检查规则滞后、交易时点和真实表现。
- `prohibited_transfers`: 不把单一市场、单一趋势规则的反证机械外推为所有双动量策略无效。
- `applicability`: 约束绝对动量/趋势门槛的验证设计，要求无前视并报告相对买入持有的真实增量。
- `known_biases`: 研究对象和本项目 ETF 组合不完全相同；不能单独评价跨截面相对动量或逆波动权重。

## etf-dm-alexjansenhome-gem

- `source_kind`: `repository`
- 标题：`alexjansenhome/GEM`
- `canonical_url`: https://github.com/alexjansenhome/GEM
- `immutable_ref`: `retrieved:2026-08-09; default-branch:master`
- `retrieved_at`: `2026-08-09`
- 许可证：`license_spdx=None`；`license_status=unknown`（检索时仓库根目录未提供 `LICENSE`）
- `reuse_policy`: `reference_only`
- `evidence_roles`: `code`, `validation`
- `mechanism_ids`: `relative_momentum_ranking`, `absolute_momentum_filter`, `defensive_fallback`, `lagged_periodic_rebalance`
- `reviewed_paths`: `README.md`, `gem.py`, `gem_backtest.py`，并核对仓库根目录文件清单
- `borrowable_mechanisms`: 可阅读其条件顺序和数据依赖，用于对照不同社区实现如何表达 GEM。
- `prohibited_transfers`: 不复制、改写或依赖代码、历史 CSV 和参数；公开可见不等于允许复用。
- `applicability`: 仅作为实现对照，帮助独立设计测试和边界；不能作为可复用代码来源。
- `known_biases`: 无许可证导致法律复用边界不明；Yahoo/历史 CSV 的数据质量、幸存者偏差和回测假设需另行核查；没有固定 commit。
