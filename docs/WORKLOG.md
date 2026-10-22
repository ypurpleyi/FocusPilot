# 开发记录

每个 Ticket 单独提交；提交前更新本记录，附命令、结果和未验证项。仅同步源码、测试和文档，不上传任务数据库、虚拟环境、日志或密钥。

## 2026-10-22 · 原始项目基线

- 项目根目录是解压目录下的 `focuspilot_starter/`。
- 已阅读 AGENTS、README、CODEX_NEXT、PRD、SPEC、TICKETS、ARCHITECTURE。
- 顺序：验证 T02/T03 → 修复问题 → T04 → 后续独立验收 Pi T07。
- 首次 `python -m pytest -q`：未运行，当前 Python 缺少 pytest。
- Windows Python Launcher 提供 Python 3.13.5；初次 venv 的 ensurepip 因沙箱临时目录权限失败。将 TEMP/TMP 指向项目 `.tmp/` 后完成 pip 安装。
- 依赖安装需要网络权限，已请求运行项目内 pip 安装。
- GitHub 账号连接为 `ypurpleyi`；连接器没有创建仓库能力，本机尚无 Git 推送凭据。已打开 GitHub 新建仓库页面，等待用户登录。
- 真实 GUI、系统通知、登录启动尚未验收；不得将单元测试代替这些结果。

## 2026-10-22 · T02 首轮验证与修复

- 环境：Windows 11 build 26200，Anaconda Python 3.13.5，pytest 9.1.1。
- 创建 `.venv`，在获准的网络环境执行 `python -m pip install -e ".[dev]"`；原始业务/数据库测试为 9 passed。
- 首次真实启动失败：PySide6 6.12.0 的 QtCore DLL 无法加载。改用 6.9.3 后 QtWidgets 可导入、真实窗口可启动；固定项目依赖为 6.9.3，并清理升级遗留的 Pdf/WebEngine 组件。`pip check` 通过。
- 在真实 Windows 深色主题下发现浅色按钮配白字、深色容器配深色标签的问题；设置完整浅色 Qt 调色板并在窗口内明确按钮文字颜色，真实截图复核可读。
- 发现空标题提交会先关闭录入窗口；将标题验证提前到对话框，保留其他输入并显示行内错误。新增回归测试先出现失败，再修复通过。
- 任务卡显示推迟结束时间和提醒阶段；FocusWindow 支持传入隔离 TaskStore 进行测试。
- 自动测试：`python -m pytest -q` → 12 passed；`python -m pip check` → No broken requirements found。
- 真实 GUI：通过点击新增、输入中文、取消 DDL、保存、显示任务和点击完成；界面数量从 0 → 1 → 0。
- 隔离环境：启动子进程 APPDATA 指向项目 `.smoke/`，测试数据库、日志及虚拟环境均被 Git 忽略。
- 截止提醒测试任务由脚本写入隔离数据库；点击主窗口关闭后观察到后台将 reminder_stage 从 none 写为 soon。这只能证明处理逻辑执行，不能证明系统横幅送达、隐藏状态或托盘恢复。
- 用户按 Esc 中止电脑操作；未完成托盘双击、实际 10 分钟推迟、重启后 GUI 展示、逾期弹窗的验收。步骤见 `VALIDATION.md`。
- 依照交接要求，T02 完整验收前暂不开发 T04；本轮无 Pi/API 调用。

## 2026-10-22 · GitHub 同步准备

- 用户指定上传到私有 FocusPilot 项目；目标为 `ypurpleyi/FocusPilot`。
- 已建立本地 main 分支并保留原始项目基线提交；本轮修复与验证记录单独提交。
- 查询目标仓库返回 404，连接器可见仓库列表未包含 FocusPilot；这可能表示尚未创建或连接器尚无访问权限。
- GitHub 连接器没有新建仓库接口，本机未获得可用推送凭据。
- 本次尝试检查浏览器被自动审批拒绝：上一轮 Esc 停止指令仍生效。未绕过限制，未创建远端仓库或上传文件。
- 同步范围仅包括源码、测试与开发文档；不上传任务数据库、日志、虚拟环境或密钥。
