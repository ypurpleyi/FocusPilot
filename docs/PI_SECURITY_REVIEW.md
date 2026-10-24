# T07：生产权限设计评审

状态：2026-10-24；用户已选择A，完成专属AppContainer离线原型，文件/进程/网络能力策略检查通过，测试profile与临时文件已清理。没有部署生产沙箱、读取正式数据或新增模型调用。只修改了用户已授权的专属临时文件ACL；没有改现有安装/正式库权限或防火墙。早期“尚未执行/等待选择”为评审时历史。

## 已执行的对照实验

运行`python -m focuspilot.permission_probe`。入口没有数据库路径参数，只能创建并清理自己的虚构数据库和控制文件。固定Python子进程只操作该临时目录，不由模型提供脚本。

| 实验 | 实际结果 | 说明 |
| --- | --- | --- |
| 业务工具调用不存在的delete_task | 拒绝 | 工具白名单有效 |
| list_tasks携带SQL参数 | 拒绝 | 参数契约有效 |
| 用原有只读连接UPDATE | SQLITE_READONLY，原任务未变 | 连接的只读约束有效 |
| 同用户子进程另开普通连接UPDATE虚构库 | 成功 | 只读连接没有改变其他连接的OS写权限 |
| 子进程读取工具之外的虚构控制文件 | 成功 | 工具范围不是进程文件访问边界 |

这是工程人员主动执行的离线对照实验，不是观察到模型越权。最后两项说明当前同权限启动不能被宣称为完整沙箱；实验会改变并删除自己的虚构数据，不操作正式库。输出os_sandbox_verified=false是边界判断，不是实验失败。

## 目标边界

| 资源 | 应用/可信工具层 | 模型执行进程的目标 |
| --- | --- | --- |
| 正式任务库 | 应用负责写入；只读服务执行固定查询 | 不可绕过工具直接打开正式库 |
| 本次授权数据 | 应用按请求明确选择字段 | 只能获取本次必要数据，不能扩大范围 |
| 原始凭据文件 | 可信宿主在授权后读取必要项 | 不开放任意凭据文件读取工具；凭据驻留和网络策略须单独验证 |
| 程序代码与其他用户文件 | 正常应用管理 | 不可修改代码或访问未授权私人文件 |
| 临时状态 | 每运行独立目录 | 仅专属工作区可写，结束可清理 |
| 网络 | 仅已授权模型请求 | 网络能力是独立权限；允许互联网不等于只允许某个域名 |

不能把“只读查询”当作“允许把所有查询结果发给模型”。正式任务数据传输仍需单独确认。

## 可选择的下一步

| 路线 | 下一轮具体工作 | 代价与限制 |
| --- | --- | --- |
| A，推荐：先做Windows AppContainer离线可行性原型 | 先用虚构文件验证允许读/禁止写、禁止读未授权控制文件、子进程继承限制与清理；通过后再评估Node/Pi/MCP运行依赖 | 需要创建专属测试AppContainer profile并给专属临时文件配置权限；尚未证明本机Python/Node/Pi兼容，不修改现有安装/正式库ACL，不调用模型 |
| B：暂缓OS隔离，先做独立虚构数据界面 | 继续验证用户取消、失败恢复等交互，不连接正式任务或再调用收费模型 | 可以推进界面学习，生产权限仍未完成；不能因此开放正式数据或任意脚本 |

推荐A，因为工程目标包含技术强制权限边界。只授权先做可行性原型，不等于同意改整个系统配置或启用正式数据。若需要复制运行时、授予现有目录权限、联网或调整其他系统设置，先提出具体变更，不自动扩大权限。

不建议本轮为此启用Windows Sandbox可选组件：微软文档列出虚拟化/系统功能启用及可能重启步骤，开发与部署成本需另行评估。AppContainer也是待验证候选，不预先宣称足够或兼容。

## A路线原型的验收门槛（尚未执行）

1. OS检查进程确实使用AppContainer token，不以环境变量或名称推断。
2. 专属只读控制文件可读、写入被OS拒绝；专属可写工作区可写。
3. 虚构未授权文件不可读/写；试图另开SQLite可写连接同样失败。
4. 子进程不能恢复普通用户权限；不开放网络时网络访问失败。
5. 超时/取消回收本轮进程树，profile/文件权限清理有记录；失败不退回普通用户启动继续处理数据。
6. 第一阶段不含Pi或真实数据；兼容性通过后另列Pi/MCP接入范围及测试，不一次推进多个阶段。

