# Workflow + Pi Harness 混合架构

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
