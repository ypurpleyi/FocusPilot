# FocusPilot · 桌面待办 Agent 项目（Starter v0.1）

**项目目标：** Windows 桌面常驻任务面板 + 低负担记录 + DDL 提醒；后续将 Pi Harness 接入为智能规划 Agent。飞书、邮件、文档读取都是可选插件，不是运行前置条件。

## 已经可以使用的功能（V0.1）

- Windows 桌面侧边小窗，默认置顶；隐藏后保留系统托盘图标。
- 创建任务、设置 DDL / 重要程度 / 预计时间，支持无 DDL 任务。
- 本地 SQLite 持久化；关闭并重启程序后保留任务。
- 自动排序：已逾期或 2 小时内截止的任务为紧急，24 小时内截止或高重要程度为优先。
- 30 分钟内截止提醒、逾期提醒；相同阶段只触发一次；支持推迟 10 分钟与完成任务。
- 基础核心逻辑自动化测试（9 条）。

**本版本尚未接入 Pi，因此是 Agent 的稳定底座，不是完整 Agent。** 尚未包含全局快捷键、开机自启、AI 任务拆解、第三方应用授权、任务编辑/删除。相关内容已纳入后续 Tickets。

## Windows 本地安装

在 PowerShell 中进入项目根目录（即含 `pyproject.toml` 的文件夹），运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
pytest -q
python -m focuspilot
```

如激活脚本受到执行策略阻止，可直接调用：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m focuspilot
```

打开窗口后点击 `＋ 添加任务`，填入事项并保存。单击 `✓ 完成` 可结束任务，`稍后10分钟` 延后提醒，点击窗口关闭按钮时会隐藏至系统托盘；托盘菜单提供「显示待办」和「退出 FocusPilot」。

数据位置：Windows `%APPDATA%\FocusPilot\tasks.sqlite3`。首次启动时自动创建。备份时先退出程序，再复制该文件。

## 验证说明

已在当前运行环境中通过 9 条数据库与业务逻辑测试（没有安装 GUI 依赖）；**Windows 可视化界面、系统通知和开机行为尚未在真实 Windows 上验证**。操作系统可能因专注助手/通知策略关闭系统通知，不能将系统气泡通知当作唯一重要提醒手段；常驻面板提供可视兜底。

## 工程路线

- `docs/PRD.md`：用户问题、产品边界、成功指标。
- `docs/SPEC.md`：业务规则及验收标准。
- `docs/TICKETS.md`：后续功能逐项开发计划。
- `docs/ARCHITECTURE.md`：Workflow + Pi Harness 的职责边界。
- `docs/CODEX_NEXT.md`：下一轮直接复制给 Codex 的开发提示。
- `AGENTS.md`：Codex 在此仓库中需要遵循的开发约束。

## 重要约束

- V0.1 使用电脑当前**本地墙上时间**，不支持跨时区同步、旅行或夏令时边界的完善处理；后续版本应使用 UTC 持久化、IANA 时区进行展示。
- 任务提醒只在电脑开机、应用运行且未被操作系统挂起时触发；退出/关机/睡眠不会在后台继续运行。
- 不发送任何任务数据到云端；后续接入 Pi 和其他应用时，必须明确展示权限及外部模型数据传输范围。

## Pi 官方资料

- Pi RPC： https://pi.dev/docs/latest/rpc
- Pi Extensions： https://pi.dev/docs/latest/extensions
- Pi Windows： https://pi.dev/docs/latest/windows
