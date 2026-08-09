# AI API 与 harness 模块

## 模块职责

AI harness 负责把用户研究问题转换为结构化任务，装配项目数据和证据，调用可替换模型 Adapter，并保存输入、输出和引用关系。

第一阶段只建立模型无关的数据流，不接真实模型供应商。

## 不负责的内容

第一阶段不负责：

1. OpenAI、Claude 或本地模型的正式接入。
2. Prompt 优化和多 Agent 编排。
3. 流式对话和 Web 聊天页面。
4. 向量数据库和长期记忆。

AI harness 不负责抓取新闻，也不应替代代码、正式名称和唯一别名等确定性关联。未来只对事件与知识模块提供的模糊候选执行结构化相关性分类。

## 目录与脚本范围

### 拥有

1. `src/ai_harness/`：任务模型、上下文装配、证据、Adapter 协议和持久化服务。
2. `src/tests/ai_harness/unit_tests/`：上下文、证据、模型 Adapter 和用户归属测试。
3. `src/tests/ai_harness/smoke_tests/`：完整研究任务验证。

### 依赖

1. 用户空间和研究报告。
2. 主数据、因子、事件和组合模块。
3. 后续 Web API 与模型供应商 Adapter。

## 第一阶段任务

第一阶段只支持 `asset_analysis`：

1. 用户问题关联一个 `asset_id`。
2. 装配主数据、因子、事件、用户报告和持仓证据。
3. 每条证据拥有稳定 `evidence_id`、类型、来源 ID、标题和内容。
4. 模型 Adapter 接收结构化任务和证据。
5. 保存模型名称、结构化结果、错误和证据引用。

## 可追溯要求

任务必须保存原始问题、用户、标的、上下文快照、模型名称和结果。

模型输出中的引用必须使用已提供的 `evidence_id`。harness 不允许保存指向不存在证据的引用。

用于信息关联时，模型输出必须区分 `related`、`unrelated` 和 `needs_review`，并返回 relation type、confidence、命中证据和候选 `asset_id`。模型判断不能直接改写主数据，也不能绕过人工确认状态。

## 当前能力与缺口

当前已建立 `src/ai_harness/`、AI 任务、证据快照、模型 Adapter、显式工具协议、工具调用记录、引用校验、事实/推断/建议 claims、错误状态和结果持久化。OpenAI-compatible Chat Completions Adapter 已实现，配置只从环境变量读取。Web API 和工作台已支持任务创建、查询、结构化结果、证据、工具记录和显式执行。

黄金 ETF 真实 smoke 已装配 9 条证据，覆盖标的、因子、事件、持仓和报告；测试 Adapter 完成任务并保存有效引用。

当前缺口是真实模型凭据与 smoke、prompt 模板、流式输出、多轮对话、自主工具循环，以及面向模糊信息关系的受控分类任务。

AI 任务 API 阶段已完成，当前没有独立的 AI 模块短期开发计划。
