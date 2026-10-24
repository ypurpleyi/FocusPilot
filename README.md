# FocusPilot · Windows 智能待办（v0.2）

**项目目标：** Windows 桌面常驻任务面板 + 低负担记录 + DDL 提醒；后续将 Pi Harness 接入为智能规划 Agent。飞书、邮件、文档读取都是可选插件，不是运行前置条件。

## 已经可以使用的功能

- Windows 桌面侧边小窗，默认置顶；隐藏后保留系统托盘图标。
- 创建任务、设置 DDL / 重要程度 / 预计时间，支持无 DDL 任务。
- 本地 SQLite 持久化；关闭并重启程序后保留任务。
- 自动排序：已逾期或 2 小时内截止的任务为紧急，24 小时内截止或高重要程度为优先。
- 30 分钟内截止提醒、逾期提醒；相同阶段只触发一次；支持推迟 10 分钟与完成任务。
- 全局快捷键 `Ctrl+Alt+N` 快速录入、主窗口 `Ctrl+Q` 退出；热键冲突时明确提示。
- 可选登录自启（默认关闭）；同一数据目录只运行一个实例，重复启动恢复已有窗口。
- 提醒设置：仅面板/标准/强提醒、每日工作时段（支持跨午夜）、一次性免打扰、1–60分钟通知间隔。
- 按用户选择默认开启“逾期任务始终强提醒”，可打断免打扰和工作时段限制；同样遵守任务推迟与通知间隔，可在设置中关闭。
- 核心逻辑、数据库、提醒策略、Qt、本地 IPC、RPC 与 MCP 最终回归246条通过（内含15项Node子测试）；真实模型和可见Windows窗口联调分别验收。

**已接入真实Pi与DeepSeek Flash的智能录入。** 点击「粘贴通知 · 分析待办」，输入最多5项任务；模型识别后通过只读MCP查重，再由你确认清单、重复候选和所有字段，最后应用一次性保存。建议值明确标注，缺少截止时间需补充或确认不设置。取消保留原文，已保存任务不受影响。

Pi在Windows AppContainer内运行，不能直接读取数据库、修改程序或直接联网；查询经宿主受限MCP服务，模型HTTPS经固定DeepSeek加密通道。密钥沿用本机Pi中已配置的DeepSeek普通API密钥，不修改Pi默认模型。点击分析会产生模型API请求；本地提醒不依赖模型或网络。

**本机直接使用：双击本文件夹的 `FocusPilot.cmd`，或桌面的FocusPilot快捷方式。** 首次分析需要准备约194MiB的专属运行副本，可能需要几分钟；同一应用会话后续复用，退出后清理。准备期间仍可取消，本地提醒继续运行。尚未包含每日规划、第三方授权、任务编辑/删除及跨时区同步。

## Windows 本地安装

在 PowerShell 中进入项目根目录（即含 `pyproject.toml` 的文件夹），运行：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,mcp,sandbox]"
pytest -q
python -m focuspilot
```

如激活脚本受到执行策略阻止，可直接调用：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,mcp,sandbox]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m focuspilot
```

打开窗口后点击 `＋ 添加任务`，填入事项并保存。单击 `✓ 完成` 可结束任务，`稍后10分钟` 延后提醒，点击窗口关闭按钮时会隐藏至系统托盘；托盘菜单提供「显示待办」和「退出 FocusPilot」。

应用在托盘中运行时，也可在其他窗口按 `Ctrl+Alt+N` 打开快速录入。主窗口获得焦点后按 `Ctrl+Q` 可真正退出。若快捷键被占用，窗口下方显示原因，仍可点击添加任务；释放冲突后可重启 FocusPilot 重试注册。

可勾选「登录 Windows 后在托盘启动（可选）」启用当前用户的登录自启，取消勾选即可关闭。未勾选时不会新增自启配置。该功能启动的是正常用户数据目录，移动项目或虚拟环境后应重新启用以更新路径。也可手动用 `.\.venv\Scripts\python.exe -m focuspilot --background` 启动到托盘；托盘不可用时仍显示主窗口。

点击「提醒设置」可调整普通任务的提醒方式、工作时段和免打扰结束时间。默认标准提醒、全天可提醒、无免打扰、通知间隔1分钟。**默认开启的逾期强提醒会打断免打扰，即使普通模式为仅面板。** 如需完全静默，请同时关闭「逾期任务始终强提醒」并选择仅面板，或设置免打扰。

