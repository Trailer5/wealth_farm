# Wealth Farm

Wealth Farm 是面向股票、ETF 和基金的本地投研与数据底座。项目将主数据、行情访问、数据质量、研究状态、因子、事件、组合风险、提醒和 AI 研究任务统一到同一个本地 SQLite 数据空间。

## 启动 Web 投研工作台

在项目根目录运行：

```powershell
python -m uvicorn src.web_api.app:create_app --factory --host 127.0.0.1 --port 8000
```

然后访问 `http://127.0.0.1:8000`。

工作台只读取和维护本地数据，不会因打开页面自动触发远程数据获取。用户选择用于区分普通研究空间，不是安全认证。

## 运行单元测试

```powershell
python src/tests/run_all_unit_tests.py
```

## daily_data 手动日线工具

现有日线 writer 和 runner 是按需共享历史工具，不是当前自动信息任务。示例配置包含 `518880 黄金ETF`，可以手动运行：

```powershell
python -m src.daily_data.runner --daily-data-root data/daily_data
```

当前 GitHub Actions 只允许手动触发，不配置 schedule。自动任务必须先完成所有用户与公共关注标的聚合、信息型数据源、标的关联、质量门禁和手动验证。