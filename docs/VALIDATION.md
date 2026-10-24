# Windows 验收记录与剩余步骤

日期：2026-10-22（Asia/Shanghai）。环境：Windows 11 build 26200、Python 3.13.5、PySide6 6.9.3、pytest 9.1.1。

## 已完成的验证

| 层次 | 结果 | 限制 |
| --- | --- | --- |
| 原始业务/数据库单测 | 9 passed | 不涉及桌面展示 |
| 最新全部自动测试 | 75 passed | Qt 使用 offscreen；真实IPC需正常Windows用户权限 |
| 依赖一致性 | pip check 通过 | 兼容性结论限当前环境 |
| 真实 GUI | 新增/显示/完成、重启、10分钟推迟、逾期面板恢复、全局录入、重复启动与退出通过 | 用户本人另确认托盘双击恢复；OS横幅未确认 |
| 真实后台处理 | 推迟结束前保持snoozed，到期后overdue并恢复面板 | 不等于确认横幅送达或精确处理时刻 |
| Windows 集成 | 真正跨窗口快捷键、热键冲突、退出后释放实测通过 | 自启与注销有代码和模拟测试，真实登录/注销待确认 |
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

## 可重复的桌面冒烟步骤

本轮T02基础闭环已通过。以下保留可重复步骤，不能将步骤本身当作已执行证据；真实执行结果见WORKLOG。当前剩余系统行为列于文末。

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
8. **退出**：托盘“退出 FocusPilot”或主窗口Ctrl+Q后程序应结束，退出后不会继续提醒。本轮通过Ctrl+Q实际退出，并用独立原生探针确认全局热键可重新注册；托盘菜单退出未由工具实际点击。

## T04 实测与剩余系统验收

- 已实测：`--background`启动后，从另一个空白窗口按Ctrl+Alt+N，打开表单并保存任务；隐藏后再次启动同一数据目录，第二进程退出并恢复已有窗口。
- 已实测：另一个隔离目录的实例显示WinError1409冲突；退出冲突实例后原实例热键仍可用；退出原实例后可重新注册该键。
- 自动测试：31 passed，包括原生消息识别、冲突不误释放、表单复用、会话结束时不隐藏、假注册表启停与失败回退、跨进程唤醒、崩溃后锁和数据恢复。IPC测试在受限沙箱里会遇到管道权限限制，当前完整通过结果来自正常Windows用户环境。
- **登录自启（用户方便时）**：正式运行程序后勾选自启，正常退出；下次自己登录Windows时检查托盘图标、Ctrl+Alt+N和任务保留。测试后取消勾选可关闭。程序和虚拟环境应保持在原目录，操作系统可能延迟启动。
- **注销（用户方便时）**：保留应用运行，自己正常注销并观察是否被程序阻止；再次登录后检查已保存任务。开发过程中没有注销/重启电脑，会话清理的模拟测试不能替代此项。
- **系统通知**：在自己的通知与专注模式配置下确认横幅是否显示；面板是可视兜底。开发过程中没有修改系统通知或安全设置。

将上述真实系统结果追加到WORKLOG，完成T04系统验收后进入T05，再进入T07 Pi只读分析。

## T05 验收（2026-10-22）

