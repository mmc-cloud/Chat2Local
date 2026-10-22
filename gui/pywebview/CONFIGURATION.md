# GUI 配置与 Token 管理

GUI 继续编辑 `~/.chat2local/config.yaml`，通过既有 read / validate / save 流程保存稀疏配置。
生成、导入和重新生成是明确的独立按钮操作，不会在打开设置、启动 Core 或普通保存时触发。
所有新增 GUI 文本支持中文和英文。此轮不修改发布流程、打包方式或线上部署。
以下使用流程适用于审查后加载新版 GUI/Core；源代码修改不会热更新当前运行的程序。

## 本次文件位置

- GUI 设置与交互：`frontend/src/pages/settings.tsx`，新增 `frontend/src/components/token-manager.tsx`、
  `frontend/src/components/browser-status.tsx`；类型、调用与翻译分别位于 `frontend/src/types/pywebview.d.ts`、
  `frontend/src/lib/bridge.ts`、`frontend/src/lib/locale.ts`。
- GUI 原生传输：`python/bridge.py`，新增 `python/clipboard.py`。
- 后端凭据：新增 `src/chat2local/management/credentials.py`，管理接口位于 `management/adapter.py`。
- 状态通道：`src/chat2local/browser/bridge.py`、`management/runtime.py`、`runtime/control.py`、
  `runtime/supervisor.py` 和 `src/chat2local/main.py`。
- 扩展全站授权：`browser-extension/options.html`、`options.js`、`browser-operations.js`。
- 测试：新增 `tests/test_management_credentials.py`、`test_management_browser.py`、`test_gui_clipboard.py`，
  更新 `tests/test_gui_bridge.py`；新增前端 `frontend/tests/management.test.mjs` 和 package.json 的 test 命令，
  扩展回归位于 `browser-extension/tests/options.test.mjs`、`operations.test.mjs`。
- 文档：本文件、GUI README、扩展 README 和 PHASE2.md。

## Windows 首次配对流程

1. 在 Chrome 加载 `browser-extension/`，复制扩展 ID。
2. 在 GUI「设置 → 浏览器」打开 Browser Bridge，填写扩展 ID，默认端口为 8766。
   此区域在 Standalone / Hub / Agent 模式下都显示。
3. 点击「生成或复用 Token」。默认创建 `~/.chat2local/browser.token`；文件已存在且有效时直接复用。
   返回的文件路径会自动填入当前配置草稿，点击「保存更改」后持久化。
4. 点击「复制 Token」，到 Chrome 扩展选项粘贴；扩展侧端口应一致，并启用连接、保存。
   复制通过 Windows 原生剪贴板完成，Token 明文不会返回 JavaScript。
5. 首次使用在概览页启动 Core；若 Core 正在运行，配置只提示待重启，必须由用户明确操作重启。
6. 设置页会每 3 秒查询实际 Browser 状态。扩展连接后显示「Chrome 扩展已连接」。
7. 在扩展选项中授权指定网站，或点击「授权所有网站」并手动确认 Chrome 提示。
   GUI 不代替 Chrome 执行网站授权，也不会通过 MCP 扩大权限。

已有用户可以直接填写原 Token 文件路径，或点击「生成或复用 Token」使用默认已有文件；不会覆盖文件。
Browser「重新生成 Token」需要再次确认，旧 Token 会被替换；随后需手动重启 Core，并重新复制到扩展配对。
如果原文件在用户数据目录外，可继续读取、复用和复制，但不能在该处创建或轮换；请选择私有目录内的新路径。

## Hub 与 Agent

- 本机 Hub：在 Hub 模式设置中点击「生成或复用 Token」，默认文件 `~/.chat2local/hub.token`。
  保存配置后按原流程手动启动或重启 Hub，主动复制 Token 给 Agent 配对。已有文件保留，不提供 Hub 全设备自动轮换。
- Agent：导入远程 Hub 已有 Token，默认保存为 `~/.chat2local/agent.token`，或选择已有文件并点击「使用已有 Token 文件」。
  Agent 不提供生成新的共享 Token 的入口，后端也拒绝该操作。
- 已有文件与待导入内容不同会拒绝覆盖，应选择另一个私有文件路径。
- CLI 的 `--token`、`CHAT2LOCAL_HUB_TOKEN`、角色的 token_file 优先级不变。GUI 按钮管理文件，不替换环境变量或运行中的凭据。
- Browser Token 与 Hub/Agent 共享 Token 保持独立；GUI 凭据操作及配置保存会拒绝已知的文件或内容复用。

## 文件与隐私

