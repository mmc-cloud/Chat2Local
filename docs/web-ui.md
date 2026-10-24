# 独立 Core 的本地 Web UI

通过 PyPI、`uv tool` 或源码安装的 Core 自带与桌面版共用的 React 界面。
Windows、Linux、macOS 均使用本机浏览器访问；运行时不需要 Node.js、pnpm 或前端服务。

## 启动及地址

三种模式默认同时启动本地管理 HTTP：

```shell
chat2local --workspace /path/to/workspace
chat2local hub --config /path/to/config.yaml
chat2local agent --config /path/to/config.yaml
```

Hub 和 Agent 仍需要原有的角色配置与 Token。默认打开
<http://127.0.0.1:8767/>，请使用 `127.0.0.1` 而非 `localhost`。
终端和 Core 日志会记录实际 Web UI 地址。管理监听始终是 `127.0.0.1`，没有管理 host 参数。
MCP、Hub `/device/ws`、Agent 的出站连接和 Browser Bridge 均保留各自的网络设置。

Browser Bridge 使用用户配对设置的端口（默认 `8766`），不自动更换。
Web UI 未显式配置端口时，在 `8767～8776` 内选择可用端口，跳过自身的 Browser/MCP/Hub 监听端口；
只有实际绑定时遇到端口占用才尝试下一个。成功后终端记录完整访问地址，界面显示相同的实际端口。
通过 YAML 或 `--management-port` 显式设置端口时严格使用该值，即使显式设置为 `8767` 也不回退。
范围耗尽、权限不足等其他错误会清楚记录，Web UI 本次不启动，业务 Core 继续运行。

```shell
chat2local --management-port 8770
chat2local hub --management-port 8770
chat2local agent --management-port 8770
chat2local --no-web-ui
```

也可以在当前 `config.yaml` 中保存：

```yaml
management:
  enabled: true
  port: 8770
```

`--web-ui` / `--no-web-ui`、`--management-port` 优先于配置文件；可放在模式参数之前或之后。
保存配置不会热切换监听、工作区或模式，须从终端或现有服务管理器重启 Core。
默认端口范围的有限绑定尝试结束后仍失败，或管理 HTTP 运行期间异常退出时，会记录错误且不再重试；MCP、Hub、Agent 主业务继续运行。
只有成功监听后才记录 Web UI 地址。主业务退出时管理 HTTP 一并清理，主业务自身的启动错误仍正常报错。
配置校验和启动共用端口冲突规则：按当前模式检查 Browser 和适用的 MCP/Hub 监听，计入 `--port` 覆盖；
Agent 不占用本机 MCP/Hub 端口。显式端口冲突会在保存前拒绝；未指定端口则保留上述有限自动选择行为。

## 页面与生命周期

- **Overview**：显示当前进程、模式、设备、工作区和启动时间。浏览器版的启动上下文只读。
- **实际管理地址**：Overview 与 Settings 显示当前监听端口，以及默认值、配置值、CLI 覆盖或自动选择的来源。
  Settings 的配置输入框继续显示持久化字段或内置默认值；实际运行端口单独展示，不会自动写入 YAML。
- **Settings**：读取并校验当前 Core 使用的配置，包含显式 `--config` 路径；保存稀疏字段并提示重启。
  Advanced 可设置管理端口及 Linux 现有服务的名称和范围。路径使用绝对路径文本输入。
- **Devices**：三种模式均可查看当前 Core 提供的设备快照，Hub 显示已连接 Agent。
- **Logs**：复用现有日志读取、增量游标、文本与级别筛选。
- **Token management**：复用现有生成、导入、复用和 Browser Token 轮换逻辑。
  普通快照仅包含 Token 文件路径；只有明确点击复制时才向浏览器返回 Token，并使用浏览器剪贴板 API。
  含凭据的代理 URL 在普通配置响应中使用保留标记，保存其他字段不会覆盖原凭据。
- **Core 自启**：独立的立即保存开关，读取操作系统实际状态；权限不足和未知状态会显示错误，不伪报成功。

浏览器版不包含托盘、窗口关闭偏好、原生目录选择器及 Core 启动/重启按钮。
**停止 Core 会同时关闭 Web UI**，页面会显示断开提示；须从终端或服务管理器重新启动后刷新。
关闭浏览器不影响 Core。中英文切换共用现有翻译，浏览器语言保存在当前 origin 的 localStorage。

## 独立 Core 自启

自启开关只调整下次登录/开机的启动行为，不启动、停止或重启当前 Core。
新建自启配置使用当前 Python 环境的绝对解释器路径、当前模式和 CLI 覆盖项，保留显式配置路径、
工作区及经过 `WorkspaceManager` 校验的工作目录。`--working-directory` 用于恢复这个目录。
Hub/Agent 的共享 Token 必须已保存在匹配的 `token_file` 中；不把 CLI 或环境变量 Token 写进注册表、
unit 或 plist。若启动时凭据尚未落盘，请先保存正确的 Token/配置并重启 Core，再开启自启。

