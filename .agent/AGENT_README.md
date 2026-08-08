# Wealth Farm Agent Memo

- 本项目是个人投研和数据底座项目。Agent 帮助用户建设数据源、数据库、文档规范、自动化测试和后续投研工作流。
- 每轮对话先读本文件。本文件是唯一固定每轮必读文件；其他 `.agent/docs/` 文件按任务场景读取。
- 新任务、路由不清、上下文丢失时，读 `.agent/SKILL.md`。
- 所有任务遵守 `.agent/docs/common/working_principles.md`。
- 开发任务读取 `.agent/docs/develop/`；研究任务从 `.agent/docs/research/mainline.md` 开始。
- IDE 单标的投研核心和 Agent 分层指引均已完成。当前没有 active 开发计划；Discovery pending 仅按用户指令续跑。
- Agent 必须保护用户已有改动，不得回滚无关内容。
- 不确定事项必须标注为不确定；影响后续行为的歧义必须先向用户确认。