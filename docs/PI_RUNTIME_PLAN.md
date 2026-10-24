# T07：Node / Pi 容器内离线启动评审

状态：2026-10-24；用户明确允许后，已完成专属副本、Node/Pi容器内版本与文件边界、RPC空闲空会话检查，profile/副本已清理。未启用MCP、模型或正式数据，不推进T08。下文“待确认/未执行”保留为前期评审历史，实际结果见文末。

## 为什么需要单独验证

固定C#程序在AppContainer成功，不代表Node/Pi的依赖、配置发现及RPC标准流也兼容。先验证确定性的启动和状态查询，再讨论工具服务、模型网络和生产GUI；此阶段没有Agent推理。

## 本机只读盘点

| 程序 | 版本与体积 | 发现 |
| --- | --- | --- |
| Node | v24.15.0；node.exe 91,694,408 bytes | 安装目录没有额外DLL旁文件；不复制npm/npx脚本或改原安装权限 |
| Pi | 1.4.0；程序树111,843,261 bytes、15,029文件 | 包内包含6个.node原生模块及3个wasm资产，不能仅凭入口存在认定依赖齐全 |
| Pi bundle | 81文件、9,211,377 bytes | 入口存在动态加载，纯静态入口扫描不足以证明仅复制bundle即可运行 |
| 组合副本 | 203,537,669 bytes，约194 MiB | 推荐先保留已安装Pi程序树及依赖，避免边验证边改原安装权限 |

精确路径、Node和CLI的SHA-256、分类体积见[只读清单](PI_RUNTIME_INVENTORY.json)。本轮只是元数据/安装代码检查，没有读取个人Pi配置、复制文件或启动沙箱。程序树未发现重解析项，仍须复制时重新校验，拒绝越出源根目录的链接。

## 推荐的具体变更（待确认）

1. 在项目被Git忽略的`.tmp/pi-runtime-*`建立本轮目录；复制现有node.exe和Pi安装包程序树，不修改源文件或源ACL、不下载/安装组件。
2. 跳过`.pi`、`.git`、auth.json、models.json、settings.json、.npmrc、.env及.env.*。当前安装程序树没有这些匹配文件；仍在复制阶段强制过滤，不复制用户配置目录或任何凭据。
3. 创建本轮独立AppContainer profile；只有副本和专属工作区获得权限。程序副本只读可执行，工作区可写，虚构未授权控制文件不可读写；不授权访问原Node/Pi目录或正式任务库。
4. 使用专属配置路径、干净环境、零网络能力；关闭扩展、MCP、技能、上下文发现和所有模型工具，禁用自动网络/版本检查/遥测，不传密钥、不提交prompt。
5. 验证Node版本、Pi版本及RPC get_state的正确关联和空闲空会话。启动仍先挂起、分配Job并核验令牌再执行；失败不回退普通用户运行测试任务。
6. 标准输入/输出使用宿主明确授权的句柄，不借网络通道；若需要从现有AppContainer辅助代码扩展标准流，保持句柄白名单，不继承其他宿主句柄。
7. 检查副本内固定Node文件访问测试仍拒绝越权；结束或超时时回收本轮进程树、删除专属profile与全部副本，报告清理。任何失败都保留边界，不靠给原目录额外权限修复。

复制是为了给沙箱可读的运行依赖，并不是沙箱本身。完整Pi副本也不等于开放其全部工具；工具选择、OS资源限制和网络策略各自独立。

## 本阶段验收（尚未执行）

| 事项 | 预期 |
| --- | --- |
| 源文件完整性 | Node/CLI指纹与盘点一致；源安装配置/ACL未改 |
| 真实身份 | Node/Pi进程的AppContainer SID与本轮profile一致，能力0，属于本轮Job |
| 启动 | Node与Pi返回对应版本，标准流无污染 |
| RPC | get_state按id响应，success=true，isStreaming=false，messageCount=0、pendingMessageCount=0 |
| 文件边界 | 固定Node脚本可写专属work，不能改只读运行时副本或访问未授权虚构文件 |
| 数据与网络 | 无prompt、凭据、正式数据和外部连接；不将无网络能力当作已完成真实模型网络验证 |
| 失败与清理 | 启动失败不普通权限重跑；超时收回Job；profile、工作区与程序副本删除 |

这一轮不启用MCP：真实Python MCP服务放在容器内还是可信宿主侧，涉及依赖与IPC权限，是后续明确的架构选择，不能由复制运行时顺带决定。模型网络、API凭据和正式任务数据范围也需另行评审。

## 授权边界

之前A路线仅授权固定测试程序的离线原型，并明确运行时复制或原目录赋权需先提出具体变更。按AGENTS的重要架构由用户决定规则，现将“约194MiB专属副本 + 仅副本ACL + 无模型RPC状态检查 + 清理”作为可审核的下一阶段范围，等待确认后实施。

## 实际执行与边界（2026-10-24）

- 用户随后明确“允许”，实现`sandbox_runtime_probe.py`，copy阶段过滤配置/凭据文件、拒绝源重解析点/非空目标，目标根约束后用8个有界工作线程逐文件复制和校验哈希。15,029份Pi文件与Node共203,537,669 bytes完成核验；没有借用源ACL。
- Node版本、Pi版本、固定Node文件权限测试、RPC状态查询共4个AppContainer进程，均在执行前核验本轮SID/零能力并加入Job；每个进程仅继承明确的stdin/stdout/stderr三个句柄。此探测用临时文件承载标准流，尚未替换生产RPC管道传输。
- Node默认真实路径解析会尝试lstat D盘根，首轮Pi因此EPERM失败。通过Node官方`--preserve-symlinks --preserve-symlinks-main`避免该模块加载器查询；程序树已拒绝全部重解析点，没有放宽磁盘根/祖先/原安装权限，没有普通权限重跑业务测试。
- 实际Node v24.15.0、Pi1.4.0，get_state正确关联且空闲空会话；Node获准读/工作区写成功，副本改写/目录新增及未授权文件读写均被拒绝。四个Job退出后均为空。
- 原Node与Pi CLI哈希不变；原Node文件/安装目录、Pi根/CLI文件四个源DACL前后相同。profile API删除且其目录消失，200MB副本和工作区删除。
- [实际结果快照](T07_PI_SANDBOX_VALIDATION.json)明确model_called=false、mcp_enabled=false、production_ready=false。下一步仍需MCP服务位置/IPC、模型网络/凭据、生产GUI的单独设计及授权，不能把本轮等同于沙箱内真实Agent Loop已通过。

原型开发曾遇串行路径解析/复制过慢：先精确核对自身venv启动链与profile后停止并清理，再验证受控目标冲突的异常清理，最终采用空目标根检查与8路哈希复制。失败不是成功结果；各轮临时资源已清理，未动原安装目录或正式任务。

参考：[Node模块路径选项](https://nodejs.org/api/cli.html#--preserve-symlinks-main)、[Win32显式句柄列表](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute)。