- 代码开工前31条测试通过；新策略先运行出现模块缺失，存储与GUI测试先失败后实现，最终75 passed，pip check通过。
- 隔离目录`.smoke/t05/`，窗口名“FocusPilot · T05 隔离验收”，不会向正式数据库写入测试任务。该验收入口直接运行同一FocusWindow，仅记录通知/恢复面板/提示音接口调用；没有伪造OS横幅或模拟听见声音。
- 实际点击提醒设置，开启09:00–18:00工作时段、免打扰至23:58:55，保留逾期强提醒例外并保存。SQLite读取结果与UI一致。
- 22:59:42创建一个15分钟后截止的测试任务；23:00:42检查阶段仍为none，无任何通知效果记录，任务仍在面板显示。
- 隐藏面板后脚本创建逾期测试任务；23:01:15记录一次横幅调用，23:01:16记录面板恢复与提示音调用，普通任务仍为none。
- 后续两次真实通知调用分别为23:02:45、23:03:45，间隔约89.98和60.04秒；没有修改系统时钟、冷却时间或任务DDL来加速结果。
- 实际打开设置后临时关闭工作时段，再点Cancel；数据库中的work_enabled仍为true。正常退出后重启，原工作时段/免打扰/例外保留，已处理阶段未重播。
- 自动测试覆盖：三种强度、工作时段两端与跨午夜、免打扰精确结束、逾期例外、任务推迟优先、静默中完成不补发、冷却和重启、面板模式不消耗冷却、设置/批量提醒事务失败、损坏配置可见且不覆盖原文。
- 剩余系统限制与T04相同：Windows横幅送达、系统音量与专注策略需用户在实际环境中确认；真实登录/注销仍没有由开发流程执行。本轮无Pi、模型或第三方数据调用。

用户复测：点击“提醒设置”保存一组偏好，关闭重开确认；如需完全静默，关闭逾期强提醒例外并选择仅面板；如需检查静默后补发，临时关闭逾期例外、设免打扰结束为几分钟后，创建即将截止任务并保持应用运行。任务完成后不应补发；未完成的任务在满足工作时段与通知间隔后，仅提醒当前阶段。

## T07 离线验收（2026-10-23）

- 基线75 passed in 6.75s。协议/只读与子进程测试先因缺模块失败；实现后新增恢复路径与深度防护用例先失败再修复。最终完整回归120 passed in 18.53s，pip check无依赖冲突，无新依赖。
- `test_rpc.py`25条：分段UTF-8、CRLF与Unicode分隔符、畸形/过深/截断JSON、接受/收敛/错误状态、取消/时限、查询白名单、参数约束、同名未完成任务及只读连接写入拒绝。
- `test_pi_runtime.py`20条：真正假子进程管道、诊断分离、快速完成、重试恢复、失败、handled/queued、未知工具/预算、重复启动、取消后迟到回调、总超时、不读stdin/忽略停止的进程清理、启动失败、查询失败及Qt提醒协作。
- Qt提醒协作实际触发既有FocusWindow对临时任务的overdue阶段更新；不是只检查对象存在，也不是Windows横幅送达验证。只读测试前后比对任务内容，提醒写入仅由既有Workflow执行。
- 实际启动独立“FocusPilot · T07 离线验收”窗口，正常查询显示样例库2项和“没有创建任何任务”；另观察到超时后原文保留、运行按钮恢复。期间用户也在操作窗口，工具输入因冲突被拒绝后暂停自动点击，没有把未完成的取消/失败手动流程记为通过。
- 验收窗口使用程序自建的临时样例库；不会读正式APPDATA数据库，不解释用户输入，不发网络请求。没有将窗口内原文写入记录或上传Git。真实Pi、模型和工具扩展未运行。

用户练习（项目根目录执行`.\.venv\Scripts\python.exe -m focuspilot.offline_demo`）：

1. 输入练习文本，选择“正常只读查询”，运行后应显示预置2项；输入文本不是这两项的来源，也不会创建任务。
2. 选择“等待，练习取消”，运行后点取消；预期原文保留、结果为空，后台清理后可再次运行。不要把点击取消后仍有短暂清理状态当成取消无效。
3. 选择“模拟失败”，运行后应显示失败并保留原文；改回正常查询，可成功重试。
4. 选择“忽略停止，练习超时”，等待默认60秒总时限；应显示超时，清理自己的假子进程后恢复按钮。也可以提前取消。
5. 关闭窗口正常退出。此入口不使用全局热键或自启，不需要结束正式FocusPilot。

真实Pi待办：固定版本与协议复核、注册受限工具的扩展桥接、工具执行前预算、进程权限隔离、明确发送字段与用户授权、真实模型评估。只读连接不限制同进程的其他文件访问，假Pi事件也不能证明真实工具权限。T08多任务字段确认与保存仍未开发。