这些门槛覆盖指定威胁路径，不构成“对所有系统漏洞绝对安全”的证明。网络域名限制、凭据最小暴露和可信MCP进程的放置仍需生产方案评审。

## 学习与决策

核心概念：Connection Scope（连接作用域）、Process Isolation（进程隔离）、Negative Testing（反向测试）。亲自观察实验输出：为什么只读连接拒绝写入和同用户子进程写入成功能够同时成立？

等待用户选择A或B后才执行对应下一阶段。依据AGENTS：“如果存在重要的架构选择……由我决定”；“修改系统级配置”须确认。当前没有修改ACL或创建AppContainer profile。

## A路线执行结果（2026-10-24）

入口`focuspilot.appcontainer_probe --approve-sandbox-prototype`，固定C#测试程序由本机现有.NET Framework编译器编译到专属临时目录；Python用Windows API设置SECURITY_CAPABILITIES启动。没有复制Python/Node运行时或调整已有目录权限。

| 门槛 | 实际结果 |
| --- | --- |
| 真实令牌身份 | 宿主在进程仍挂起时读取令牌，AppContainer SID与本轮profile一致，能力数量0；不符则终止，不执行应用代码 |
| 文件与SQLite | 允许文件可读，只读文件/虚构库写入拒绝；未授权控制文件读写拒绝；专属低完整性工作区可写 |
| 子进程 | 同一固定程序启动的后代仍是AppContainer，不能读未授权文件或写只读文件 |
| 正常/取消/超时 | Job Object在ResumeThread之前分配；正常结束与两种模拟终止后ActiveProcesses均0，包含睡眠后代；尚未接Qt按钮或Pi RPC |
| 网络 | 宿主Python和相同C#网络代码都能连接本机监听正对照；容器内800ms内未连通，Windows能力诊断对文档地址192.0.2.1返回0:2，即缺internetClient；没有外部连接/DNS或修改网络配置 |
| 清理 | 虚构库字节未改动，DeleteAppContainerProfile成功且API报告的profile目录不存在，专属临时目录删除 |

不能只用超时证明权限拦截。本轮网络门槛采用宿主/同代码正对照、准确的AppContainer零能力令牌、OS缺能力诊断及容器连接结果组合；套件明确记录无即时AccessDenied错误，未声称完成外网阻断实测。外网、IPv6、多协议与真实模型网络路径仍待后续专门授权和测试。

原型失败时不降级启动：API错误、令牌不符、额外能力、Job分配失败都中止。失败启动也应关闭本轮句柄和清理profile；对应失败注入单元测试覆盖未恢复执行和句柄释放。该原型的Job归属与销毁不代表现有pi_runtime已经改用同一机制。

实际结果见[脱敏快照](T07_APPCONTAINER_VALIDATION.json)。下一阶段只能先评审Node/Pi/MCP运行依赖与传输布局；如需复制运行时、给现有目录赋权或开放网络，要先给出具体变更，不把本轮A自动扩大成生产接入授权。

下一阶段已获授权并执行：[Node/Pi离线启动方案](PI_RUNTIME_PLAN.md)。专属副本在AppContainer内版本、文件边界与无模型RPC检查通过，副本/profile已清理。MCP布局、模型网络与生产配置仍未决定；本轮没有给原安装目录赋权。

新增官方依据：[网络能力诊断API](https://learn.microsoft.com/en-us/windows/win32/api/netfw/nf-netfw-networkisolationdiagnoseconnectfailureandgetinfo)、[NETISO_ERROR_TYPE](https://learn.microsoft.com/en-us/windows/win32/api/networkisolation/ne-networkisolation-netiso_error_type)、[Windows Firewall网络隔离说明](https://learn.microsoft.com/en-us/windows/security/operating-system-security/network-security/windows-firewall/troubleshooting-uwp-firewall)。

官方依据：

- [AppContainer隔离](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)：文件、进程、网络等资源隔离。
- [启动AppContainer](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)：SID、token、DACL与能力；实施仍需本机验证。
- [Windows Sandbox安装条件](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-install)：虚拟化、可选功能启用及可能重启。
