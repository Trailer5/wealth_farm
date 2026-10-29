# 策略（“这个策略好不好”）

能力：策略档案、生命周期、常见策略模板、单次正式回测、完整验证（滚动样本外、参数邻域、显著性折扣、分市场状态表现）与前向跟踪。历史成分选股样本（阶段 10）未接入，按路由缺口回答。

1. 请求：`subject_type=strategy`，`subject_refs` 写策略 id 或登记名，`intents=["evaluate"]`，`personalization=none`（不写 `user_view`）。`run-recipe` 一次跑完档案、完整验证、分市场状态、折扣显著性与前向记录；策略没登记时它返回 `needs_input` 和可用模板，按第 3 步登记后重跑。路由里 `samples` 的 partial 只对选股策略成立，以 `selection_samples` 步骤的结果为准。下面第 2–5 步是分步入口，用于追问或单独查看。
2. 找策略：`strategy-archive` 列出已登记策略和状态；`strategy-archive --strategy-id <id>` 看档案、版本和事件。用户口述的策略先按“模板 + 标的”找已有登记，有就直接用。字段与读法见 `strategy-archive --help`。
3. 用户说的策略没有登记：`strategy-archive --templates` 找对应模板，用 `strategy-archive --from-template <模板> --strategy-id <id> --asset ... --set ...` 登记（来源已登记，直接进入“验证中”）。用户说的是指数时用跟踪它的可交易 ETF 登记，在回答里说明代理关系即可。对不上任何模板或落在 `not_expressible` 时，按缺口说明哪部分无法回测，不硬套；确有完整机制、来源与失效条件的新策略才写 JSON 用 `--register-json` 登记（从“研究”开始）。
4. 回测：只问单次表现用 `strategy-backtest --strategy-id <id>`；问“好不好、能不能用”用 `strategy-evaluate --strategy-id <id>`（滚动样本外、参数邻域、折扣显著性，并记为验证记录）。读法见各自 `--help`；没有信号实现时命令会说明，按缺口回答。
5. 前向：完整验证后 `next_blockers` 为空时，直接用 `forward_tracking` 步骤 `next_actions` 里的命令迁到“前向跟踪”（`--evidence-ref` 写 `evaluation_id`，理由写四项验证的结论与 DSR），并在回答里说明——迁移只是开始记录信号，不是交易，不必先问用户；“启用”同样按门槛迁移，退役只在失效条件触发时做。处于“前向跟踪”或“启用”的策略，用 `strategy-track --strategy-id <id>` 记录当天信号并取前向记录（`routine` 也会跑）；问“它最近的信号是什么、前向表现如何”时用它。
6. 作答：先说生命周期状态和它意味着什么，再说机制、来源、适用范围与失效条件；有完整验证时按「样本外 → 参数敏感性 → 折扣显著性 → 分市场状态」的顺序写，再写全样本与简单基准的对比、成本与容量；分市场状态不解读 `exposure_driven` 列出的维度，用 `current` 说现在处于哪一档。有前向记录时再写跟踪起始日、已结算信号数与命中率区间和当前信号（`position`）。只有单次回测时写与四个基准的对比、回撤、成本与容量和 `significance.verdict`；单次回测不是有效性证明，未接入的验证项按路由缺口说明。
