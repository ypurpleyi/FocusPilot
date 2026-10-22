# Codex 项目开发指令

本项目的最终目标是 Windows 本地优先的 FocusPilot Agent，采用 Workflow + Pi Harness。

1. 先阅读 `README.md`、`docs/PRD.md`、`docs/SPEC.md`、`docs/TICKETS.md`。
2. **先执行测试**：`python -m pytest -q`。如依赖缺失，先创建 venv 并安装 `pip install -e ".[dev]"`。
3. 一次只完成一个 Ticket。发现需求模糊时，先提出可验证的默认方案并记录于 docs，不擅自扩大范围。
4. 核心时间计算、权限检查、数据变更采用确定性代码；不能委托大模型兜底。
5. 开发时优先按 Red → Green → Refactor（TDD）。对 GUI 做人工冒烟测试并记载真实运行环境。
6. 禁止凭空报告已通过 Windows UI 测试或真实 Pi / API 调用；测试报告需区分单元测试、GUI 和外部集成。
7. 未经用户批准，不读取任意本地私人目录，不联网发送任务数据，不自动修改 DDL，不使用无权限限制的 shell 工具处理外部内容。
8. 后续 Pi RPC 集成应通过单独后台工作线程/进程，保持 GUI 主线程不阻塞；不将普通 `prompt` 回应误当作模型执行完成。
9. 完成后汇报：变更文件、运行命令、通过/失败测试、剩余风险、下个 Ticket。
