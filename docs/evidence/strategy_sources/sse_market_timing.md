# 上证指数多周期择时：外部证据登记

- `strategy_id`: `sse_market_turning_points`
- 版本：`v1`
- 声明机制：`short_horizon_rsi_reversal`、`medium_horizon_trend_position`、`long_horizon_trend_drawdown`、`long_horizon_valuation_context`、`qvix_volatility_confirmation`
- 登记日期：`2026-08-09`

本文是开发者审计记录，不是运行配置。论文中的 10 个月均线、美国估值区间、VIX 口径和 TA-Lib 默认 RSI 周期都不可照搬为上证指数参数；本项目的日窗口、阈值、权重、估值观察数和 QVIX 分位数必须使用中国市场的点时数据独立验证。

## sse-timing-faber-qtaa

- `source_kind`: `paper`
- 标题：Mebane T. Faber, *A Quantitative Approach to Tactical Asset Allocation*
- `canonical_url`: https://doi.org/10.3905/jwm.2007.674809
- `immutable_ref`: `DOI:10.3905/jwm.2007.674809; SSRN:962461`
- `retrieved_at`: `2026-08-09`
- 许可证：`license_spdx=None`；`license_status=unknown`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `validation`
- `mechanism_ids`: `medium_horizon_trend_position`, `long_horizon_trend_drawdown`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 用价格相对长期均线形成机械趋势状态，并用跨资产/跨市场样本检验规则的可迁移性。
- `prohibited_transfers`: 不复制 10 个月均线、月末成交、现金收益、资产配置和历史绩效参数；不把美国及全球资产结果当成上证指数结论。
- `applicability`: 提供中长期趋势过滤的研究起点，并要求样本外验证。
- `known_biases`: 市场制度、交易日频率和现金工具不同；原始规则与本项目多周期转折识别并不等价；更新稿含事后样本。

## sse-timing-campbell-shiller

- `source_kind`: `paper`
- 标题：John Y. Campbell and Robert J. Shiller, *Valuation Ratios and the Long-Run Stock Market Outlook*
- `canonical_url`: https://doi.org/10.3905/jpm.24.2.11
- `immutable_ref`: `DOI:10.3905/jpm.24.2.11`
- `retrieved_at`: `2026-08-09`
- 许可证：`license_spdx=None`；`license_status=unknown`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `validation`
- `mechanism_ids`: `long_horizon_valuation_context`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 把估值比率限制在长期收益背景和情景解释中，而非短期精确择时。
- `prohibited_transfers`: 不照搬美国 CAPE/P-E 历史均值、分位阈值、预测系数或期限；不把高估值直接转换成即时卖出信号。
- `applicability`: 支持“估值只作为长周期上下文”的设计边界。
- `known_biases`: 主要证据来自美国长期市场；会计口径、指数成分、无风险利率和中国结构性变迁不同；长期关系不能证明短期拐点。

## sse-timing-talib-rsi

- `source_kind`: `repository`
- 标题：TA-Lib Core C Library 的 RSI 源码
- `canonical_url`: https://github.com/TA-Lib/ta-lib/blob/85a3b3f2a47fc28b8ab2eec2e3f1402764b57aa3/ta_func_defs/rsi/rsi.c
- `immutable_ref`: `commit:85a3b3f2a47fc28b8ab2eec2e3f1402764b57aa3`
- `retrieved_at`: `2026-08-09`
- 许可证：`license_spdx=BSD-3-Clause`；`license_status=verified`（仓库 `LICENSE`）
- `reuse_policy`: `adapt_allowed`
- `evidence_roles`: `code`
- `mechanism_ids`: `short_horizon_rsi_reversal`
- `reviewed_paths`: `ta_func_defs/rsi/rsi.c`, `ta_codegen_output/c/ta_RSI.c`, `LICENSE`
- `borrowable_mechanisms`: Wilder RSI 的上涨/下跌分离、初始平均值、递推平滑、零损失边界和 lookback 处理。
- `prohibited_transfers`: 不复制 C 接口、代码生成层、兼容模式、unstable-period 全局状态或默认 14 日阈值；改写时履行 BSD-3-Clause 的许可证通知义务。
- `applicability`: 可用于核对本项目独立 Python RSI 计算的边界和递推语义，不代表 RSI 超买超卖阈值在上证指数有效。
- `known_biases`: 指标实现只保证算法语义，不提供择时有效性；不同初始化、缺失值和复权输入会产生差异。

## sse-timing-zakamulin

- `source_kind`: `paper`
- 标题：Valeriy Zakamulin, *Revisiting the Profitability of Market Timing with Moving Averages*
- `canonical_url`: https://doi.org/10.1111/irfi.12132
- `immutable_ref`: `DOI:10.1111/irfi.12132; SSRN:2743119`
- `retrieved_at`: `2026-08-09`
- 许可证：`license_spdx=None`；`license_status=unknown`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `counterevidence`
- `mechanism_ids`: `medium_horizon_trend_position`, `long_horizon_trend_drawdown`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 把前视偏差检查、真实交易时点和相对买入持有的统计区别纳入验收。
- `prohibited_transfers`: 不把特定错误回测的反驳扩大为所有趋势状态都无信息；也不把边际优势包装成确定择时能力。
- `applicability`: 直接约束均线/趋势模块的模拟方式和结论强度。
- `known_biases`: 反驳对象与本项目多信号规则不同；其样本市场不能替代中国市场的滚动样本外检验。

## sse-timing-whaley-vix

- `source_kind`: `paper`
- 标题：Robert E. Whaley, *Understanding the VIX*
- `canonical_url`: https://doi.org/10.3905/JPM.2009.35.3.098
- `immutable_ref`: `DOI:10.3905/JPM.2009.35.3.098; SSRN:1296743`
- `retrieved_at`: `2026-08-09`
- 许可证：`license_spdx=None`；`license_status=unknown`
- `reuse_policy`: `reference_only`
- `evidence_roles`: `mechanism`, `validation`
- `mechanism_ids`: `qvix_volatility_confirmation`
- `reviewed_paths`: 无（论文）
- `borrowable_mechanisms`: 将期权隐含波动率理解为市场预期波动的状态变量和确认信息，而非方向预测本身。
- `prohibited_transfers`: 不照搬 VIX 的 SPX 期权口径、30 日年化水平、绝对阈值或“恐慌指数”叙事到 QVIX；不把高隐波等同于必然反转。
- `applicability`: 仅支持 QVIX 作为短周期风险/情绪确认变量的解释框架。QVIX 基于中国期权市场，与 VIX 的标的、流动性、期限插值和制度环境不同。
- `known_biases`: QVIX 不是官方 VIX 的同一序列；期权供需、波动率风险溢价、交易限制和方法变更会影响可比性；论文不验证上证指数阈值。

## 参数迁移门禁

以上来源只能支持机制候选和实现审阅。任何外部窗口、阈值、资产替代、分位数、权重或再平衡频率进入 `v1` 前，都必须有本项目的参数声明、点时数据、无前视测试、成本假设和滚动/分块样本外结果；否则只能记录为 `reference_only`，不能宣称来源已经验证该参数。