静默或通知间隔内的提醒会保留，恢复后只汇总当前仍待办的阶段，不连续补发过期通知；每个阶段仅处理一次，修改强度不会重播已处理提醒。所有任务仍在面板显示，截止时间和排序不变。设置、推迟状态及通知间隔会在重启后保留；声音和系统横幅取决于Windows设置。

数据位置：Windows `%APPDATA%\FocusPilot\tasks.sqlite3`。首次启动时自动创建。备份时先退出程序，再复制该文件。

智能录入使用已验证的本机Node v24.15.0、Pi1.4.0和Pi配置中的DeepSeek密钥。安装路径/指纹见`docs/PI_RUNTIME_INVENTORY.json`；这是当前个人电脑可用版，其他电脑需重新核对运行时配置。未配置密钥或权限检查失败时明确停止，仍可手动添加；不会自动关闭沙箱重跑。仅在点击分析后发送输入及必要查重结果，不自动读取邮件、文档或其他目录。

2026-10-24：真实模型分析和可见Windows窗口完整交互通过，含明确截止时间填充、重复候选选择、人工确认后批量保存与重新读取、运行中取消保留原文/数据库。详见[使用与验收](docs/USER_GUIDE.md)、[结果快照](docs/T08_GUI_VALIDATION.json)和[学习档案](docs/LEARNING.md)。后面的T07探测章节保留各阶段历史，原生stdio失败不影响当前进程内适配路径。

最终回归：**246条Python/Qt测试通过**（含15项Node传输/适配/模型请求约束子测试），依赖一致性检查通过。真实模型/可见窗口测试另外显式执行，不在常规pytest中重复收费。

## 验证说明

2026-10-22：Windows 11、Python 3.13.5、PySide6 6.9.3 环境下，75 条自动测试通过，依赖一致性检查通过。实际窗口验证了新增/显示/完成、完整 10 分钟推迟后逾期面板恢复、重启持久化、从其他窗口触发全局热键并保存任务、热键冲突提示、重复启动恢复和正常退出后热键释放；用户本人确认托盘双击恢复。

T05独立窗口已实测保存/取消、设置重启保留、未逾期任务静默、逾期例外恢复面板并调用提示音/横幅接口；实际通知调用间隔约90秒、60秒。自动测试另覆盖跨午夜、精确边界、静默后补发、冷却重启、事务回滚和配置损坏时的静默回退。观察到接口调用不能证明OS横幅实际送达或声音被听见。

**系统横幅送达、实际登录自启和注销仍待用户验证。** 自启适配器测试使用假注册表，不更改真实自启设置；注销使用 Qt 会话回调与自动测试，没有注销电脑。Qt 测试使用 offscreen 平台；本地 IPC 测试需要正常 Windows 用户命名管道权限，受限沙箱可能拒绝创建管道。

当前固定 PySide6 6.9.3：原来的宽版本范围安装到了 6.12.0，在本机 Anaconda Python 下出现 QtCore DLL 导入失败。排查和验收细节见 `docs/VALIDATION.md`；每轮开发记录见 `docs/WORKLOG.md`。操作系统可能因专注助手/通知策略关闭系统通知，不能将系统气泡通知当作唯一重要提醒手段；常驻面板提供可视兜底。

## T07 离线运行练习（2026-10-23）

```powershell
.\.venv\Scripts\python.exe -m focuspilot.offline_demo
```

这是独立验收窗口，不是正式待办的 AI 入口。使用临时数据库中的两项虚构待办；输入文本不会被理解或拆分。可选择正常查询、等待后取消、失败、忽略停止后超时，观察运行状态和原文保留。不读取正式 APPDATA 任务、不安装真实 Pi、不调用模型，也不创建待办。关闭窗口清理本次临时数据。

已实现：后台子进程 JSONL 通信、请求关联、接受/结束/成功区分、取消失效、总超时、子进程有界清理、受限只读查询和同名候选匹配。默认总时限60秒、停止宽限1秒、每轮最多观察8次工具调用；每条记录最多1MiB、嵌套64层、每轮输出最多4MiB/2000条记录。工具事件超限会停止本轮，不代表已在真实工具执行前实施预算限制。

