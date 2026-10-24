# Workflow + Pi Harness 混合架构

## v0.2实际链路（2026-10-24）

```text
桌面输入 → QThread → AnalysisEngine（本地运行控制）
                        └─ 3个显式RPC标准流 → AppContainer中的Pi
                             ├─ 进程内TaskAdapter → 受限管道 → 宿主MCP → 只读SQLite
                             └─ 固定模型fetch → TLS管道 → 宿主固定DeepSeek地址 → 模型API
草稿结果 → Schema/原文/时间规则校验 → 清单确认 → 重复候选选择 → 字段检查
        → 应用create_many事务保存 → 原有排序/提醒Workflow
```

Pi保持零网络能力，不能直接打开任务库；宿主每轮先用固定子进程尝试打开（不读内容）确认数据库隔离。模型HTTPS由Node验证证书/域名，宿主仅转发加密字节到固定api.deepseek.com:443，不能由模型指定目标。客户端仅允许Flash的POST chat/completions、输出上限与4次请求；MCP仅两项只读工具、8次尝试。宿主服务仍是可信计算基，不宣称它自身全部权限已被OS剥夺。

程序副本只读、工作区可写；首次准备后在应用会话内复用，退出清理。每轮新进程/Job/管道/会话，无普通权限回退；原Pi/Node安装与全局默认模型不修改。失败保留输入，取消立即作废界面运行标识，再在后台停止/清理；不承诺回滚已发送的API请求或费用。

核心文件：`analysis_ui.py`管理确认界面；`analysis_engine.py`运行控制与证据；`analysis_runtime.py`真实隔离启动；`pi_task_extension.mjs`绑定工具和固定fetch；`mcp_transport.mjs`/`mcp_adapter.mjs`处理协议及契约；`model_relay.py`/`model_fetch.mjs`限制模型通信；`drafts.py`/`deadline_parser.py`执行草稿和时间规则；`storage.py`仅由可信应用保存。

下面初期V2/V3图保留演进背景；实际已完成范围以当前链路、TICKETS和VALIDATION为准。

```text
Windows Desktop / Quick Capture
           │
           ▼
  FocusPilot App Service
       ┌───┴──────────────┐
       │                  │
Deterministic workflow   Pi Harness [V2]
  SQLite persistence      │
  Due checks              LLM + tools
  Desktop reminders       Natural language parsing
  Snooze / completion     Planning + decomposition
       │                  │
       └───── restricted tool gateway
                  │
        Explicitly-authorized sources [V3]
        Local docs / Calendar / Feishu ...
```

### 边界

- Workflow 决定 **什么时候**提醒，负责可靠持久化；不会因模型故障停止服务。
- Pi 决定复杂任务 **如何拆解/规划**，但其工具调用受限于应用提供的最小权限。
- 文档读取默认只读，且仅允许用户选择的文件/目录；写入、删除和外部发送必须应用层检查权限并要求确认。
- AI 推理结果是建议，不等于数据库中的事实或外部系统操作已成功。
- Pi 与 Python 交互优先使用 RPC 子进程模式；之后可改用 JS/TS SDK，但不用为此重写桌面 GUI。

### 架构决策 001

早期不强行引入 Skill：先对照纯 Workflow 与基于 Pi 的自然语言规划，观察错误率、延迟和调用成本。只有经过评估能减少任务遗漏或提升规划一致性时，再把经验证的业务规则封装为 Skill。

### 架构决策 002

最小权限优先；不能只靠 SKILL.md 的文字规则限制 Agent 的文件/网络权限，必须在工具实现和系统环境中实施限制。
