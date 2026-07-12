# Skill 与 Agent 能力构建标准框架

本文档抽象一套通用的 Agent 能力构建标准，用于指导在 Claude Code、Cursor、VS Code Copilot、Codex 等环境中选择 Skill、Agent 或 Sub-agent 形态，并编写、维护和评估可复用能力包。这里的 Skill 指“按需加载的一组任务知识”：它通常由一个 `SKILL.md` 入口文件，以及可选的脚本、参考文档、模板、示例和测试用例组成。

一个优秀的可复用能力包不只是提示词集合，而是把某类重复工作沉淀成稳定、可发现、可执行、可验证、可维护的工作流。

> **核心结论**
>
> 跨平台默认优先选择 **Skill** 或 **Sub-agent / delegated agent**：
>
> - 需要当前主 Agent 获得专家能力、工作流或角色化任务模式：优先写 **Skill**。
> - 需要隔离上下文、并行处理或只要一次性结论：写 **Sub-agent / delegated agent**。
> - 普通 Agent / custom agent 只作为平台增强：仅在 VS Code Copilot 等明确支持身份切换的平台，或 Codex 这类可通过 profile/default config/启动参数明确指定角色的平台上提供。

## 阅读导引

| 如果你想解决的问题是 | 优先阅读 |
| --- | --- |
| 先判断该做 Skill、Agent 还是 Sub-agent | [1. 构建准备](#1-构建准备skillagentsub-agent-的理论区别)、[2. 跨平台定位原则](#2-跨平台定位原则) |
| 编写一个高质量 Skill | [3. 标准目录结构](#3-标准目录结构) 到 [18. 质量检查清单](#18-质量检查清单) |
| 处理 Claude / Cursor / VS Code / Codex 差异 | [19. 各家兼容方案](#19-各家兼容方案)、[20. 各家部署方式](#20-各家部署方式) |
| 快速确认最终原则 | [21. 推荐结论](#21-推荐结论) |

---

## 1. 构建准备：Skill、Agent、Sub-agent 的理论区别

在开始构建可复用 Agent 能力之前，先区分三种常见抽象：

- **Skill**：给当前主 Agent 加载的一套能力包。它通常包含任务流程、领域知识、参考资料、脚本、模板、示例和验证方法。Skill 关注“当前执行者该如何完成这类任务”。
- **Agent**：一个命名执行者或角色定义。它通常描述 persona、职责范围、工具权限、行为边界和输出契约。Agent 关注“谁来执行这类任务”。
- **Sub-agent / delegated agent**：被主 Agent 委派调用的独立执行上下文。它通常处理边界清楚的一次性子任务，完成后把结论返回给父 Agent。Sub-agent 关注“把哪部分工作隔离出去执行”。

理论上，三者的职责可以这样区分：

| 抽象 | 核心问题 | 典型使用场景 | 用户期望 |
| --- | --- | --- | --- |
| Skill | 当前 Agent 怎么做 | 多步工作流、领域知识包、脚本化能力、角色化任务模式 | 当前对话里的主 Agent 获得一套能力 |
| Agent | 谁来做 | 专家角色、固定工具权限、长期工作模式、独立行为边界 | 用户选择或启动一个专门执行者 |
| Sub-agent | 哪部分交给别人做 | 并行探索、隔离上下文、专项审查、一次性分析 | 父 Agent 获得一个可整合的最终结论 |

> **判断口径**
>
> - Skill 不是“弱 Agent”，而是把能力加载到当前主 Agent。
> - Agent 不是“更高级 Skill”，而是平台层面的命名执行者或角色。
> - Sub-agent 不是“长期专家模式”，而是一次或一段委派工作的隔离执行上下文。

Skill 适合承载：

- 多步、重复、专业化的任务流程。
- 需要特定领域知识、项目约束、操作顺序或验收标准的工作。
- 需要捆绑脚本、模板、参考文件、示例输入输出的任务。
- 需要在不同对话中反复复用，而不适合每次都重新解释的工作模式。
- 需要让当前主 Agent 临时担任某种专家角色，但不要求真正替换宿主 Agent 底层身份的场景。

Agent 适合承载：

- 平台明确支持用户选择或启动的专家角色。
- 需要长期固定 persona、职责边界、输出风格或工具权限的工作模式。
- 需要与默认 Agent 明显不同的执行者身份，且目标平台能稳定表达这种身份切换。

Sub-agent 适合承载：

- 输入明确、输出明确、过程不需要暴露给用户的专项任务。
- 需要独立上下文，避免污染主对话的探索、审查、检索或验证任务。
- 可以由父 Agent 整合结果，而不需要直接面向最终用户持续对话的任务。
- 通常不应依赖主对话的完整上下文和长期记忆；如平台支持 resume 或上下文传递，应把它作为显式机制处理。

以下内容通常不应做成 Skill：

- 所有任务都必须遵守的项目级通用规范。此类内容更适合作为 `AGENTS.md`、`copilot-instructions.md`、Cursor Rules 或全局 instructions。
- 单次、简单、参数化的任务模板。此类内容更适合作为 prompt file。
- 必须被确定性执行或强制拦截的策略。此类内容更适合作为 hooks。
- 必须严格隔离上下文、限制工具权限，并只返回一次性结论的任务。此类内容更适合作为 Sub-agent。

---

## 2. 跨平台定位原则

现实情况是，不同 IDE/CLI 对 agent、custom agent、sub-agent、task agent 的实现差异很大。一般用户或普通项目开发者很难通过用户级配置，在所有平台上稳定地把宿主 IDE 自带的主 Agent 切换成自己的主 Agent，并同时强制固定 persona、固定工具权限、长期专家模式和完全不同的行为边界。

> **重要时点说明**
>
> 以下判断基于截至 **2026.05.26** 可观察到的各家平台实现、官方文档和工程实践。Agent / Skill / Sub-agent 相关能力仍在快速演进，后续版本可能改变这些结论。

当前普通 Agent 在跨平台部署时经常会出现两种退化：

- 在 Claude Code、Cursor、Codex 等环境中，用户级 agent definition 或 `agents/` 目录下的角色文件通常更偏 delegated sub-agent、slash task-agent、spawned session 或一次性任务 Agent，而不是稳定替换当前主 Agent。
- 在不稳定支持主 Agent 身份切换的环境中，普通 Agent 的 persona、流程和输出约束，本质上会退化为“让当前主 Agent 遵循的一组 instructions”，也就是 Skill 更擅长承载的约束范畴。

因此，从工程稳定性考虑，跨平台标准不建议把“custom agent”作为默认抽象。推荐把可复用能力基本收敛为二选一：

- **Skill**：让当前主 Agent 读取并使用这套能力。
- **Sub-agent / delegated agent**：让独立上下文处理一次委派任务并返回结论。

普通 Agent / custom agent 可以作为平台特性使用，但不作为跨平台默认路径。

这里的“二选一”指默认发行形态和用户心智模型，不表示运行时绝不能组合。部分平台允许 Skill 触发或预装到 sub-agent 中执行；这种组合应作为高级平台能力记录在部署说明里，而不是改变跨平台默认判断。

| 目标 | 推荐形态 | 原因 |
| --- | --- | --- |
| 让当前主 Agent 获得一套专业能力或临时专家角色 | Skill | Skill 会被当前主 Agent 读取和使用，跨平台语义相对稳定，也可以承载 persona、流程、工具和输出契约。 |
| 隔离上下文，只关心一次委派的最终结论 | Sub-agent / delegated agent | 独立执行后返回结果，适合代码库探索、专项分析、并行审查。 |
| 用户直接进入某个专家角色 | 平台普通 Agent / custom agent | 仅在平台明确支持选择 active custom agent、agent profile 或等价启动机制时使用；当前 VS Code Copilot 最明确，Codex 需按版本和启动方式确认。 |
| 所有任务默认遵守某些规则 | Instructions / Rules | 常驻上下文，适合项目级或用户级通用规范。 |

> **推荐决策**
>
> | 目标 | 推荐 |
> | --- | --- |
> | 主上下文能力和角色化工作模式 | Skill |
> | 隔离执行能力 | Sub-agent / delegated agent |
> | 专家角色切换 | 平台明确支持时再提供 custom agent |
> | 强制策略 | hooks 或平台权限机制 |

需要注意：把能力做成 Skill 并不等于真正替换主 Agent 的底层身份。Skill 能让当前主 Agent 在某类任务中临时担任专家角色，遵循特定流程、读取特定知识、调用特定工具并输出特定格式；但宿主 Agent 的底层身份、工具权限和会话生命周期仍由平台控制。对大多数跨平台专家助手来说，这已经足够接近“主 Agent 专家模式”，且比 custom agent 更稳定。

---

## 3. 标准目录结构

推荐目录结构如下：

```text
skill-name/
├── SKILL.md
├── references/
│   ├── overview.md
│   └── scenario-a.md
├── scripts/
│   ├── validate.py
│   └── helper.py
├── assets/
│   └── template.md
├── examples/
│   └── example-1.md
└── evals/
    ├── evals.json
    └── files/
```

各目录职责：

- `SKILL.md`：唯一必需文件，负责描述 Skill 的身份、触发场景、核心流程、路由规则和最小可用指导。
- `references/`：存放按需阅读的详细知识、场景指南、API 说明、领域规则。适合承载长文档。
- `scripts/`：存放可执行脚本，用于确定性、重复性或易出错操作，例如校验、转换、生成、聚合。
- `assets/`：存放模板、样例资源、静态文件、字体、表单、配置片段等输出材料。
- `examples/`：存放真实或近真实的输入输出示例，帮助 Agent 理解质量标准。
- `evals/`：存放测试提示、输入文件、期望结果和断言，用于验证 Skill 是否真的改善任务表现。

目录命名准则：

- 使用小写字母、数字和连字符，例如 `pdf-processing`、`code-review`。
- 避免 `helper`、`utils`、`tools` 这类无法表达用途的泛名。
- Skill 目录名应与 frontmatter 中的 `name` 保持一致。
- 文件引用尽量从 `SKILL.md` 一层直达，例如 `references/scenario-a.md`，避免多层跳转。

---

## 4. `SKILL.md` 文件职责

`SKILL.md` 是 Skill 的入口，不应承担所有知识的存放职责。它的核心职责是：

1. 声明 Skill 元数据，让 Agent 能发现并判断是否加载。
2. 说明何时使用、何时不要使用。
3. 给出最小可执行流程，让 Agent 加载后能立刻开始工作。
4. 根据任务类型路由到更具体的参考文件、脚本或模板。
5. 明确安全边界、确认边界、验证方式和输出要求。

`SKILL.md` 不应：

- 复制完整 README、官方文档或长篇背景材料。
- 放入大量“Agent 本来就知道”的通用解释。
- 同时覆盖过多互不相关的任务。
- 只写理念，不给操作步骤、决策点或验收标准。
- 把发现触发规则只写在正文里，而不放进 `description`。

如果用户提供了希望写入 Skill 的精确措辞，应按原文保留，不随意改写、柔化或扩展。只有在措辞存在明显安全、事实或平台兼容问题时，才先向用户说明风险并请求确认。

---

## 5. Frontmatter 标准

推荐基础格式：

```yaml
---
name: skill-name
description: "做什么，以及什么时候使用。Use when: 具体触发场景、关键词、近义表达。"
---
```

常见可选字段：

```yaml
argument-hint: "用户通过 slash command 调用时显示的参数提示"
user-invocable: true
disable-model-invocation: false
```

字段准则：

- `name` 必须短、准、稳定，通常不超过 64 个字符。
- `description` 是 Skill 的发现入口，应同时包含“做什么”和“何时使用”。
- `description` 建议使用第三人称或客观描述，不写“我可以帮你”。
- 当 Skill 需要自动触发时，`description` 要包含具体触发词、任务场景和近义表达。
- 当 Skill 只应手动调用时，可以设置 `disable-model-invocation: true`。
- 如果 YAML 中包含冒号、复杂标点或较长文本，优先给字符串加引号。
- 不要把完整流程摘要塞进 `description`；它负责“发现和触发”，流程细节应放在正文。

高质量 `description` 示例：

```yaml
description: "Create, evaluate, and improve reusable Agent Skills. Use when the user wants to write SKILL.md, package a workflow as a skill, improve skill triggering, add evals, or compare skill performance."
```

低质量 `description` 示例：

```yaml
description: "A helpful skill for docs."
```

---

## 6. `SKILL.md` 内部布局

推荐布局：

```markdown
---
name: skill-name
description: "..."
---

# Skill Title

## Purpose
一句话说明该 Skill 解决的问题。

## When To Use
列出典型触发场景。

## When Not To Use
列出边界和替代机制。

## Quick Start
给出最小可执行步骤。

## Workflow
按阶段描述完整流程、决策点和分支。

## References
说明何时读取哪些参考文件。

## Scripts And Assets
说明哪些脚本可执行、哪些资源可引用。

## Validation
说明如何检查结果是否正确。

## Output
说明最终回复或产物格式。
```

对于项目级或复杂 Skill，可采用“入口索引 + 场景文件”的结构：

```markdown
# Project Skill Index

## Required First Reads
1. Read this file first.
2. Select the smallest relevant scenario file.

## Scenario Routing
- For test creation, read `references/new-test.md`.
- For execution diagnosis, read `references/execution.md`.
- For cross-layer changes, read `references/global.md`.

## Default Boundaries
State what the Agent should avoid unless explicitly requested.
```

这种结构的关键是：入口文件只负责路由和边界，场景文件负责细节。

---

## 7. Progressive Disclosure 准则

Skill 应遵循渐进加载：

1. 元数据：`name` 和 `description` 始终可见，用于发现。
2. `SKILL.md`：Skill 触发后加载，承载最小必要流程。
3. 资源文件：只有在具体任务需要时才读取或执行。

写作准则：

- `SKILL.md` 尽量控制在 500 行以内。
- 详细背景、长表格、平台差异、API 说明放入 `references/`。
- 脚本逻辑放入 `scripts/`，不要把长代码直接嵌入正文。
- 每个引用文件应有明确使用条件，例如“仅当用户要求部署时读取”。
- 引用文件保持一层可达，避免 `SKILL.md -> A -> B -> C` 的深链路。

成熟项目级 Skill 的常见模式：

- `SKILL.md`：总索引和路由表。
- `common.md`：项目地图和稳定接口。
- `scenario-x.md`：单一场景的详细规则。
- `workflow.md`：跨场景任务编排。
- `tooling.md`：工具、部署、环境、诊断等特殊能力。

---

## 8. 工作流设计准则

Skill 应把任务转化为 Agent 可执行的流程。推荐包含：

- 起始条件：需要先确认什么信息。
- 决策点：不同输入或风险下走哪条路径。
- 操作步骤：按顺序完成哪些动作。
- 工具选择：优先使用什么脚本、接口、命令或 API。
- 验证方式：如何证明结果正确。
- 失败处理：常见失败如何诊断、何时停下来询问用户。
- 输出格式：最终如何向用户汇报。

好的流程示例：

```markdown
## Workflow

1. Identify the user's target output and input files.
2. If the input is scanned, use OCR path; otherwise use text extraction path.
3. Run `scripts/extract.py`.
4. Validate with `scripts/validate.py`.
5. If validation fails, fix the mapped fields and validate again.
6. Return the output path and any fields that need human review.
```

差的流程示例：

```markdown
## Workflow

Process the file carefully and make sure the result is good.
```

## 9. 自由度准则

Skill 需要根据任务脆弱程度设置不同自由度：

| 任务类型 | 推荐表达 | 示例 |
| --- | --- | --- |
| 高自由度 | 原则、判断标准、少量示例 | 代码审查、写作风格、架构分析 |
| 中自由度 | 步骤、模板、默认选择、可变分支 | 报告生成、数据清洗、PR 描述 |
| 低自由度 | 脚本、固定命令、严格验证 | 数据迁移、文件格式转换、发布流程 |

准则：

- 多种方法都合理时，给原则和判断标准。
- 容易出错但仍需 Agent 判断时，给模板和分支。
- 结果必须稳定时，提供脚本并要求验证。
- 不要用大量 `ALWAYS` / `NEVER` 替代理由；能解释原因时优先解释原因。

## 10. 描述与触发准则

`description` 决定 Skill 是否会被发现，是最重要的字段之一。

高质量描述应包含：

- 能力范围：这个 Skill 能做什么。
- 触发场景：用户说什么、做什么、遇到什么问题时应使用。
- 关键词和近义表达：覆盖真实用户可能使用的说法。
- 边界暗示：避免与其他 Skill 或普通任务混淆。

编写建议：

- 用 “Use when...” 明确触发条件。
- 包含真实用户会说的词，而不是抽象分类词。
- 对容易漏触发的 Skill，描述可以更主动一些。
- 对容易误触发的 Skill，补充具体边界和近邻场景。
- 建立触发 eval：准备应触发和不应触发的真实查询，测试描述是否准确。

## 11. 示例准则

示例适合用于传达难以完全规则化的质量标准。

推荐示例类型：

- 输入与输出对照。
- 好例子与坏例子对照。
- 常见边界情况。
- 真实用户提示。
- 失败后的修正示例。

示例编写准则：

- 示例应具体，不要只写抽象占位。
- 示例应代表真实任务，不要过度理想化。
- 示例数量不宜过多；优先覆盖高价值边界。
- 当示例变长时，放入 `examples/`，在 `SKILL.md` 中按需引用。

## 12. 脚本准则

当任务需要稳定、重复、可验证的操作时，应优先提供脚本。

适合脚本化的场景：

- 文件格式转换。
- 数据校验。
- 批量重命名或结构化修改。
- 指标聚合。
- 输出质量检查。
- 固定 API 调用封装。

脚本准则：

- 脚本应解决实际问题，而不是把困难转移给 Agent。
- `SKILL.md` 必须说明脚本是“要执行”还是“仅供阅读参考”。
- 脚本参数、输入、输出、错误行为应清晰。
- 脚本应给出明确退出码或机器可读输出。
- 不要在脚本中硬编码密钥、个人路径或临时环境。
- 跨平台场景要说明 Windows、macOS、Linux 差异。

脚本引用示例：

```markdown
Run `scripts/validate.py <output-dir>` after generating files.
It exits with code 0 when validation passes and prints actionable errors otherwise.
```

## 13. 验证与评估准则

Skill 的质量应通过真实任务验证，而不是只靠阅读感觉。

最低验证：

- 准备 2 到 3 个真实用户会提出的测试提示。
- 确认 Skill 能在这些提示下触发或被正确手动调用。
- 检查输出是否符合任务目标、格式要求和边界规则。

推荐评估：

- 建立 `evals/evals.json`，记录 prompt、输入文件、期望输出和可验证断言。
- 对比 with-skill 与 baseline，确认 Skill 带来实际改善。
- 记录失败案例和用户反馈。
- 根据 transcript 观察 Agent 是否浪费步骤、误读指令或重复造轮子。
- 对定量指标做聚合，例如通过率、耗时、token、工具调用数量。

好的断言应：

- 难以被错误输出“表面满足”。
- 检查实质结果，而不只是文件是否存在。
- 有明确证据来源。
- 能帮助发现 Skill 的真实缺陷。

## 14. 安全与权限准则

Skill 不应让 Agent 做用户不会预期的事。

必须明确的边界：

- 何时需要用户确认。
- 哪些操作只读、哪些会写入或删除。
- 是否会访问远端、生产环境、凭据、外部服务。
- 是否会运行长任务、部署、安装依赖或修改环境。
- 失败时是否应停止并询问用户。

安全准则：

- 不创建误导性、隐藏意图或绕过授权的 Skill。
- 不把凭据、token、私钥写入 Skill 或脚本。
- 对删除、覆盖、远端写入、部署、真实执行等操作设置明确确认边界。
- 优先使用项目公开接口、结构化 API 或官方工具，避免解析 UI 或猜测状态。
- 长任务应由框架、CI、后台服务或明确的执行入口托管，Agent 负责启动、查询和汇报。

## 15. 维护准则

Skill 是活文档，需要随工具、项目和流程演进。

维护要求：

- 公共接口变化时，同步更新对应 Skill。
- 删除过期路径、命令和术语。
- 保持术语一致，例如始终使用同一个名称描述同一个概念。
- 避免时间敏感表达，例如“在 2025 年 8 月前使用旧 API”。应改为“当前方法 / 旧方法”。
- 修改 Skill 后重新跑关键 eval 或至少做手工验证。
- 当 Agent 在多个测试中重复生成同类 helper 脚本时，应考虑把脚本沉淀进 Skill。

## 16. 常见反模式

- `description` 太泛，导致不能触发或频繁误触发。
- `description` 试图概括完整流程，导致 Agent 只按摘要行动而不读取正文。
- `SKILL.md` 过长，混入大量背景和完整文档。
- 入口文件没有路由，复杂任务只能让 Agent 自己搜索。
- 只有原则，没有步骤、分支、验证和输出格式。
- 同一个 Skill 覆盖多个不相关任务。
- 把项目级全局规范塞进 Skill，而不是 instructions/rules。
- 把必须确定执行的策略写成提示词，而不是 hook 或脚本。
- 把跨平台 custom agent 当作稳定主 Agent 机制，期待它完全接管宿主 IDE 的默认 Agent。
- 引用文件层级太深，Agent 不知道该读哪个。
- 示例过于抽象，不能代表真实用户输入。
- 断言只检查表面结果，无法发现错误输出。
- 为了兼容未发布的临时实现而堆叠历史包袱。

## 17. Skill 创建流程

推荐创建流程：

1. 捕获意图：明确 Skill 要让 Agent 完成什么任务。
2. 提取上下文：如果当前对话已经形成某种工作流，先提取工具使用、步骤顺序、用户纠正、输入输出格式和完成标准。
3. 澄清缺口：确认目标、触发场景、输出格式、边界情况、依赖、示例文件和是否需要 eval。
4. 确定边界：判断应使用 Skill、instructions、prompt、agent、sub-agent 还是 hook。
5. 收集场景：记录真实用户提示、输入输出、边界情况和失败案例。
6. 设计结构：确定目录、入口、参考文件、脚本、示例和 eval。
7. 编写 `description`：覆盖能力、触发词、近义表达和边界。
8. 编写 `SKILL.md`：保持入口精简，先让最小流程可用。
9. 补充资源：添加 references、scripts、assets、examples。
10. 建立验证：准备 eval prompts 和关键断言。
11. 试运行：对比有无 Skill 的表现，收集用户反馈。
12. 迭代：基于失败、transcript 和反馈改写 Skill。
13. 发布：按目标平台部署。
14. 维护：在流程或公共接口变化时更新。

与用户协作时，应根据用户熟悉程度调整术语密度。对不确定用户是否熟悉的概念，例如 eval、benchmark、JSON、assertion，可以简短解释；不要让术语理解成本阻碍需求澄清。

## 18. 质量检查清单

发布前检查：

- `name` 与目录名一致，命名清晰。
- `description` 同时说明做什么和何时使用。
- `description` 包含真实触发词和近义表达。
- `SKILL.md` 精简，通常不超过 500 行。
- `SKILL.md` 给出可执行步骤，而不只是理念。
- 复杂内容已拆到 `references/`、`scripts/`、`assets/` 或 `examples/`。
- 引用文件从 `SKILL.md` 一层可达。
- 每个参考文件都有明确读取条件。
- 脚本参数、输出和错误行为清晰。
- 输出格式和验收标准明确。
- 有安全、确认和权限边界。
- 至少有 2 到 3 个真实测试提示。
- 关键断言检查实质结果。
- 没有硬编码密钥、个人路径或无关环境假设。
- 没有复制大段已有文档或 README。

---

## 19. 各家兼容方案

不同平台对 Skill 的概念、路径和元数据支持略有差异；对 custom agent 与 sub-agent 的运行语义差异更大。为了最大化复用，建议采用“通用核心 + 平台适配”的方式。跨平台主路径只在 **Skill** 与 **Sub-agent / delegated agent** 之间选择；普通 Agent / custom agent 只在平台明确支持身份切换时作为增强形态提供。

### 19.1 通用核心

保持所有平台共享：

- `SKILL.md`
- `references/`
- `scripts/`
- `assets/`
- `examples/`
- `evals/`

通用 frontmatter 保持最小字段：

```yaml
---
name: skill-name
description: "What this skill does. Use when: trigger scenarios."
---
```

平台特定字段应谨慎使用：

- `argument-hint`
- `user-invocable`
- `disable-model-invocation`
- `compatibility`

如果需要跨平台，建议：

- 把平台差异写入 `references/platforms.md`。
- 在 `SKILL.md` 中只保留所有平台都能理解的核心指令。
- 避免依赖某平台专有工具名，除非有清晰 fallback。
- 脚本使用相对路径和标准命令，减少 IDE 绑定。

### 19.2 Claude Code 兼容

Claude Code Skill 通常支持：

- `SKILL.md` 作为入口。
- `scripts/`、`references/`、`assets/` 等捆绑资源。
- 通过描述进行触发。
- 通过 eval、baseline、grader、benchmark 等方式迭代优化。

建议：

- 强化 `description`，覆盖应触发和不应触发的边界。
- 为重要 Skill 建立 eval 集。
- 使用 with-skill / without-skill 对比验证效果。
- 对输出质量高要求的 Skill，加入人工 review 和定量断言。
- 如果目标是让当前 Claude 主会话使用某套专业能力或专家角色，优先做成 Skill；不要假设用户级 `agents/` 一定会成为主 Agent。
- 如果目标是委派一次性专项任务，可提供 Claude subagent 版本。

### 19.3 Cursor 兼容

Cursor Agent Skills 通常要求：

- Skill 存放为目录，目录内有 `SKILL.md`。
- `name` 使用小写字母、数字和连字符。
- `description` 非空，且用于发现。
- 可使用 `disable-model-invocation: true` 控制是否自动触发。

建议：

- 个人 Skill 放在 `~/.cursor/skills/<skill-name>/`。
- 项目 Skill 放在 `.cursor/skills/<skill-name>/`。
- 不要写入 `~/.cursor/skills-cursor/`，这是 Cursor 内置 Skill 区域。
- 如果 Skill 仅希望手动调用，设置 `disable-model-invocation: true`。
- 若希望自动触发，省略该字段并写好 `description`。
- 如果目标是让 Cursor 当前主 Agent 掌握某套能力或专家角色，优先做成 Skill。`.cursor/agents/`、用户级 agents 或 `/agent-name` slash 调用更应按 sub-agent / task-agent 语义理解，不能作为跨平台主 Agent 保证。

### 19.4 VS Code Copilot 兼容

VS Code Copilot 中 Skill 是 agent customization 体系的一部分。相关形态包括：

- Agent instructions：项目级常驻规范。
- File instructions：按文件或任务加载的规则。
- Prompt files：单任务模板。
- Hooks：确定性生命周期自动化。
- Custom agents：带角色和工具边界的专门 Agent。
- Skills：按需触发的多步工作流和资源包。

VS Code Skill 常见路径：

- `.github/skills/<skill-name>/`
- `.agents/skills/<skill-name>/`
- `.claude/skills/<skill-name>/`
- `~/.copilot/skills/<skill-name>/`
- `~/.agents/skills/<skill-name>/`
- `~/.claude/skills/<skill-name>/`

建议：

- 项目共享优先使用 `.github/skills/<skill-name>/` 或团队约定路径。
- `SKILL.md` 中 `name` 必须与目录名一致。
- 使用 `user-invocable` 控制是否显示为 slash command。
- 使用 `disable-model-invocation` 控制是否允许模型自动加载。
- 若内容实际是全局规则、单次 prompt、hook 或 custom agent，应使用对应 primitive，不要强行做成 Skill。
- VS Code Copilot 对 custom agent 与 sub-agent 的控制相对明确。可用 `user-invocable: true` 表示用户可选 agent，用 `user-invocable: false` 且 `disable-model-invocation: false` 表示更偏委派调用的 sub-agent。
- 因为 VS Code Copilot 当前可以通过 Agent mode 选择 custom agent，所以若明确希望“切换身份进入专家 Agent”，可以额外提供 `.agent.md` 版本；但跨平台默认仍建议优先提供 Skill。

### 19.5 Codex 兼容

Codex 当前有 profile、默认配置和 agent role / subagent 配置能力，可用于表达不同角色、sandbox、模型和 developer instructions；但需要区分两类机制：profile / default config 更接近当前会话身份或配置切换，`~/.codex/agents/*.toml` 与 `.codex/agents/*.toml` 更接近可被 spawned 的 custom agent / subagent role。它不应像 VS Code Copilot 的 Agent mode 一样被假设为稳定统一的普通 Agent 入口。

建议：

- 主上下文专业能力：优先做 Skill。
- 平台特化身份切换：在 Codex 支持的 profile、默认配置或启动参数中写入 persona、developer instructions、输出契约，并在部署说明中写明启动方式。
- 委派执行：用 Codex `agents/*.toml` custom agent / subagent role 表达“供父 Agent 委派调用”的语义；如果需要父 Agent 可发现该角色，确认它已在 Codex 配置中注册。
- 不要把 Codex profile 或 agent role 当作跨平台主路径；它是 Codex 平台增强，且应随 Codex 版本复核。

### 19.6 Agent 与 Sub-agent 平台差异参考

各平台对 agent definition 的部署和运行语义不同，建议只作为参考层记录。跨平台标准仍优先选择 Skill 或 Sub-agent：

| 平台 | 普通 Agent 身份切换 | 更稳定的主上下文方案 | Sub-agent / 委派方案 |
| --- | --- | --- | --- |
| Claude Code | 用户级 `agents/` 更偏 subagent 定义，不应作为稳定主 Agent 切换能力 | Skill | `~/.claude/agents/*.md` 通常更接近 subagent 定义 |
| Cursor | 用户级 `agents/` 和 slash 调用更应按 task-agent/sub-agent 理解，不应视为稳定主 Agent 切换 | Skill | `.cursor/agents/*.md` / `~/.cursor/agents/*.md` 用于 sub-agent / task-agent 角色 |
| VS Code Copilot | 当前支持通过 Agent mode 选择 custom agent，适合平台特化的普通 Agent 身份切换 | Skill；或平台特化 `.agent.md` | frontmatter 可显式控制 `user-invocable` 和 `disable-model-invocation` |
| Codex | 可通过 profile、默认配置或启动参数表达当前会话身份；不要把 `agents/*.toml` 误认为默认主 Agent 入口 | Skill；或平台特化 Codex profile/default config | 通过 `agents/*.toml` custom agent / subagent role 表达委派语义，并确认父 Agent 可发现 |

结论：跨平台设计时，不要只根据目录名判断“这是主 Agent 还是 sub-agent”。如果需要稳定复用主上下文能力，写 Skill；如果需要隔离执行，写 Sub-agent；只有在 VS Code Copilot 等明确支持身份切换的平台，或 Codex 这类可通过 profile/启动配置明确指定角色的平台上，再额外提供普通 Agent 版本。

> **平台差异速记**
>
> - Claude Code：`agents/` 更偏 subagent；主上下文能力优先 Skill。
> - Cursor：`skills/` 是稳定主上下文能力；`agents/` 更偏 task-agent/sub-agent。
> - VS Code Copilot：custom agent 与 subagent 控制最明确。
> - Codex：`agents/*.toml` 更偏 spawned custom agent / subagent role；身份切换依赖 profile/default config/启动参数。

---

## 20. 各家部署方式

### 20.1 通用部署步骤

1. 确认目标平台和作用域：个人级还是项目级。
2. 确认目录名和 `name` 一致。
3. 放置 `SKILL.md` 和相关资源。
4. 检查 frontmatter YAML 可解析。
5. 确认 `description` 有明确触发词。
6. 通过示例 prompt 手动或自动验证触发。
7. 如有脚本，确认依赖、权限和跨平台行为。
8. 如果同时提供普通 Agent 或 sub-agent 版本，明确标注它是平台特化身份切换入口，还是委派执行入口。跨平台默认入口仍应是 Skill。

### 20.2 Claude Code 部署

推荐方式：

- 将 Skill 目录放入 Claude Code 支持的 Skill 搜索路径或项目约定目录。
- 如需分发，可打包为 `.skill` 文件。
- 使用测试 prompts 和 eval 流程验证效果。

部署后检查：

- Skill 是否被列入可用 Skill。
- 描述是否能在真实任务中触发。
- 捆绑资源路径是否正确。
- eval 是否能跑通。
- 若部署到 `agents/`，确认文档没有把它承诺为稳定主 Agent；更准确地说明它可能作为 subagent 被委派调用。主上下文专家能力应优先部署为 Skill。

### 20.3 Cursor 部署

个人级：

```text
~/.cursor/skills/<skill-name>/SKILL.md
```

项目级：

```text
.cursor/skills/<skill-name>/SKILL.md
```

部署后检查：

- 不要部署到 `~/.cursor/skills-cursor/`。
- `name`、目录名、引用路径一致。
- 如果设置了 `disable-model-invocation: true`，确认用户知道需要手动调用。
- 如果希望自动触发，确认 `description` 覆盖真实触发场景。
- 若另行部署到 `~/.cursor/agents/`，把它标注为 Cursor agent definition；通过 slash 调用时可能是 task-agent/sub-agent 语义。主上下文专家能力应优先部署为 Skill。

### 20.4 VS Code Copilot 部署

项目级常见路径：

```text
.github/skills/<skill-name>/SKILL.md
.agents/skills/<skill-name>/SKILL.md
.claude/skills/<skill-name>/SKILL.md
```

个人级常见路径：

```text
~/.copilot/skills/<skill-name>/SKILL.md
~/.agents/skills/<skill-name>/SKILL.md
~/.claude/skills/<skill-name>/SKILL.md
```

部署后检查：

- Skill 是否出现在 slash command 候选中。
- 自动触发和手动调用行为是否符合 `user-invocable` / `disable-model-invocation` 设置。
- 如果本应是 instructions、prompt、hook 或 custom agent，及时迁移到更合适的机制。
- 如果部署 custom agent，明确它是用户可选普通 Agent 还是模型可委派 sub-agent，并设置对应 frontmatter。

### 20.5 Codex 部署

Codex agent role / subagent 常见位置：

```text
~/.codex/agents/<agent-name>.toml
```

普通 Agent / profile 启动通常还需要结合 Codex 的 `config.toml`、profile 选择、默认配置或命令行启动参数。不要只复制 `agents/*.toml` 文件就假设它一定会成为当前主 Agent；该目录更适合作为可委派 custom agent / subagent role。

部署后检查：

- `.toml` 中的 `name`、`description` 和 developer instructions 是否一致。
- 如果目标是身份切换，确认 Codex 的启动方式、profile 或默认 agent 配置确实会使用该角色。
- 如果目标是委派执行，确认 description/instructions 明确它是 delegated sub-agent，并确认父 Agent 能发现该 agent role。
- 跨平台主入口仍应优先提供 Skill，Codex profile 作为平台特化增强。

## 21. 推荐结论

一个通用 Agent 能力构建标准应坚持以下核心判断：

- 用 `description` 解决发现问题。
- 用 `SKILL.md` 解决入口、路由和最小流程问题。
- 用 `references/` 解决详细知识问题。
- 用 `scripts/` 解决稳定执行问题。
- 用 `examples/` 解决质量感知问题。
- 用 `evals/` 解决真实有效性问题。
- 用安全边界和确认规则解决信任问题。
- 用平台适配解决部署差异问题。
- 跨平台主上下文能力优先做成 Skill，而不是依赖 custom agent 接管宿主主 Agent。
- 需要隔离上下文和一次性结论时，再做 sub-agent / delegated agent 版本。

> **最终落点**
>
> 不要把目标放在“写更强的提示词”。目标是让 Agent 在正确时机加载正确知识，并用可验证、可维护、可迁移的方式完成一类工作。
