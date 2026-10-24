# T07：沙箱Pi与宿主MCP桥接评审

状态：2026-10-24，用户已选择A。固定客户端的真实容器桥接/只读查询/拒绝/取消/超时已通过；Pi原生stdio发现受本机Node子进程兼容性阻塞，整体未通过。下文“用户选择后/尚未实测”为设计时历史，实际结果见文末。

## 推荐与取舍

Pi在AppContainer内通过stdio启动的MCP子进程也受容器限制，不能直接沿用普通宿主环境中的Python路径和数据库权限。

| 方案 | 好处 | 代价与边界 |
| --- | --- | --- |
| A：宿主只读Python MCP服务 + 容器内Node转发代理（推荐） | 复用现有工具，不复制Python运行时；Pi不获数据库文件权限 | 增加受限IPC、身份与生命周期校验；宿主服务纳入可信计算基 |
| B：Python MCP也放容器内 | 无需跨容器工具桥接 | 需另行盘点/复制Python依赖、授权数据库只读文件；同容器Pi也可能直接读取已授权文件 |

推荐A。宿主服务仍是普通用户进程：参数契约与只读连接约束业务入口，不等于OS已经禁止该进程的全部写入。该服务属于TCB（Trusted Computing Base，可信计算基）；若要求工具进程也被OS强制禁止写库，需要另设受限身份/容器并单独验证。

## 拟定链路

```text
FocusPilot可信控制器 ── RPC ──> [AppContainer：Pi]
                                  │ MCP stdio
                             [Node转发代理]
                                  │ 专属本地IPC
宿主侧：[Broker受限入口] ──> [Python MCP服务] ──> [只读虚构SQLite]
```

RPC负责会话通信，MCP规定工具发现/调用协议，IPC（Inter-Process Communication，进程间通信）承载跨进程数据。Broker（权限代理）检查本轮身份和预算；Node代理只转发协议，不解释SQL、执行脚本或选择数据库路径。

将来Agent负责选择查询和理解结果；身份、权限、Schema、预算、超时、取消仍由确定性Workflow执行。工作区可写不赋予宿主权限，工作区内容也不自动全部成为模型上下文。

## 用户选择A后，第一个离线子阶段

复用已批准的专属Node/Pi副本方式，新建专属临时profile和虚构库，验证跨边界通道。候选为Windows本地命名管道（Named Pipe），Pi继续通过原生MCP stdio启动代理。先验证固定Node客户端通信，再验证Pi发现和固定MCP客户端调用。

Node支持Windows命名管道，但自建AppContainer与宿主之间的命名空间、ACL及完整性级别兼容性尚未实测。失败停止，不通过HTTP回环豁免或普通用户Pi绕过。

约束与验收：

1. 专属管道创建时防抢占、拒绝远程访问；不向Everyone或全部AppContainer授权。验证对端本轮AppContainer SID和Job归属，失败不查询。SID识别容器，不宣称识别其中某个可信脚本；同容器程序仍视为不可信请求方。
2. 宿主固定数据库和两个工具list_tasks、find_duplicate_tasks；拒绝SQL、路径、写入工具及无关MCP资源/提示能力，只允许必要的初始化、发现、调用及生命周期消息。复用服务端参数校验。
3. 每轮最多8次工具尝试（失败也计数），绝对总时限60秒；单条请求最多64KiB、业务结果最多64KiB。有界队列、JSON深度限制和请求ID关联，进度通知不能延长总预算。
4. 本轮身份由宿主管理，不能信任请求自报run_id。取消先撤销资格，再断开/回收；迟到结果不交给新运行。查重失败不能返回有效空列表。
5. 宿主MCP子进程单独跟踪回收，不能假称属于Pi容器Job；所有本轮进程、profile、副本和管道均需清理。
6. 正向验证只发现两个工具、虚构待办2项/重复候选2项、库字节未变；负向验证错误SID、旧运行、非法参数、额外工具、超限、断线、超时、取消与清理。区分入口拒绝和业务校验拒绝。

不读取凭据或正式任务，不发送prompt、不调用模型，不开放网络能力、不增加回环豁免、不改原安装ACL。此阶段无模型，不能称为沙箱内Agent Loop验收；不涉及生产GUI或T08。

本轮仅文档修改，未重跑代码测试。此前190条测试和容器RPC结果不覆盖本方案。

