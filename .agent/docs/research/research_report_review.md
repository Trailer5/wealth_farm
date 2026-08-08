# 研究报告与复查

## 正式入口

使用 `ResearchReportAssemblyService.assemble()` 将正式执行结果和可选同行结果转换为报告。服务不调用模型补写事实。

## 报告结构

1. 任务、profile、问题、horizon、截止日期
2. 数据覆盖、质量、来源和失败
3. 指标、单位、报告期和版本
4. 图表 artifact 与 digest
5. 基准、行业和同行
6. 策略状态、条件和限制
7. 乐观、基准和悲观情景
8. 最强反方和失效条件
9. gap
10. 非投资建议声明

## 声明类型

- fact：必须有 evidence ref
- inference：解释推导
- assumption：未验证情景
- unknown：证据不足
- not_applicable：profile 不适用

不得把 inference/assumption 写成 fact，也不得隐藏 unknown。

## 情景与反方

三情景使用同一 horizon。反方优先使用策略 counterevidence、failed/vetoed 条件、官方风险和同行反例。没有反方证据时引用策略限制，不写伪反方。

失效条件必须可观察并区分 warning 与 thesis_break。

## 复查清单

- 身份和 profile 正确
- 信息不晚于 `as_of_date`
- 没有使用不适用指标
- 单位、报告期和版本一致
- 图表可追溯
- 策略匹配 horizon
- `insufficient_data` 未被写成 fail
- 来源失败未遗漏
- fact 有证据
- 无收益承诺

黄金 ETF 报告必须分开载体准备度与黄金方向；没有宏观和实际利率证据时方向为 unknown。
