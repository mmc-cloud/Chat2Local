<h1 align="center">Chat2Local</h1>

<p align="center">
  <a href="https://github.com/mmc-cloud/Chat2Local/blob/master/README.md">简体中文</a> ·
  <strong>English</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/chat2local/"><img src="https://img.shields.io/pypi/v/chat2local" alt="PyPI"></a>
  <a href="https://pypi.org/project/chat2local/"><img src="https://img.shields.io/pypi/pyversions/chat2local" alt="Python"></a>
  <a href="https://github.com/mmc-cloud/Chat2Local/actions/workflows/test.yml"><img src="https://github.com/mmc-cloud/Chat2Local/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="https://github.com/mmc-cloud/Chat2Local/blob/master/LICENSE"><img src="https://img.shields.io/pypi/l/chat2local" alt="License"></a>
</p>

<p align="center"><strong>Let ChatGPT access and operate your authorized local computers and projects through MCP.</strong></p>

Chat2Local is a lightweight MCP agent for single-user, self-hosted workflows. It can read and modify local files, run commands, manage long-running processes, and connect multiple computers or servers through a **Hub + Agent** architecture.

The project provides both **Core / CLI** and **Windows Desktop**. Core is suitable for developers, servers, and command-line environments on Linux, macOS, and Windows. Windows Desktop adds graphical management, a system tray, logs, and a portable EXE on top of the same Core.

## Features

You can ask ChatGPT to:

- Read and search local project files without repeatedly uploading them
- Modify files, apply patches, and inspect the result
- Run tests, builds, scripts, and other Shell commands
- Read output from long-running processes and continue interacting with them
- Select target devices across multiple computers or servers
- Save project workstreams or personal research, plans, and ideas with Handoff so a new Chat can continue from them
- Read and interact with websites through an optional Chrome extension, including DOM inspection, clicks, input, and scrolling

Chat2Local is developed and tested primarily around practical **ChatGPT + MCP** workflows. Other MCP-compatible clients may also connect, but they are not currently the main test target.

## Architecture

For a single device, ChatGPT connects directly to Chat2Local:

```text
ChatGPT
   │ MCP / HTTPS
   ▼
Chat2Local
   │
   └── Local workspace / processes
```

For multiple devices, the Hub exposes one MCP endpoint and Agents connect outbound to it:

```text
                    ┌── Hub local workspace
                    │
ChatGPT ── MCP ──▶ Hub
                    │
                    ├── Agent: Windows PC
                    ├── Agent: Server
                    └── Agent: Other device
```

Agents do not expose an MCP endpoint to ChatGPT and do not need public inbound ports. They only need to be able to connect outbound to the Hub.

## Installation

### Windows Desktop

