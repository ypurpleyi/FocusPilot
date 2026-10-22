# FocusPilot 规范（SPEC v0.1）

## 总体架构原则

Local-first、fail-safe。提醒与存储完全独立于模型。Agent 对外通过受限 Tool API 访问任务；对原始 DDL 的改变必须由用户确认。

## 数据模型

- `id`: UUID
- `title`: 非空字符文本
- `due_at`: 可空，本地 `YYYY-MM-DDTHH:MM:SS`（V0.1 暂存本地时间）
- `importance`: 1（低）/2（中）/3（高）
- `estimated_minutes`: 1–1440
- `status`: `pending`/`done`
- `created_at`: 本地时间
- `snoozed_until`: 用户主动推迟结束时间，可空
- `reminder_stage`: `none`/`soon`/`overdue`/`snoozed`

## V0.1 规则（已实现）

R1. 任务标题为空不可保存。
R2. 无 DDL 允许保存，不触发截止提醒。
R3. 已完成任务不参与排序和提醒。
R4. 当前时间 >= DDL，属于紧急且触发一次逾期提醒。
R5. 当前时间距 DDL <= 2 小时，属于紧急；<= 24 小时或高重要程度属于优先。
R6. 剩余 <= 30 分钟首次触发截止提醒；同一提醒阶段仅通知一次。
R7. 用户选择推迟 10 分钟后，推迟结束之前不通知；到期后再次检测阶段。
R8. SQLite 写入后重启应用，任务仍存在。
R9. 用户关闭主窗口时，只要系统托盘存在，程序继续运行。

## V0.1 接受标准

A1. 能创建有/无 DDL 任务，排序和显示。
A2. 关闭重启后任务仍可读取。
A3. 测试覆盖截止前提醒、逾期提醒、推迟后重试、不提醒已完成任务。
A4. 真实 Windows 验收：系统托盘点击可恢复界面；背景提醒仍在运行。
A5. 真实 Windows 验收：禁用系统通知时面板仍显示任务；提醒不保证穿透系统专注模式。

## 后续 Pi Agent 的接口契约（尚未实现）

输入：当前任务和可选用户上下文；外部文档必须来自白名单。

允许默认调用：`list_tasks`、`propose_plan`；`create_task` 需用户确认解析内容；`complete_task`、`change_deadline` 等影响正式状态的写操作必须受到应用侧许可检查。

返回：结构化每日计划，包含任务 ID、建议排序、原因、风险提示、缺失字段；不要凭空补全截止时间。

Pi RPC 客户端必须区分 `prompt` 接受确认与 `agent_settled` 执行完成；按 JSONL 解析事件且不在桌面主线程中阻塞等待。

## T04 默认方案（2026-10-22）

- 使用 Windows RegisterHotKey 注册 Ctrl+Alt+N，并设置 MOD_NOREPEAT；通过 Qt 原生事件过滤器处理 WM_HOTKEY，排队打开现有录入表单。已打开表单时只恢复该表单，不重复创建。
- 注册冲突不抢占其他应用的热键，不影响普通按钮录入；底部显示原因。退出和会话结束时释放资源；正常隐藏保留热键。Ctrl+Q 是主窗口内的退出快捷键。
- 登录自启默认关闭，仅在用户勾选时写入 HKCU 的 Run/FocusPilot 值；使用绝对 pythonw 路径和 `-m focuspilot --background`，正确引用含空格路径，并检查260字符限制。取消只删除本应用的值。读写失败可见且不将界面错误地保留为启用。
- `--background` 优先驻留托盘，托盘不可用时显示主窗口。属于用户登录后启动，不承诺登录前运行或固定启动时刻。
- 同一数据库路径通过 QLockFile 限制单实例，QLocalServer 仅允许当前用户访问。重复启动通过本地 IPC 请求恢复已有窗口，并等待确认后退出；崩溃后可恢复锁，不产生多实例重复提醒。
- Qt 会话结束请求设置 RestartNever 并清理计时器、表单、托盘、热键；不阻止系统注销。所有写入操作即时提交，异常进程终止后的数据和锁恢复由跨进程测试覆盖。
- 不自动启用自启，不在开发验收中注销或重启用户电脑。真实登录/注销与OS通知策略的最终验收独立记录。

实现依据：[RegisterHotKey](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey)、[Run registry keys](https://learn.microsoft.com/windows/win32/setupapi/run-and-runonce-registry-keys)、[Qt native event filter](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QAbstractNativeEventFilter.html)。
