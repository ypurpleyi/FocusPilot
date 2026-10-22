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