新建和轮换文件只允许在 Chat2Local 用户私有数据目录内，拒绝 Git 仓库、相对路径、`..`、符号链接和 junction。
读取拒绝目录、硬链接和大于 4096 字节的文件；导入值须非空且为可打印文本，Browser 还必须满足原有 32–128 位格式。
生成使用系统安全随机数，先写同目录临时文件，再独占发布或原子替换；创建冲突、权限错误或轮换失败不会覆盖旧文件。
POSIX 新文件为 0600；Windows 新文件写入前设置受保护 DACL，仅文件所有者和 SYSTEM 具有访问权限。
复用已有文件不会改写内容或更改原文件权限。

普通配置、凭据信息、Browser 状态及日志不返回 Token 明文或凭据哈希。
仅明确的导入操作接收明文，复制动作在 Python 内按需读取并写入剪贴板，返回 `{copied: true}`。
Token 复制后保留在用户剪贴板中，不自动清空剪贴板。
原生复制当前仅实现 Windows，其他平台明确返回不支持，不会将明文回退显示在页面。

## 本地接口与状态

GUI Bridge 只转发以下新增管理操作，无新增公开 MCP Tool：

| 方法 | 作用 | 返回 |
| --- | --- | --- |
| `credential_info(purpose, path)` | 检查 hub / agent / browser 文件 | path、ready / missing / invalid |
| `manage_token(purpose, path, value, regenerate, confirmed)` | 明确生成、复用、导入或确认 Browser 轮换 | path、state、reused；无明文 |
| `copy_token(purpose, path)` | 写入 Windows 原生剪贴板 | copied；无明文 |
| `browser_status()` | 查询当前本机 Core 的 Browser 状态 | 状态枚举；无明文 |

Core 既有 loopback 管理协议增加只读 `browser` 方法，由当前进程的 BrowserBridge 提供状态与所加载配置。
普通 Core status 和 runtime.json 结构不变；Core 不支持新方法时显示状态不可用，提示更新后重启，绝不猜测已连接。

状态包括 disabled、starting、listening、connected、failed、configuration_error、core_stopped、restart_required 和 unavailable。
通过比较已保存配置与运行配置、以及凭据是否仍与加载值一致，识别待重启状态；不读取日志推断连接，不持久化连接状态。
GUI 管理默认配置文件；CLI 指定其他 config 或覆盖参数时，仍以实际运行配置为准，GUI 不会擅自重启或覆盖运行实例。

## 自动化检查

后端可运行 `uv run pytest -q`。扩展运行 `npm test --prefix browser-extension`。
前端在 `gui/pywebview/frontend` 执行 `pnpm test`、`pnpm exec tsc -b` 和 `pnpm lint`。
首次准备在 `gui/pywebview/frontend` 执行 `pnpm install --frozen-lockfile`。
前端 DOM 测试使用 GUI 自己声明的 jsdom 开发依赖，无需安装浏览器扩展依赖。没有新增生产依赖。

测试使用临时用户目录、临时 loopback 端口、模拟 Chrome 及模拟 Windows 剪贴板，不读取或修改个人 Token、真实剪贴板或线上 Hub。

本轮验证结果：Python 全量 **1652 passed、31 skipped**；扩展 **41 passed**；前端交互 **6 passed**；
TypeScript 类型检查、ESLint 与 `git diff --check` 通过。未构建 GUI/EXE 分发产物，也未进行下面的真实 GUI 人工验收。
新增的一项跳过来自当前 Windows 环境不允许创建符号链接；硬链接检查已执行，其余跳过来自原有测试条件。

## 审查后的人工验收

本轮遵守先审查再构建：不重启现有 Core/扩展，不构建 GUI 分发产物。
审查后可在全新 Windows 测试用户或独立测试环境中执行：

1. 完成上述首次配对全过程，确认不用 PowerShell 或手工编辑 YAML，中文/英文界面均可用。
2. 关闭并重新打开 GUI，已有路径和配置保留，Token 文件内容与时间戳不变。
3. 扩展连接、断开、重新连接时，GUI 对应显示监听/连接状态；错误凭据或占用端口应显示启动失败。
4. 普通保存后原连接保持，提示待重启；手动重启后新配置生效。
5. 重新生成 Browser Token：取消时文件不变；确认后旧配对在手动重启 Core 后失效，新配对可恢复。
6. 独立本机 Hub 生成 Token，Agent 导入同一 Token；确认原有远程部署与凭据完全不受影响。
7. 全站授权取消或拒绝时不能操作未授权网站，允许后普通 HTTP/HTTPS 页面可用；撤销后再次拒绝。
   Chrome 管理页和商店仍然不可操作。
8. 检查真实 WebView2 的复制功能、剪贴板占用错误提示、用户账户下的文件 ACL 和 Chrome 原生权限弹窗。

全站与逐站模式的详细边界、撤销行为见 [扩展说明](../../browser-extension/PHASE2.md)。
