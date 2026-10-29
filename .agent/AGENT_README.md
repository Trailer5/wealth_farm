# Wealth Farm Agent Memo

- 本项目是个人投资者的投研智囊系统：持续维护对宏观、市场、行业、标的和策略的认识，回答各类投资问题，并结合用户约束给出可验证、可执行、可复盘的分析与规划。方向以 `docs/long_term/长期愿景.md` 为准。
- 每轮对话先读本文件。本文件是唯一固定每轮必读文件；其他 `.agent/docs/` 文件按任务场景读取。
- 新任务、路由不清、上下文丢失时，读 `.agent/SKILL.md`。
- 所有任务遵守 `.agent/docs/common/working_principles.md`。
- 开发任务读取 `.agent/docs/develop/`；研究问题按 `.agent/docs/research/question_routing.md` 的标准流程走（写请求 → `route` → `run-recipe` → 任务流程）：标的类问题走 `.agent/docs/research/mainline.md`，其他问题走 `.agent/docs/research/playbooks/` 里对应的流程。
- 指引只给流程和动作。用到哪个命令先看它的 `--help`（字段、示例、读法），然后按输出的 `next_actions`、`gaps` 和说明继续；写 Python 调用时看类与方法的文档字符串。
- 单标的研究的基础收集必须走 `src/research_orchestration/`；此后由 Agent 进行多轮探索、查询、交互、推演和方向确认，最后撰写报告。不得把基础研究包当成最终报告，也不得回退到手工串服务。
- 新对话只给标的代码或名称时从 `.agent/docs/research/mainline.md` 冷启动；单标的最终研究分别回答是否、何时和多少，不能回答时说明阻塞而不伪造精确结论。
- Agent 必须保护用户已有改动，不得回滚无关内容。
- 不确定事项必须标注为不确定；影响后续行为的歧义必须先向用户确认。