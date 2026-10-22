# Windows 验收记录与剩余步骤

日期：2026-10-22（Asia/Shanghai）。环境：Windows 11 build 26200、Python 3.13.5、PySide6 6.9.3、pytest 9.1.1。

## 已完成的验证

| 层次 | 结果 | 限制 |
| --- | --- | --- |
| 原始业务/数据库单测 | 9 passed | 不涉及桌面展示 |
| 修复后全部自动测试 | 12 passed | Qt 使用 offscreen，不验证 OS 通知 |
| 依赖一致性 | pip check 通过 | 兼容性结论限当前环境 |
| 真实 GUI | 启动、新增无 DDL 中文任务、显示、完成、配色复核通过 | 托盘及完整提醒流程未完成 |
| 真实后台处理 | 脚本创建截止任务后，数据库 reminder_stage 变为 soon | 未确认横幅送达、逾期弹窗 |
| 外部 Agent | 未接入、未调用 | Pi 留到后续 Ticket |

发现并修复：宽版本依赖导致本机 QtCore DLL 导入失败；深色系统主题与局部浅色样式混用导致文字难读；空标题提交关闭录入表单。另为推迟状态补充可见时间。新增 Qt 回归测试经历失败后修复通过。

## 运行命令

在含 `pyproject.toml` 的 `focuspilot_starter` 文件夹执行，不需要激活脚本：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m focuspilot
```

首次安装可执行 `py -3.13 -m venv .venv`，然后执行 `.\.venv\Scripts\python.exe -m pip install -e ".[dev]"`。本机安装曾遇到临时目录权限问题，可在当前 PowerShell 会话中将临时目录设为项目内目录，再重试 ensurepip/安装：

```powershell
New-Item -ItemType Directory -Force .tmp | Out-Null
$env:TEMP = Join-Path $PWD '.tmp'
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 待真实验证

建议在新 PowerShell 会话中设置隔离测试数据位置，随后启动程序：

```powershell
$env:APPDATA = Join-Path $PWD '.smoke'
.\.venv\Scripts\python.exe -m focuspilot
```

这仅影响该会话的子进程。正式运行时使用未覆盖 APPDATA 的新会话，数据保存到正常 `%APPDATA%\FocusPilot\tasks.sqlite3`。不要同时运行多个测试实例，以免互相领取提醒阶段。

1. **表单校验**：填写预计耗时但保持标题为空，点击 OK。期望表单保留、显示“任务标题不能为空”；补上标题后可保存。
2. **有 DDL 录入**：创建截止时间为当前时间后 2–3 分钟的任务。期望显示“紧急”、正确的 DDL；保存后检查系统通知和卡片提醒文字。
3. **托盘恢复**：点击主窗口 ×，在任务栏通知区域（可能位于折叠图标内）找到 FocusPilot，双击恢复；再右键选择“显示待办”验证菜单。期望后台继续运行。
4. **持久化**：创建测试任务，通过托盘“退出 FocusPilot”，重新运行同一命令。期望任务仍存在；完成后重启不会重新出现为待办。
5. **推迟 10 分钟**：对即将截止或逾期的任务点击“稍后10分钟”，记录卡片结束时间。保持应用运行，结束前不再提醒；结束后最迟约 30 秒内重新检测并显示当前提醒阶段。若此时 DDL 已过，应触发逾期提醒。
6. **后台逾期**：创建 2–3 分钟后截止的任务并隐藏主窗口，等待截止后约 30 秒。期望面板恢复并显示逾期任务；不要把截止前 soon 通知误当作 overdue 验收。
7. **通知关闭时的兜底**：在用户自己的通知配置下检查任务面板和提醒文字。当前未实际验证系统专注模式；本程序不承诺横幅穿透系统策略。
8. **退出**：托盘“退出 FocusPilot”后程序应结束，退出后不会继续提醒。当前快捷键、自启、Windows 注销行为均未开发/验收。

每项完成后将真实结果、日期和失败现象写入 WORKLOG，再更新 T02/T03 状态。完成 T02 后才进入 T04。