## T07 本地MCP验收（2026-10-23）

- 用户明确选择MCP方案；在项目.venv安装官方SDK2.3.0并记为可选依赖，未安装SDK CLI/Inspector或全局组件。可编辑安装使用原有隔离构建；无构建后端的环境不使用--no-build-isolation。
- 基线120 passed in 19.57s。MCP测试先因缺mcp_server模块失败；服务实现后26条MCP测试通过，另增3条RPC命名空间用例，最终完整149 passed in 30.53s，pip check通过。
- 受限沙箱内测试阻塞在Python asyncio创建socketpair，faulthandler定位后结束本轮测试进程，在正常用户环境重新运行通过；没有把环境阻塞误判为协议成功，也没有实际调用外部模型。
- 测试包含服务端封闭Schema与固定白名单、只读查询、结构化输出/文本一致、最大结果大小、损坏结果拒绝、调用前预算（非法尝试也计数）、查询失败不伪装为空、不泄露数据库路径，以及两种协议模式的工具发现/调用和退出。每次测试用临时虚构数据。
- 实际执行`python -m focuspilot.mcp_probe`与`--protocol-mode legacy`。auto协商2026-07-28，legacy协商2025-11-25；只发现list_tasks与find_duplicate_tasks，pending_count与duplicate_candidate_count均2，extra_argument_rejected/write_tool_rejected/database_unchanged均true。已完成真实MCP本地协议测试，不是实际Pi/模型工具调用。

