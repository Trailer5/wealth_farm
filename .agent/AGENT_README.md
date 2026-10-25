# Wealth Farm Agent Memo

- 本项目是个人投研和数据底座项目。Agent 帮助用户建设数据源、数据库、文档规范、自动化测试和后续投研工作流。
- 每轮对话先读本文件。本文件是唯一固定每轮必读文件；其他 `.agent/docs/` 文件按任务场景读取。
- 新任务、路由不清、上下文丢失时，读 `.agent/SKILL.md`。
- 所有任务遵守 `.agent/docs/common/working_principles.md`。
- 开发任务读取 `.agent/docs/develop/`；研究任务从 `.agent/docs/research/mainline.md` 开始。
- Research 第一阶段基础收集必须走 `src/research_orchestration/`；此后由 Agent 进行多轮探索、查询、交互、推演和方向确认，最后撰写报告。不得把基础研究包当成最终报告，也不得回退到手工串服务。
- 新对话只给标的代码或名称时从 `.agent/docs/research/mainline.md` 冷启动；最终研究分别回答是否、何时和多少，不能回答时说明阻塞而不伪造精确结论。
- Agent 必须保护用户已有改动，不得回滚无关内容。
- 不确定事项必须标注为不确定；影响后续行为的歧义必须先向用户确认。