Windows users can download the portable build from [GitHub Releases](https://github.com/mmc-cloud/Chat2Local/releases):

```text
Chat2Local-v<version>-windows-x64.zip
```

Extract the archive and run:

```text
Chat2Local.exe
```

The portable build already includes the Python runtime, Core, and the Desktop GUI. A system-wide Python installation is not required.

### Core / CLI

Core requires Python 3.12+.

Independent Core includes a local Web UI on Windows, Linux, and macOS. After starting Core, open
[http://127.0.0.1:8767/](http://127.0.0.1:8767/) (the actual address is also logged) to manage settings,
logs, devices, credentials, and login startup. Node.js is not required at runtime.
Use `--management-port 8770` to select another port or `--no-web-ui` to disable it.
Browser Bridge keeps its configured port (default 8766). Without an explicit port, Web UI tries up to ten ports
starting at 8767 and logs the actual URL. A port explicitly set in configuration or CLI never falls back.
See [Web UI and cross-platform startup](docs/web-ui.md). The Windows ZIP / EXE workflow is preserved.

Using `uv tool` is recommended because it installs Chat2Local into an isolated environment:

```powershell
uv tool install chat2local
chat2local --help
```

You can also install it with pip:

```powershell
python -m pip install chat2local
chat2local --help
```

## Quick Start

### Standalone

Run Chat2Local from the directory you want to authorize:

```powershell
chat2local
```

Or specify the workspace explicitly:

```powershell
chat2local --workspace D:\Projects\example
```

If `--workspace` is omitted, the current working directory at startup becomes the default workspace.

### Hub + Agent

Hub:

```powershell
$env:CHAT2LOCAL_HUB_TOKEN = '<shared-secret>'
chat2local hub --device-id hub --host 127.0.0.1 --port 8765
```

Agent:

```powershell
$env:CHAT2LOCAL_HUB_TOKEN = '<shared-secret>'
chat2local agent --device-id desktop --hub-url wss://hub.example.com/device/ws
```

The Hub and all Agents use the same shared token. Agents connecting over the public internet should use `wss://`.

The Hub example above listens on loopback only. Bind the Hub directly to `0.0.0.0` only when a firewall, TLS reverse proxy, or another trusted network boundary is already in place. `CHAT2LOCAL_HUB_TOKEN` protects only the Hub ↔ Agent `/device/ws` connection; it does not protect the MCP `/mcp` endpoint. A public MCP endpoint should use OAuth or another trusted access-control layer.

### Connect to ChatGPT

ChatGPT Web cannot access an MCP Server running only on `localhost`, so Chat2Local needs a connection path that ChatGPT can reach.

#### OpenAI Secure MCP Tunnel

For personal use, local development, or cases where you do not want to expose the MCP Server publicly, you can use OpenAI's official [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

`tunnel-client` runs on a machine or network that can reach Chat2Local and establishes an outbound HTTPS connection to OpenAI. Chat2Local itself does not need a public inbound port or a public HTTPS address.

Typical topology:

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

When creating a custom MCP Server / Plugin in ChatGPT, choose **Tunnel** as the connection type and select or enter the corresponding `tunnel_id`.

In Standalone mode, `tunnel-client` can run on the same machine as Chat2Local. In Hub + Agent mode, it can run on the Hub machine or anywhere else that can reach the Hub MCP endpoint.

Secure MCP Tunnel is well suited to private connections and development. If you plan to publish a Plugin publicly, you still need a stable, publicly reachable HTTPS MCP endpoint.

#### Public HTTPS endpoint

Another option is to expose the Chat2Local MCP endpoint through a reverse proxy, tunnel service, or your own domain, for example:

```text
https://mcp.example.com/mcp
```

Then create a custom MCP Server / Plugin in ChatGPT and provide that Server URL. Public deployments should use HTTPS and enable OAuth or another trusted access-control mechanism.

After connecting, you can verify the setup by asking ChatGPT to read the current workspace or call `list_devices`.

## MCP Tools

Chat2Local exposes 12 MCP tools covering files, processes, multi-device routing, Handoff, and optional capabilities.
Browser Use adds 24 Chrome capabilities through `capability_discover` and `capability_execute`, including tab activation/closing/moving/pinning, normal-window management, navigation status/bounded waits, optional tab-group management, and existing page reading and DOM interactions. Window tab summaries support bounded pagination with an exact window ID and stateless snapshot to detect changed membership/order. Closing a window requires user confirmation in the Agent conversation and a separate check of the complete expected tab ID set; navigation.go revalidates its target and only initiates navigation. Waits default to 5 seconds (8 maximum); an old complete page is not proof of a new navigation, and Chrome complete is not SPA business readiness. Group capabilities require independent optional tabGroups permission granted by a real click in extension Options; partial multi-step effects are reported without rollback or automatic retries. The paired extension handles browser operations, with additional per-site consent for page content and DOM access. Download the extension ZIP from [GitHub Releases](https://github.com/mmc-cloud/Chat2Local/releases) and follow the [extension setup guide](browser-extension/README.md).
For observe-then-wait URL changes, use the short-lived random `url_baseline_id` returned by `navigation.status`, bound to the exact tab and full URL without exposing sensitive URL data. An independently known exact `baseline_url` remains supported as a mutually exclusive alternative. Group moves verify actual position and contiguous membership, accept legal Chrome index adjustments, and return `actual_index`; an unachieved position fails verification.

Current tools:

```text
read · search · apply_patch · exec_command · interact_process
kill_process · list_devices · handoff_list · handoff_get · handoff_save
capability_discover · capability_execute
```

Remote calls can specify a target device through `device`. If omitted, the MCP Server's local device is used.

All three Handoff tools accept an optional `scope`. The default, `"workspace"`, groups records by
project workspace and preserves existing calls. Use `"personal"` for research, plans, or early ideas
without a specific project. Personal calls need no workspace and reject an explicitly supplied
`workspace` (including `null`). Identical Workstream IDs in different scopes identify independent records.

Both scopes persist Markdown in the selected device's user-data directory: Workspace records use
`~/.chat2local/handoffs/<workspace-key>/`; Personal records use `~/.chat2local/handoffs/_personal/`.
Existing files need no migration, and devices do not synchronize automatically. Start with
`handoff_list` for titles and summaries, then use `handoff_get` for the body as needed. Create with
`handoff_save` and `expected_revision=0`; update using the latest revision.

## Configuration

Default configuration file:

```text
~/.chat2local/config.yaml
```

See the full example in [`config.example.yaml`](https://github.com/mmc-cloud/Chat2Local/blob/master/config.example.yaml).

Minimal Hub / Agent example:

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

`security.allowed_roots` limits which local directories may be selected as workspaces. The default value `[]` does not grant access to the entire filesystem; it authorizes only the default workspace selected at startup.

CLI arguments override settings only for the current run and are not written back to `config.yaml`. Windows Desktop Settings can edit the persisted configuration.

### Advanced configuration

Chat2Local also supports:

- Agent → Hub connections through the system proxy, direct mode, or explicit HTTP / HTTPS / SOCKS4 / SOCKS5 proxy URLs
- OAuth Resource Server mode for Standalone / Hub MCP HTTP endpoints
- WorkOS AuthKit as the currently supported OAuth provider

Core / CLI requires the additional `python-socks[asyncio]` dependency when using SOCKS. The Windows Desktop portable build already includes it.

## Security Model

Chat2Local provides explicit application-level access boundaries, but it is not an OS sandbox.

- File tools are always restricted to the current workspace
- A workspace must be inside the device's configured `security.allowed_roots`
- `..`, absolute paths outside the workspace, and symlink / junction escapes are blocked
- `exec_command` still runs with the permissions of the OS user running Chat2Local
- The Agent shared token protects only Hub ↔ Agent communication
- Public MCP endpoints should use HTTPS and OAuth or another trusted access-control layer
- Logs do not record tokens, full authentication payloads, or Tool argument contents

For stronger isolation, run Chat2Local under a dedicated OS user, container, virtual machine, or another system-level isolation mechanism.

## Windows Desktop

The Desktop GUI currently provides:

- Core status and Standalone / Hub / Agent start-stop controls
- Workspace selection
- Hub online-device list
- `config.yaml` settings editing
- Core log viewer
- Windows system tray
- Start at login and silent startup
- Automatic Core startup
- 简体中文 / English
- Single-instance behavior

Closing the main window hides it to the system tray by default. Exiting Desktop does not automatically stop a running Core.

Desktop development and packaging documentation is available at [`gui/pywebview/README.md`](https://github.com/mmc-cloud/Chat2Local/blob/master/gui/pywebview/README.md).

## Run and Build from Source

To run, test, or modify Chat2Local directly from the repository:

```powershell
uv sync
uv run pytest
```

Build the Windows portable package:

```powershell
uv sync --group pywebview-gui --group desktop-build
uv run --group pywebview-gui --group desktop-build python scripts/build_windows.py
```

Output directory:

```text
dist/windows/Chat2Local/
```

## License

Chat2Local is licensed under the [MIT License](https://github.com/mmc-cloud/Chat2Local/blob/master/LICENSE).

