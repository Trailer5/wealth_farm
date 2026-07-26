# Wealth Farm Agent Memo

- 本项目是个人投研和数据底座项目。Agent 帮助用户建设数据源、数据库、文档规范、自动化测试和后续投研工作流。
- 每轮对话先读本文件。本文件是唯一固定每轮必读文件；其他 `.agent/docs/` 文件按任务场景读取。
- 新任务、路由不清、上下文丢失时，读 `.agent/SKILL.md` 选择正确文档。
- 涉及开发、文档、目录或代码修改时，读 `.agent/docs/development_rule.md`。
- 涉及开发计划、进度 tracking、开发汇报时，读 `.agent/docs/development_report_rule.md`。
- 涉及当前开发阶段时，先从 `docs/modules/README.md` 查找当前短期计划；涉及 daily_data 时读取 `docs/短期开发计划-daily_data跟踪集合与信息增量.md`。
- Agent 必须保护用户已有改动，不得回滚无关内容。
- 不确定事项必须标注为不确定；影响后续行为的歧义必须先向用户确认。