### Windows

使用当前用户的 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Chat2LocalCore`。
优先使用同环境中的 `pythonw.exe`，缺失则使用当前解释器。
命令经 Windows 参数转义，直接运行 `python[w] -m chat2local.main`。
由于 [Windows Run 命令长度限制](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys)，
较长的路径/参数会写入用户数据目录下的私有 `windows-core-startup.py`，Run 项仅保留解释器与该文件路径。
这个启动脚本在同一 Python 进程中进入 Core，不创建额外常驻进程；注册表更新失败时会恢复原脚本。
原有桌面 EXE 的 `Run\Chat2Local`、`launch_at_login`、`auto_start_core` 独立保留。
读取独立 Core 自启状态时会校验当前命令、解释器、工作目录及受管启动脚本；过期或失效时返回 `stale`，
界面提供修复和移除操作，修复会使用当前 Core 的启动上下文，不执行注册表中已有的命令。

### Linux

复用 systemd。优先通过当前进程的 cgroup 识别实际服务名称和用户/系统管理器；也可显式指定：

```yaml
autostart:
  service: company-chat2local-hub.service
  scope: system  # 或 user
```

现有服务仅通过 `systemctl [--user] --no-ask-password enable/disable <unit>` 管理未来启动。
读取 `LoadState`、`UnitFileState`、`FragmentPath`、`ExecStart` 等属性进行校验，前端不返回命令或环境内容。
静态、masked、间接依赖、仅 runtime 启用等状态不被错误映射为可编辑的普通开关。
不会覆盖现有 unit，不使用 `--now`、sudo、交互提权或重部署服务器服务。
没有可识别的已有服务时，可创建当前用户的
`$XDG_CONFIG_HOME/systemd/user/chat2local-core.service`（默认 `~/.config/systemd/user/`）。
该服务在用户登录后启动；登录前常驻等需求继续由现有 systemd 部署配置决定。
系统级服务是否可管理取决于当前用户已有权限；用户管理器不可用或权限不足时显示错误。
Settings 根据会话接口的平台标识显示字段：Linux 始终保留 systemd 服务名称和 scope，即使自启状态读取失败；
状态错误仍正常显示，Windows 和 macOS 隐藏这些 Linux 专属字段。

### macOS

使用用户的 `~/Library/LaunchAgents/org.chat2local.core.plist`，保存参数数组、工作目录与 `RunAtLoad`。
使用 `launchctl print-disabled gui/<uid>` 读取持久启用状态，
`launchctl enable/disable gui/<uid>/org.chat2local.core` 更新后续登录行为。
开启时同步更新受管 LaunchAgent 的启动参数与工作目录，失败时恢复原文件。
不会执行 `bootout`、`unload`、`bootstrap` 或 `kickstart`；不停止当前 Core，也不引入额外守护程序。
普通用户不需要管理员权限。该实现已进行模拟 launchd 自动化测试，尚未进行 macOS 实机验证。

## 本机浏览器安全边界

管理应用与 MCP/Hub 应用使用独立监听。它校验精确 Host 和 Origin、Fetch Metadata、JSON Content-Type，
所有 API 操作均要求同源握手获得的随机 CSRF 请求头；不配置跨域访问许可。
静态页面设置 CSP、禁止 iframe 嵌入与缓存；API 不提供任意命令执行或泛化方法调用。
此入口信任本机 OS 用户，不能替代多用户身份认证，也不应通过反向代理发布到公网。

## 构建与验证

```shell
cd gui/pywebview/frontend
pnpm install --frozen-lockfile
pnpm test
pnpm lint
pnpm build
cd ../../..
uv run pytest -q
uv build --no-sources
python scripts/check_web_ui_package.py dist/chat2local-0.2.0-py3-none-any.whl dist/chat2local-0.2.0.tar.gz
```

同一次 Vite 构建从一套源码输出桌面 `gui/pywebview/frontend/dist/` 和包内
`src/chat2local/management/static/`。后者包含标记为 HTTP transport 的 HTML 和哈希 JS/CSS，作为
包资源纳入版本管理；修改前端后须一起更新。uv_build 将这些资源包含在 wheel 和 sdist 中，
从 sdist 安装也不要求执行前端构建。发行工作流检查归档中的 HTML 及所引用资源。
`scripts/build_windows.py` 保留原桌面构建，并将 Core 包资源纳入 PyInstaller。

测试使用临时目录、独立端口及模拟 OS 服务，不操作现有生产 Hub/Agent。
覆盖三模式管理 HTTP、停止后端口关闭、浏览器请求安全、配置与凭据，以及三个平台的自启逻辑。
