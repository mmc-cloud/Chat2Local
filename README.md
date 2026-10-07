<h1 align="center">Chat2Local</h1>

**简体中文** | [English](https://github.com/mmc-cloud/Chat2Local/blob/master/README.en.md)

[![PyPI](https://img.shields.io/pypi/v/chat2local)](https://pypi.org/project/chat2local/)
[![Python](https://img.shields.io/pypi/pyversions/chat2local)](https://pypi.org/project/chat2local/)
[![Tests](https://github.com/mmc-cloud/Chat2Local/actions/workflows/test.yml/badge.svg)](https://github.com/mmc-cloud/Chat2Local/actions/workflows/test.yml)
[![License](https://img.shields.io/pypi/l/chat2local)](https://github.com/mmc-cloud/Chat2Local/blob/master/LICENSE)

**让 ChatGPT 通过 MCP 访问并操作你授权的本地电脑和项目。**

Chat2Local 是一个面向单用户、自托管场景的轻量 MCP Agent。它可以读取和修改本地文件、运行命令、管理长时间运行的进程，并通过 **Hub + Agent** 模式连接多台电脑或服务器。

项目同时提供 **Core / CLI** 和 **Windows Desktop**：前者适合开发者、服务器和命令行环境，后者在同一套 Core 外提供图形化管理、系统托盘、日志和 portable EXE。

## Features

你可以直接在 ChatGPT 中让它：

- 阅读和搜索本地项目代码，而不需要反复上传文件
- 修改文件、应用 patch，并继续检查修改结果
- 运行测试、构建、脚本或其他 Shell 命令
- 查看长时间运行进程的输出，并继续与进程交互
- 在多台电脑或服务器之间选择目标设备执行操作
- 通过 Handoff 保存当前工作线，让新的 Chat 继续之前的项目上下文

Chat2Local 主要围绕 **ChatGPT + MCP** 的实际工作流开发和测试。其他支持 MCP 的客户端也可以连接，但目前不是主要测试目标。

## 架构

单设备时，ChatGPT 直接连接 Chat2Local：

```text
ChatGPT
   │ MCP / HTTPS
   ▼
Chat2Local
   │
   └── Local workspace / processes
```

多设备时，由 Hub 提供统一 MCP endpoint，各 Agent 主动连接 Hub：

```text
                    ┌── Hub local workspace
                    │
ChatGPT ── MCP ──▶ Hub
                    │
                    ├── Agent: Windows PC
                    ├── Agent: Server
                    └── Agent: Other device
```

Agent 不对 ChatGPT 暴露 MCP endpoint，也不需要对公网开放入站端口；它只需要能够主动连接 Hub。

## 安装

### Windows Desktop

Windows 用户可以从 [GitHub Releases](https://github.com/mmc-cloud/Chat2Local/releases) 下载：

```text
Chat2Local-v<version>-windows-x64.zip
```

解压后直接运行：

```text
Chat2Local.exe
```

portable 版本已经包含 Python runtime、Core 和 Desktop GUI，不要求系统预先安装 Python。

### Core / CLI

Core 要求 Python 3.12+。

推荐使用 `uv tool` 安装到独立环境：

```powershell
uv tool install chat2local
chat2local --help
```

也可以使用 pip：

```powershell
python -m pip install chat2local
chat2local --help
```

## 快速开始

### Standalone

在希望授权给 Chat2Local 的目录中运行：

```powershell
chat2local
```

也可以显式指定 workspace：

```powershell
chat2local --workspace D:\Projects\example
```

如果不传 `--workspace`，启动时的当前目录就是默认 workspace。

### Hub + Agent

Hub：

```powershell
$env:CHAT2LOCAL_HUB_TOKEN = '<shared-secret>'
chat2local hub --device-id hub --host 127.0.0.1 --port 8765
```

Agent：

```powershell
$env:CHAT2LOCAL_HUB_TOKEN = '<shared-secret>'
chat2local agent --device-id desktop --hub-url wss://hub.example.com/device/ws
```

Hub 和 Agent 使用同一个 shared token。公网 Agent 应使用 `wss://`。

上面的 Hub 示例只监听 loopback。只有在已经配置防火墙、TLS 反向代理或其他可信网络边界时，
才应把 Hub 直接监听到 `0.0.0.0`。`CHAT2LOCAL_HUB_TOKEN` 只保护 Hub ↔ Agent 的 `/device/ws`，
不会保护 MCP `/mcp` endpoint；公网 MCP endpoint 应另外启用 OAuth 或可信的外部访问控制。

### 连接到 ChatGPT

ChatGPT Web 不能直接访问 `localhost` 上的 MCP Server，因此需要为 Chat2Local 提供一条 ChatGPT 可以使用的连接路径。

#### OpenAI Secure MCP Tunnel

对于个人使用、本地开发或不希望把 MCP Server 暴露到公网的场景，可以使用 OpenAI 官方的 [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)。

`tunnel-client` 运行在能够访问 Chat2Local 的机器或网络中，并主动通过 HTTPS 连接 OpenAI。这样 Chat2Local 本身不需要开放公网入站端口，也不需要拥有公开 HTTPS 地址。

典型结构：

```text
ChatGPT
   │
   │ OpenAI Secure MCP Tunnel
   ▼
tunnel-client
   │
   ▼
Chat2Local /mcp
```

在 ChatGPT 中创建自定义 MCP Server / Plugin 时，Connection 选择 **Tunnel**，再选择或填写对应的 `tunnel_id` 即可。

Standalone 模式下，`tunnel-client` 可以直接运行在本机；Hub + Agent 模式下，可以把它运行在 Hub 所在机器或其他能够访问 Hub MCP endpoint 的环境中。

Secure MCP Tunnel 适合私有连接和开发测试；如果准备公开发布 Plugin，则仍需要稳定、可公网访问的 HTTPS MCP endpoint。

#### 公网 HTTPS endpoint

另一种方式是通过反向代理、隧道服务或自己的域名，把 Chat2Local 的 MCP endpoint 暴露为公网 HTTPS 地址，例如：

```text
https://mcp.example.com/mcp
```

然后在 ChatGPT 中创建自定义 MCP Server / Plugin，并填写该 Server URL。公网部署建议启用 HTTPS，并配置 OAuth 或其他可信的访问控制。

连接成功后，可以先让 ChatGPT 读取当前 workspace，或者调用 `list_devices` 验证 Chat2Local 是否已经正常连接。

## MCP Tools

Chat2Local 当前公开 10 个 MCP tools，覆盖文件读取与搜索、patch 修改、Shell / Process 操作、多设备选择和 Handoff。

当前公开的 Tool：

```text
read · search · apply_patch · exec_command · interact_process
kill_process · list_devices · handoff_list · handoff_get · handoff_save
```

远程调用可以通过 `device` 指定目标设备；省略时使用当前 MCP Server 所在设备。

## 配置

默认配置文件：

```text
~/.chat2local/config.yaml
```

完整示例见 [`config.example.yaml`](https://github.com/mmc-cloud/Chat2Local/blob/master/config.example.yaml)。

一个最小的 Hub / Agent 配置示例：

```yaml
hub:
  device_id: hub
  host: 127.0.0.1
  port: 8765
  token_file: ~/.chat2local/hub.token

agent:
  device_id: desktop
  hub_url: wss://hub.example.com/device/ws
  token_file: ~/.chat2local/hub.token
  proxy: system
```

`security.allowed_roots` 用来限制本机可选择的 workspace 范围。默认值 `[]` 不代表开放整个文件系统，而是只授权启动时的默认 workspace。

CLI 参数只覆盖当前启动，不会自动写回 `config.yaml`。Windows Desktop 的 Settings 可以编辑持久化配置。

### 进阶配置

Chat2Local 还支持：

- Agent → Hub 使用系统代理、直连或显式 HTTP / HTTPS / SOCKS4 / SOCKS5 代理
- Standalone / Hub MCP HTTP endpoint 启用 OAuth Resource Server 模式
- 当前 OAuth provider 支持 WorkOS AuthKit

Core / CLI 使用 SOCKS 时需要额外安装 `python-socks[asyncio]`；Windows Desktop portable 已内置该依赖。

## 安全模型

Chat2Local 提供明确的应用级访问边界，但它不是 OS sandbox。

- 文件工具始终限制在当前 workspace 内
- workspace 必须位于本机允许的 `security.allowed_roots` 内
- 阻止通过 `..`、workspace 外绝对路径、symlink / junction 逃逸
- `exec_command` 仍拥有 Chat2Local 当前 OS 用户的权限
- Agent shared token 只保护 Hub ↔ Agent 连接
- 公网 MCP endpoint 应使用 HTTPS，并配置 OAuth 或可信的外部访问控制
- 日志不会记录 token、完整认证 payload 或 Tool 参数内容

如果需要更强隔离，应配合独立 OS 用户、容器、虚拟机或其他系统级机制使用。

## Windows Desktop

Desktop GUI 当前提供：

- Core 状态和 Standalone / Hub / Agent 启停
- workspace 选择
- Hub 在线设备列表
- `config.yaml` 设置编辑
- Core 日志查看
- Windows 系统托盘
- 登录启动与静默启动
- Core 自动启动
- 简体中文 / English
- single-instance

关闭窗口默认隐藏到系统托盘；退出 Desktop 不会自动停止正在运行的 Core。

Desktop 的开发与打包说明见 [`gui/pywebview/README.md`](https://github.com/mmc-cloud/Chat2Local/blob/master/gui/pywebview/README.md)。

## 从源码运行与构建

如果你希望直接从仓库运行、测试或修改 Chat2Local，克隆仓库后：

```powershell
uv sync
uv run pytest
```

构建 Windows portable：

```powershell
uv sync --group pywebview-gui --group desktop-build
uv run --group pywebview-gui --group desktop-build python scripts/build_windows.py
```

输出目录：

```text
dist/windows/Chat2Local/
```

## License

Chat2Local 使用 [MIT License](https://github.com/mmc-cloud/Chat2Local/blob/master/LICENSE)。
