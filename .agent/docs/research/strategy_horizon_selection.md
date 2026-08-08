# 策略与时间尺度

## 时间尺度

- short：不超过 1 个月
- medium_short：1–6 个月
- medium_long：6 个月–3 年
- long：3 年以上

任务 horizon 决定策略候选，不因策略结果修改时间尺度。

## 策略

- short：`trend_timing`
- medium_short：`catalyst_repricing`
- 普通公司 medium_long：`quality_value`
- 普通公司 long：`business_thesis_check`
- ETF medium_long：`etf_tracking_efficiency`
- 金融机构 medium_long：`bank_quality_risk`
- 周期资源 long：`resource_cycle_readiness`
- 黄金 ETF long：`gold_etf_cycle_vehicle`

同时匹配 horizon 和 profile；多项匹配时优先 profile 专用规则。

## 执行

1. `StrategyInputBuilder.build()`
2. 参数化因子提供 filters
3. `StrategyExecutor.execute()`
4. 保存 matched、failed、vetoed、missing、versions、limitations 和 `input_effective_dates`

可交易策略结果缺少输入生效日期时不得进入回测。回测拒绝晚于信号日的指标日期。

## 状态

- pass：规则通过，不是买入建议
- fail：未通过规则
- vetoed：风险否决
- insufficient_data：停止结论

不跨时间尺度汇总 score。所有策略仍是 research rule，不是收益承诺。
