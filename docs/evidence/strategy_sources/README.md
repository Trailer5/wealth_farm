# 策略外部证据登记

本目录是策略来源的开发者审计记录，项目版本统一为 `v1`。新策略必须先完成来源登记和机制对照，再通过 `StrategyEvidenceGate`，之后才能开始正式编码；已有策略补录时也按同一门禁复核。

## 编码前流程

1. 先声明稳定的 `strategy_id` 和逐项 `mechanism_ids`，不得用“整体参考某策略”代替机制拆分。
2. 登记论文、仓库、文档的规范 HTTPS URL、永久标识或固定引用、检索日期、许可证状态、审阅路径、可借鉴机制、禁止迁移项、适用性和已知偏差。
3. 许可证只决定能否复制、改写或作为依赖，不决定能否阅读。没有许可证的公开代码可以作为 `reference_only` 帮助理解实现差异，但不得复制、改写或依赖。
4. 调用 `StrategyEvidenceGate.assess(...)`。每个声明机制都要有记录覆盖，并且整体至少具备一项论文机制来源、一项已审阅具体路径的公开实现和一项反证来源。
5. 门禁状态为 `implementation_ready` 后才进入正式策略实现。门禁只给出缺口，不计算固定记录数量之外的“质量分”。

当项目需要复制、改写或直接依赖外部代码时，才额外要求许可证核验；直接依赖还必须固定完整 commit SHA。若只从论文和公开实现理解机制后独立编码，许可证不是实现门槛，但来源、差异和未采用部分仍须记录。

## 运行与展示边界

- 本目录的 Markdown 仅供人工审计，不参与运行。代码记录由调用方构造，单元测试记录只使用 fixture，不解析文档文本。
- `evidence_id`、`immutable_ref`、许可证状态、复用策略、审阅路径、机制 ID、偏差清单和门禁诊断均为内部开发/审计字段。
- 面向用户的研究报告不展示上述内部来源审计字段。用户只看到经门禁约束后的结论、关键公开依据、适用范围和必要限制。
- 来源登记不等于实证有效性证明；策略仍须执行无前视、含成本、与目标市场及时间尺度匹配的验证。

当前登记：

- [ETF 双动量趋势](etf_dual_momentum.md)
- [上证指数多周期择时](sse_market_timing.md)
- 策略模板（代码记录在 `src/strategy/template_evidence.py`）：[均线择时](ma_timing.md)、[估值分位择时](valuation_timing.md)、[因子选股](factor_selection.md)、[定期再平衡与分批建仓](rebalance_staged_entry.md)；双动量轮动模板沿用 [ETF 双动量趋势](etf_dual_momentum.md)
- [折扣夏普比率与最短回测长度（回测显著性方法，非交易策略）](deflated_sharpe.md)
- [滚动样本外与参数邻域（验证方法，非交易策略）](rolling_oos_neighbourhood.md)
- [分市场状态表现与夏普差检验（验证方法，非交易策略）](market_regimes.md)
- [风险预算仓位](position_budget.md)
- [事件—名称注意力外溢发现](event_name_attention_discovery.md)
- [内在价值与隐含预期（估值方法，非交易策略）](valuation_models.md)
- [高成长未盈利就绪度规则（研究规则，非交易策略）](growth_runway.md)
- [组合配置与风险预算（配置方法，非交易策略）](allocation_risk_budget.md)