参考：[本机Pi MCP说明](<C:/Users/yyyaz/AppData/Roaming/npm/node_modules/@earendil-works/pi-coding-agent/docs/mcp.md>)、[Node IPC](https://nodejs.org/api/net.html#ipc-support)、[Windows命名对象隔离](https://learn.microsoft.com/en-us/windows/apps/develop/communication/sharing-named-objects)。官方能力说明不代替本机跨边界实测。

## 实际实现与验证

- `mcp_bridge.py`实现严格JSONL解析、协议/工具白名单、参数Schema、8次尝试预算、60秒绝对时限与响应关联。Windows管道拒绝远程客户端，专属SID和低完整性ACL；用Windows API读取对端PID，再核验本轮AppContainer SID、零能力和Job归属。随机名字不作为授权依据。
- Host创建管道时使用当前Session ID和GetAppContainerNamedObjectPath返回的相对命名空间，容器内使用LOCAL别名。最初直接创建宿主LOCAL管道导致容器ENOENT；地址修正没有扩大DACL或增加网络能力。命名空间大小写/格式不符直接拒绝。
- 通过身份后才启动固定Python MCP服务，继承裁剪环境、固定虚构库。宿主服务属于可信计算基；普通用户身份没有被OS全面剥夺写权限。该可信子进程启动后加入独立Job；Pi等不可信进程仍严格先挂起核验/入Job再恢复，两者不能混称同等启动保证。
- 固定Node客户端在容器内发现两个工具，实际查询得2项待办、2项重复候选，ID与虚构库一致且未截断；容器直接读取宿主虚构库被拒绝，库哈希未变。
- SQL额外参数在转发前拒绝；普通用户对照连接因身份不符拒绝（未启动查询服务）；模拟取消/绝对时限到期令本轮失效，断开管道，容器Job与已启动的宿主Job均清空，读写线程停止。取消不承诺撤销已发送的只读查询，只保证停止本轮后续处理、不交付迟到结果；尚未接GUI取消按钮。
- `mcp_pipe_proxy.cjs`是拟供Pi stdio启动的纯转发代理，但Pi未能启动它，故未完成真实Pi代理端到端验证。成功的固定客户端测试不能代替这一项。
- 失败资源亦走清理：各轮临时profile、副本删除；原Node/Pi关键文件哈希及4份源DACL前后相同。没有模型、凭据、正式数据、外网、安装或全局配置变化。

## 已定位的阻塞与下步选择

本机Node v24.15.0/libuv1.51.0。真实Pi MCP发现超时且代理没有接入。进一步用固定`child_process.spawn(process.execPath, ['--version'], {stdio: ['ignore','pipe','pipe']})`复现：只输出before-spawn，不返回after-spawn；5秒后停止并确认Job清空。最终入口在此失败即停止，不再重复复制Pi。

[Node问题66374](https://github.com/nodejs/node/issues/66374)描述AppContainer中子进程stdio/IPC创建卡住；[libuv修复5181](https://github.com/libuv/libuv/pull/5181)改用LOCAL管道。本机结果与该已知问题一致，这是结合最小复现与上游记录的工程归因，没有本地调试器栈证据。查阅时Node引入libuv1.53.0的[PR66282](https://github.com/nodejs/node/pull/66282)仍开放，不能假定升级任意Node版本就解决。

后续推荐先评审**Pi进程内的专用MCP适配扩展**：复用已安装`@earendil-works/pi-mcp`的`McpClient.connect(McpTransport)`接口，提供命名管道Transport，经过发现/Schema核验后只注册两个只读工具，避免stdio子进程。宿主MCP、数据库边界、Job及工具预算保持同一目标。需要维护取消/关闭与工具绑定，不是仅向`registerMcpServer`填管道URL（该配置目前仅stdio/HTTP）。这一替代方案尚未实施，必须经架构选择与离线验证，不能冒称Pi原生MCP已连通。

另一选择是保留stdio方案，等待并验证包含所需修复的官方运行时。下载/安装/编译运行时需先具体评审，不修改现有Node来试错，不放宽AppContainer或增加回环豁免。

证据：[脱敏结果](T07_MCP_BRIDGE_VALIDATION.json)，offline_bridge_checks_passed=true、pi_mcp_discovery_verified=false、overall_passed=false。自动测试开工前190条通过；新增19条后全套209 passed in38.36s，依赖一致性通过；清理检查修订后另跑相关28条通过（6.38s），并重做Windows桥接/拒绝/取消/超时/宿主Job和线程清理实测。自动测试不会启动该系统原型。

进程内适配的明确范围、公开接口、备选和验收门槛已整理为[下一阶段评审](PI_INPROCESS_MCP_PLAN.md)。用户要求继续评审；尚未确认实施替代方案，未改原生stdio路径或增加扩展加载。