最终完整测试：120 passed，pip check通过。只读查询、管道分帧、快速完成、失败/重试、取消与迟到消息、不读输入的子进程、超时清理及后台等待时本地提醒执行均有离线回归。实际Windows窗口已观察正常查询完成与超时后恢复、原文保留；取消与失败后的真实窗口操作可按[验证步骤](docs/VALIDATION.md)自行练习。

**真实Pi尚未接入。** 假Pi在自身进程调用只读查询模块，RPC工具事件只作观察，不是Python收到事件后执行命令。真实Pi扩展桥接、固定版本兼容性、OS权限隔离和数据发送授权尚待完成；只读连接保护该连接，不是整个进程的沙箱。T08多任务草稿/字段确认界面尚未开发。

设计、执行边界与用例见[设计草稿](docs/PI_HARNESS_DESIGN.md)、[离线测试计划](docs/PI_HARNESS_TEST_PLAN.md)和[个人学习档案](docs/LEARNING.md)。

## 本地 MCP 工具检查（不调用模型）

用户已选择MCP适配，项目用可选依赖固定官方Python SDK `mcp==2.3.0`。基础桌面入口无需MCP依赖；需要服务和完整测试时运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,mcp]"
.\.venv\Scripts\python.exe -m focuspilot.mcp_probe
.\.venv\Scripts\python.exe -m focuspilot.mcp_probe --protocol-mode legacy
```

检查程序自动创建虚构临时任务库，用官方MCP客户端启动本项目stdio服务，发现并调用`list_tasks`和`find_duplicate_tasks`，验证非法参数与写工具被拒绝，最后关闭子进程并清理临时数据。预期两个计数都为2，`extra_argument_rejected`、`write_tool_rejected`、`database_unchanged`均为true。这不是文本识别或Pi分析，也不修改全局MCP/Pi配置。

本轮完整回归149 passed，pip check通过；含26条MCP测试与3条精确命名空间事件测试。auto模式协商`2026-07-28`，legacy模式协商`2025-11-25`；这些是Python客户端与本服务的结果，不能当作已验证本机Pi 1.4.0。

服务只接受应用/启动参数明确指定的数据库路径，无APPDATA默认值，模型参数不能指定SQL或路径。每进程会话默认最多8次调用尝试（失败也计数），在查询前限制；业务结果JSON最多64KiB。Schema拒绝额外参数，数据库连接只读，输出也经过校验；`readOnlyHint`仅为说明，不能替代实际权限。

`mcp_server.py`可由可信宿主以`python -m focuspilot.mcp_server --database <已授权数据库路径>`启动。直接运行后等待stdin属于正常现象；亲自练习请优先运行上面的probe，不使用正式库。此服务仅启用stdio，不开放HTTP服务端口；Pi启动配置、模型权限/费用授权、OS沙箱和真实端到端接入仍待完成。MCP测试在未安装可选依赖时会明确跳过。

## 工程路线

### Node/Pi容器内离线启动（2026-10-24）

用户授权约200MB专属程序副本后，本机AppContainer内已验证Node v24.15.0、Pi1.4.0、Node文件边界与RPC空闲空会话；四个进程令牌/零能力/Job及三个显式标准流句柄检查通过。源程序关键哈希及四个源DACL未变，profile、副本和工作区已清理。没有MCP、模型、凭据或正式数据。

```powershell
.\.venv\Scripts\python.exe -m focuspilot.sandbox_runtime_probe --approve-runtime-copy
```

该命令按已评审清单临时复制并逐文件校验程序，排除用户配置，缺确认标记不执行。使用文件承载标准输入输出作离线探测，并非生产RPC传输；Node模块解析选项避免查询无权访问的磁盘祖先目录，不放宽源权限。全套190条测试通过。结果与后续边界见[运行时方案](docs/PI_RUNTIME_PLAN.md)及[实际快照](docs/T07_PI_SANDBOX_VALIDATION.json)。

### AppContainer离线可行性原型（2026-10-24）

用户选择A后已在本机Windows执行通过：真实AppContainer令牌、虚构文件读写边界、独立SQLite写入被拒绝、后代权限继承、正常/模拟取消/模拟超时的Job进程树清理。网络验证组合了零能力令牌、OS缺internetClient诊断与本机正对照/容器连接结果；没有连接外网或验证Pi/Node/MCP在容器内运行。

```powershell
.\.venv\Scripts\python.exe -m focuspilot.appcontainer_probe --approve-sandbox-prototype
```

该命令会创建本轮专属profile、编译固定测试程序并设置临时文件ACL，完成后清理；没有确认参数则不执行。只使用虚构数据、不调用模型、不改现有安装目录/正式库/防火墙。可选依赖为`.[sandbox]`（pywin32==312，本机已存在），另需本机现有.NET Framework64编译器与Windows winsqlite3。C#源码作为包数据随项目发布。

检查`prototype_acceptance_passed=true`及清理字段，同时保留`external_egress_live_tested=false`和`production_ready=false`。这只是离线原型；结果、限制和下一阶段范围见[权限评审](docs/PI_SECURITY_REVIEW.md)与[结果快照](docs/T07_APPCONTAINER_VALIDATION.json)。

T07生产接入前新增无模型[权限对照实验](docs/PI_SECURITY_REVIEW.md)：`.\.venv\Scripts\python.exe -m focuspilot.permission_probe`。程序只创建、修改并清理自己的虚构库和控制文件；验证只读接口拒绝写入，但普通用户权限子进程可另开连接写入。它用于说明隔离缺口，不是沙箱部署或模型越权事件。下一步AppContainer离线原型需先确认路线，不自动修改系统权限。

真实模型验收入口`focuspilot.pi_model_probe`已在2026-10-24获授权后实际运行通过：Pi1.4.0驱动`deepseek-flash`，两种只读MCP工具成功执行，返回2项未完成任务/2项重复候选，程序核验结果及虚构库未改动。需要明确指定Pi入口、provider/model、auth文件及可选models文件；缺少`--approve-model-test`时，在读取凭据或启动Pi前退出。没有接入正式GUI，不执行取密命令或OAuth刷新；另行真实测试涉及数据和费用，需授权。范围和限制见[学习档案](docs/LEARNING.md)。本文早期未接入描述以本段及最新VALIDATION为准。

### 真实 Pi 本地连接检查（2026-10-24，无模型）

新增 `focuspilot.pi_local_probe`，已在本机Pi 1.4.0验证：真实CLI启动、RPC `get_state`返回空闲空会话，`pi mcp list --json`连接FocusPilot服务并发现两个只读工具。仅使用临时配置和虚构数据库，结束后清理；没有发送prompt、读取个人Pi凭据或修改全局配置。这不代表模型已调用工具或正式桌面已接入。

```powershell
.\.venv\Scripts\python.exe -m focuspilot.pi_local_probe --pi-entry 'C:/Users/yyyaz/AppData/Roaming/npm/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js'
```

安装位置不同时传入实际CLI路径；Node未在PATH时可用`--node`指定可执行文件。独立配置和环境裁剪不是OS沙箱；真实模型接入仍需明确数据/费用授权及权限设计。

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

### T07 MCP沙箱桥接（2026-10-24，部分验证）

已用固定容器客户端验证命名管道、宿主身份检查、两个MCP只读查询、非法参数/身份拒绝及模拟取消/超时清理。待办与重复候选各2项，虚构库未改。真实Pi原生stdio工具发现仍受本机Node子进程兼容性阻塞，不能算完整Pi接入。

```powershell
.\.venv\Scripts\python.exe -m focuspilot.sandbox_mcp_probe --approve-offline-bridge
```

需要已安装的mcp/sandbox可选依赖。此命令创建并清理专属AppContainer、程序副本和虚构数据；无模型或正式待办访问。当前Node v24.15.0/libuv1.51.0下，固定查询通过后子进程stdio检查超时，命令预期退出1；此时停止，不普通权限重跑。已知失败处在复制Pi之前，初次真实Pi发现尝试及其失败也分别记录。

查看[设计与下一步取舍](docs/PI_MCP_BRIDGE_PLAN.md)、[结果快照](docs/T07_MCP_BRIDGE_VALIDATION.json)和[学习记录](docs/LEARNING.md)。回归209条通过；该命令的系统实测和自动测试分别报告，无新增GUI验证。

- Pi RPC： https://pi.dev/docs/latest/rpc
- Pi Extensions： https://pi.dev/docs/latest/extensions
- Pi Windows： https://pi.dev/docs/latest/windows