用户操作步骤（Windows PowerShell，项目目录）：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,mcp]"
.\.venv\Scripts\python.exe -m focuspilot.mcp_probe
.\.venv\Scripts\python.exe -m focuspilot.mcp_probe --protocol-mode legacy
```

本机已安装依赖，通常可直接运行后两条。检查返回工具名、数量和三个true；这是样例库结果，不来自用户待办或原始文本。程序自动关闭本次MCP子进程并清理样例库；不必运行裸server等待stdin，不编辑任何Pi账号或mcp.json。

MCP本身不实现OS沙箱，readOnlyHint只是注解。MCP调用前预算现已在服务实现，但真实Pi默认工具/扩展、进程权限和数据发送范围仍待配置与验收。本阶段没有新增桌面UI，不声称MCP已通过GUI或真实模型集成验收。

## 2026-10-24 · T07真实Pi本地连接（无模型）

- 开工前149条通过；新增11条检查响应关联、失败/非空状态、工具发现异常、敏感环境继承的测试，最终 **160 passed in 30.49s**；pip check通过。
- `focuspilot.pi_local_probe --pi-entry <本机包的dist/bundle/cli.js>`实际运行通过：Pi版本1.4.0，RPC空闲空会话验证true，临时库字节未变true，model_prompt_sent=false，MCP服务connected且工具恰好为list_tasks/find_duplicate_tasks。
- 采用临时PI_CODING_AGENT_DIR、会话不落盘、不加载上下文/技能/自动发现扩展，只显式加载builtin:mcp；RPC工具选择指定精确MCP名。未验证模型看到的最终工具声明，不把CLI参数当成OS权限保证。
- Pi自动网络活动关闭（PI_OFFLINE/PI_SKIP_VERSION_CHECK/PI_TELEMETRY）；未提交prompt，未读个人Pi凭据或正式任务。未做网络抓包或OS防火墙隔离，不宣称已验证所有网络路径封锁。
- MCP发现与RPC状态为两个独立CLI检查；发现不执行任务查询，也不证明模型选工具。临时mcp.json中的global scope仅指这次临时agent目录，不修改用户全局配置。
- 首次受限环境默认临时目录报WinError5，在Pi启动前失败；按既有项目.tmp及普通用户环境重试通过。未声称此环境问题是应用功能失败或已修改系统权限。
- 手动验证：在项目PowerShell执行README的pi_local_probe命令，核对上述输出；应自动退出。此阶段无新增GUI，正式桌面集成、模型Agent Loop、进程级隔离与T08仍未完成。

## 2026-10-24 · T07真实模型验收入口（未发送模型请求）

- 开工前160 passed in32.59s；TDD新增模块先因缺失失败，实施后目标测试30 passed，再补授权门槛与可信启动参数回归；最终 **172 passed in36.56s**，pip check通过。没有真实模型结果或GUI验收。
- pi_runtime允许可信宿主传入独立env/cwd，默认行为不变；真实子进程测试验证传入目录/环境生效，调用者后续修改原dict不影响启动。
- pi_model_probe准备固定3条虚构任务（2未完成+1已完成），验证两种成功工具结果的ID集合、未截断、模型摘要与DB字节未变；仅最终文本正确而无工具证据不得通过。
- 尚未读取个人Pi配置，也未确认“dsv4 flash”实际provider/model/端点；入口支持显式指定auth与可选models文件，仅选当前提供方，拒绝取密命令和动态环境引用，普通API密钥不进入argv/输出。测试通过不等于真实凭据或自定义模型配置兼容。
- 模型执行默认关闭，必须显式传--approve-model-test；已用不存在的配置路径验证缺授权时退出，无Traceback、不先访问文件。实际读取和费用仍待用户确认。
- 限制为一个prompt、总60秒、最多4次工具尝试、每次模型maxTokens配置1024、自动重试/压缩关闭；并非单次API请求或硬金额上限。默认工具禁用不等于OS沙箱，正式任务库不参与。

## 2026-10-24 · T07真实DeepSeek Flash工具循环

- 用户明确回答“允许”，授权必要Pi配置读取和一轮可能收费的虚构数据测试。只读核对settings中默认deepseek/deepseek-v4-pro、auth中DeepSeek凭据类型及存在性；models.json未发现，不创建用户全局文件。原始凭据未输出。
- 按用户指定Flash，通过独立临时models.json显式选deepseek-flash，不改原默认Pro。官方当前Flash标识及旧名称兼容说明：https://api-docs.deepseek.com/quick_start/pricing/ 。本轮实际请求标识为deepseek-flash，不自行声称固定了服务端快照。
- 实际运行一次pi_model_probe，进程退出码0，约6.59秒（含启动/清理，不是纯模型延迟）。两个真实tool_execution成功结果被检查：list_tasks与find_duplicate_tasks各1次，均返回样例2个pending任务ID且未截断；最终JSON为pending_count=2、duplicate_candidate_count=2，DB文件字节未变。[脱敏快照](T07_MODEL_VALIDATION.json)。
- 命令形式：`.\.venv\Scripts\python.exe -m focuspilot.pi_model_probe --pi-entry <已安装CLI> --auth-file <已授权auth.json> --provider deepseek --model deepseek-flash --approve-model-test`。模型默认配置文件不是参数，只有明确需要自定义模型时才传models-file。授权不扩展为反复收费；本轮没有重试或另发prompt。
- 没有生产任务数据、全局配置改动或新GUI。测试只证明这次最小模型工具循环；真实模型取消/失败、正式GUI集成与OS级隔离未验收，账单实际金额未读取。
- 回归：开工前172 passed in37.32s；增加失败诊断脱敏用例后完整 **173 passed in35.97s**，pip check通过。回归只调用离线替身，无第二轮模型请求。

## 2026-10-24 · T07权限对照与路线评审

- 开工前173 passed in32.93s。新增permission_probe与1条独立子进程回归，目标测试1 passed in0.43s；实际CLI也运行通过。
- 三种拒绝均true：写工具、SQL额外参数、只读连接写入（确认SQLITE_READONLY）。同用户子进程另开连接写入虚构库及读取工具范围外的虚构控制文件也均true；os_sandbox_verified=false。
- 这是固定脚本的离线对照，不是Pi或模型越权；无模型/网络、私人文件或正式任务访问，且入口无外部数据库路径参数。临时库主动改变后清理，不改变现有应用数据或ACL。
- 新增PI_SECURITY_REVIEW，列明AppContainer离线原型/暂缓隔离两条路线及未执行验收门槛。未创建profile、修改权限、启用Windows可选功能或生产集成。学习记录更新最小Agent Loop回答与精确反馈。
- 最终完整 **174 passed in34.96s**，pip check通过；无新增GUI或外部模型实测。第一次完整回归启动申请因自动审批超时未运行，按工具许可重试后通过，不属于安全拒绝或测试失败。

## 2026-10-24 · 用户选择A后的AppContainer离线原型

- 开工前174 passed in35.25s。新增7条测试（临时路径边界、profile唯一命名、显式批准门槛、超时单独不算网络证据、三种无效启动不恢复执行/释放资源）。最终 **181 passed in36.63s**，pip check通过；pytest不创建AppContainer，真实OS实测单独记录。
- 命令：`.\.venv\Scripts\python.exe -m focuspilot.appcontainer_probe --approve-sandbox-prototype`。Windows现有.NET Framework64 csc编译固定C#代码；没有复制现有运行时或安装组件。pywin32312本已由项目MCP依赖安装，本轮只声明sandbox可选依赖和C#包数据。
- 实际成功一轮覆盖normal/cancel/timeout三场景：创建并挂起进程、Job分配、宿主核验AppContainer SID一致且TokenCapabilities数量0后恢复。后代令牌/文件限制保留；正常及模拟终止后Job ActiveProcesses均0。未接Qt/Pi的真实取消路径。
- 文件：获准文件读取成功、只读文件写入拒绝、未授权虚构文件读写拒绝、工作区写入成功；C#用Windows winsqlite3另开READWRITE连接并尝试UPDATE失败，SQLite错误为READONLY或CANTOPEN才接受，虚构库字节未变。
- 网络：仅本机监听端口，Python宿主和同一C#代码普通权限均连接成功，容器800ms内未连通；没有即时WSAEACCES。网络诊断API仅对数字文档地址192.0.2.1作能力分类，返回ERROR_SUCCESS/NETISO_ERROR_TYPE_INTERNET_CLIENT（0:2），无外部TCP/DNS请求。门槛组合令牌/诊断/正对照/容器结果，不仅用超时判断。未实测外网、IPv6或多协议。
- 完成后profile API删除成功且其目录不存在，临时测试根删除。没有改现有安装/正式数据库ACL、防火墙或回环豁免，无模型/凭据/正式数据。只读检查显示三种防火墙profile均Enabled，不修改任何设置。
- 实测修复：pywin32 SECURITY_INFORMATION需有符号LONG；CreateJobObject使用随机专属名称；AppContainer创建需要LOCALAPPDATA等Windows环境项；不把无监听端口ConnectionRefused当作权限拒绝。网络只要求WSAEACCES的早期判定不通过，随后加入OS诊断和同代码正对照，明确限定离线策略范围；没有放宽网络权限。
- 首次目标测试未使用项目TEMP发生受限环境tmp_path错误，改用既有项目.tmp后通过。初次模块缺失是TDD红灯；上述修复不虚报为首次即成功。
- [实际快照](T07_APPCONTAINER_VALIDATION.json)：prototype_acceptance_passed=true、external_egress_live_tested=false、production_ready=false。此结果不是Pi/Node/MCP容器内兼容、正式GUI或生产隔离全部验收。

## 2026-10-24 · Node/Pi运行时只读评估

- 181 passed in32.00s；此轮没有业务代码改动，不重复模型或容器实测。
- 本机node.exe实际--version为v24.15.0，91,694,408 bytes；Pi包元数据1.4.0，15,029程序文件、111,843,261 bytes，包含6个.node和3个wasm文件。指纹与分类计数见PI_RUNTIME_INVENTORY.json。
- 仅扫描已知安装程序目录及读取入口代码/package元数据，未读取个人Pi配置/凭据；未发现程序树内重解析项或拟排除配置文件。源和副本均未写入，未改变ACL或创建profile。
- 已准备194MiB专属副本方案和明确get_state验收，不以盘点代替容器内启动结果。等待确认后才实施；不启用MCP、联网或模型。既有Fail Closed学习误解保留反馈，未伪记为独立掌握。

## 2026-10-24 · 获授权的Node/Pi容器内离线RPC

- 用户明确“允许”专属运行时复制/仅副本ACL/无模型RPC范围后实施。开工前181 passed in38.62s；新增9条测试，最终 **190 passed in39.29s**，pip check通过。pytest不启动本原型或创建profile，真实系统实测单独执行。
- 命令：`.\.venv\Scripts\python.exe -m focuspilot.sandbox_runtime_probe --approve-runtime-copy`；无确认标记在读清单前退出。全包复制排除配置/凭据文件，拒绝源重解析点及非空目标，仅继承目标ACL；逐文件源与副本SHA-256一致，15,029份Pi文件+Node共203,537,669 bytes。
- 初次逐文件路径解析过慢，核对自身venv启动链39840→37644及当前临时根AppSID对应profile后，仅停止自身复制并API删除该profile、验证根在项目.tmp内再删除副本。后续受控目标冲突触发一次异常，报告profile_deleted/runtime_and_work_deleted均true。最终8路有界复制保留根范围、重解析点及逐文件哈希，不因性能放宽源权限。
- Node --version先通过；首轮Pi启动因Node默认realpath执行lstat D盘根收到EPERM，失败清理成功。加入官方--preserve-symlinks与--preserve-symlinks-main避免该查询；没有给D盘根/祖先/源安装授权，也没有普通权限回退。
- 成功实测：容器内Node v24.15.0、Pi1.4.0，固定Node脚本6项权限检查全部true；Pi RPC get_state按local-state关联、success=true、isStreaming=false、messageCount=0、pendingMessageCount=0。
- 4个容器进程（Node版本、Pi版本、Node文件检查、Pi状态）均在挂起阶段核验AppContainer SID/能力0、分配Job后才执行；仅stdin/stdout/stderr 3个显式可继承句柄，传输为临时文件非网络。进程正常结束后4个Job均ActiveProcesses=0。无模型/工具循环或生产GUI验收。
- 成功后原Node/Pi CLI指纹不变；原Node文件/安装目录与Pi根/CLI共4份DACL前后相同。源其他对象未执行ACL写操作，报告不声称逐一比较全部源文件ACL。
- profile API删除且profile目录不存在，副本/工作区整体删除。[脱敏快照](T07_PI_SANDBOX_VALIDATION.json)。独立更新的快速退出输出上限只做离线回归，不为此重复复制程序；实测标准输出远低于1MiB。
- MCP、扩展、技能、上下文及全部工具关闭，无密钥、prompt、正式数据或外网实测；原型配置仅允许自建工作区。production_ready=false。下一步MCP进程位置、IPC、网络/凭据和生产GUI待评审。

## 2026-10-24 · MCP桥接实测（整体未通过）

- 用户选择A；基线190 passed in32.70s。新增19条门禁/JSON/预算/取消/响应/身份/批准门槛测试，完整209 passed in38.36s、pip check通过。清理增强后相关28条6.38s通过，真实清理实测也重做。pytest不自动创建AppContainer或调用外部模型。
- 运行`.\.venv\Scripts\python.exe -m focuspilot.sandbox_mcp_probe --approve-offline-bridge`。无批准标记在读清单/复制前退出；当前本机Node下该命令**预期退出1**，因为末尾兼容性门槛失败，不能当作整体通过。
- 真实固定容器客户端：身份/零能力/Job核验、MCP两个工具发现及查询通过，待办2项/重复候选2项且ID精确一致；宿主虚构库不能由容器直接读取，库哈希不变。SQL额外参数被门禁拒绝且forwarded_calls=0；普通用户对照连接不能通过本轮AppContainer身份验证，未启动MCP服务。
- 模拟取消与提前设置绝对截止时间均停止本轮；本轮管道关闭，容器Job为空，启动过的宿主MCP Job为空、进程已回收、读写线程停止。不是GUI按钮测试，也不承诺撤销已经发出的只读查询。
- Pi1.4.0原生stdio发现实际超时（代理未连接），没有成功结果；独立Node子进程stdio最小复现仅输出before-spawn，5秒后停止且Job清空。本机Node v24.15.0/libuv1.51.0，与[上游缺陷](https://github.com/nodejs/node/issues/66374)一致，无本地调试器栈证据。最终入口先检查此门槛，失败后不再复制Pi。
- 最终profile b76e3e239c644aa9aafc74c3e0bed014及临时副本/工作区删除，源关键指纹/4份DACL相同；早期失败轮次同样清理。无凭据、正式数据、模型、外网、全局变更或Git同步。[快照](T07_MCP_BRIDGE_VALIDATION.json)保留失败结论；[下一步评审](PI_MCP_BRIDGE_PLAN.md)尚待选择。

## 2026-10-24 · v0.2产品闭环验收

- T07进程内适配：基线209条通过，新增8项Python验收（含Node传输/适配子测试），217 passed in38.79s、依赖检查通过；真实Pi正常/取消/超时三个容器场景通过，详见T07_PI_INPROCESS_VALIDATION.json。
- T08及交付：最终246 passed in38.88s、pip check通过。其中15项Node子测试检查分帧、取消、迟到响应、关闭挂起、契约/参数/结果和固定模型请求边界；pytest不发起收费模型请求。
- 固定模型HTTPS经独立SID/Job核验的本地管道，宿主只连接api.deepseek.com:443，Node验证TLS证书/域名；Pi零网络能力不变。客户端只允许Flash的POST chat/completions、1–4096输出token和每轮最多4次请求；MCP最多8次尝试。没有给Pi直接联网能力。
- analysis_smoke真实模型测试：首次失败并停止/清理；增加仅固定错误码及字节计数诊断后，独立重试通过。两次模型连接、一次实际查重、两项草稿、一个重复候选，虚构库哈希未变。未确定首次失败的根因，未宣称长期网络稳定。见T08_MODEL_ANALYSIS_VALIDATION.json。
- gui_smoke在真实Windows窗口中用QTest操作本进程控件，使用真实Pi/Flash与独立虚构库。确认2项清单、选择仍然新增重复候选、检查2份表单、明确无DDL选项，最终保存前库仍只有原有1项；保存后重读为3项且原任务未被删除。
- 截图检查曾发现模型将明确“明天17点”留空；加入保守的确定性日期/钟点规则后重做完整可见窗口验收，确认2026-10-25 17:00正确填入并保存。模糊“明天下午”不猜17点，单元测试也覆盖同一原文多任务时不把期限复制给无关任务。
- 第二轮在运行状态点击取消：立即保留原文/显示已取消，后台清理后无迟到成功；库仍3项。正常分析的模型报告现为独立快照，不被取消运行的统计覆盖。见T08_GUI_VALIDATION.json。
- 真实进程使用3个显式RPC标准流、先挂起/Job/SID核验后恢复；身份/Job/通道失败的反向测试确认不Resume。每轮发送模型请求前，另用固定子进程尝试打开但不读取所选数据库，必须被OS拒绝。
- 用户确认后的批量保存属于可信应用，不是模型工具。SQL触发器失败注入验证整批回滚；取消草稿不影响已保存任务。Qt自动测试覆盖无DDL确认与取消后迟到结果不弹确认。
- 所有本轮模型/窗口测试仅虚构数据；没有将正式任务用于联调。退出后运行副本、工作区、AppContainer profile通过API清理；正式窗口启动与Git同步另在交付末尾执行。系统通知实际送达、登录/注销及跨时区场景仍不在本轮验收范围。

- 正式启动检查：桌面快捷方式已建立，项目pythonw启动链及“FocusPilot · 今日待办”窗口Responding=true。没有读取正式任务文本或用正式任务追加模型验收。
