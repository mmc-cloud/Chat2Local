# Chat2Local

单用户本地 MCP 工具。同一个安装包支持单设备 standalone 和可选的多设备 Hub + Agent。
Stage 0-4 已完成，公共工具为 `read`、`search`、`apply_patch`、`exec_command`、
`interact_process`、`kill_process`、`handoff_list`、`handoff_get`、`handoff_save`、`list_devices`。
Local Dispatcher capabilities = 9；MCP public tools = 10（额外包含 MCP-only 的 list_devices）。

```powershell
uv sync
uv run chat2local
```

不传 `--workspace` 时使用启动时当前目录；显式传入时使用指定目录。
读取 `~/.chat2local/config.yaml`，配置缺失时使用内置默认值。
`config.example.yaml` 是示例；每台设备自己的 `security.allowed_roots` 是本地授权上限，
默认 `[]` 只授权本次启动的 default workspace。

Runtime Management V0.1：每台设备、每个 OS 用户同时只能运行一个 Core（standalone / hub / agent
共用限制）。多 workspace 继续通过 Tool 的 workspace 参数和 allowed_roots 选择。
复用用户数据目录的 `runtime.lock`：Windows 使用 `msvcrt.locking`，Linux/macOS 使用
`fcntl.flock`；进程全生命周期持锁，残留锁文件不影响重启。第二个 Core 启动报
`Chat2Local is already running`。

取得锁后，先启动本地 control listener，再原子发布 `~/.chat2local/runtime.json`，最后运行 Core。
descriptor 字段为 `schema_version=1`、`instance_id`（每次启动新 UUID）、`pid`、`mode`、
`device_id`、`workspace`（startup workspace）、`started_at`（UTC RFC3339）、`version`（包 metadata），
以及 `control={transport: tcp, host: 127.0.0.1, port: <OS 分配端口>}`；不保存认证或 Tool 参数。
descriptor 仅用于发现，客户端必须 ping 并比对 instance_id，不能仅凭文件判断在线。

Control 使用 IPv4 loopback TCP，与 HTTP / MCP 独立。每个连接只处理一个 UTF-8 JSON request / response，
均以换行结束。request 严格为 `{"id":"1","method":"ping"}`：id 为 1–128 字符串，method
仅允许 `ping`、`status`、`stop`，拒绝未知字段。请求最大 4096 bytes、响应最大 16384 bytes
（含换行），读取和发送等待最多 5 秒；客户端也应为 TCP connect 设置 timeout。

- 成功响应：`{"id":"1","ok":true,"result":{...}}`。
- ping result：`{"instance_id":"..."}`。
- status result：descriptor 的实例信息字段（不含 schema_version/control），加实时 `state`。
  Agent 复用连接状态；Hub/Standalone 使用 starting / running；退出期间为 stopping。
- stop result：`{"accepted":true}`。回送确认后触发统一 shutdown signal，重复 stop 幂等。
- 失败响应：`{"id":null,"ok":false,"error":"invalid_request"}`，另有
  request_too_large / request_timeout；response_too_large 保留合法 request id。响应后关闭连接。

Agent stop 取消主任务并等待其现有 finally；HTTP Core 设置 Uvicorn `should_exit`，继续走
FastAPI lifespan 的 registry / process cleanup。Ctrl+C、正常退出和运行错误也走统一清理：
Core → control → 自己的 descriptor → 释放锁。异常 kill 可留下 descriptor；下次成功持锁后安全替换。
V0.1 不增加 control token，loopback 控制不构成针对本机其他进程的安全沙箱。
GUI 和 status/stop CLI 尚未实现。

多设备：在 Hub 和每台 Agent 上设置同一个 `CHAT2LOCAL_HUB_TOKEN`，也可以用 `--token` 覆盖。
Standalone 无需 token 或 Hub。

```powershell
$env:CHAT2LOCAL_HUB_TOKEN = '<your shared secret>'
uv run chat2local hub --device-id hub --host 0.0.0.0 --port 8765
uv run chat2local agent --device-id desktop --hub-url wss://hub.example.com/device/ws
```

Hub 和 Agent 都是角色，均可运行于 Windows/Linux/macOS。
在各自机器的 `~/.chat2local/config.yaml` 中保存对应启动配置；以下为通用示例，按需替换设备名和 Hub 地址：

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

把共享 token 写入 `token_file` 指定的 UTF-8 文本文件后，可直接运行 `chat2local hub`
或 `chat2local agent`。

| 启动项 | 优先级（从高到低） |
| --- | --- |
| Hub device_id | `--device-id` → `hub.device_id` → hostname |
| Hub host | `--host` → `hub.host` → `127.0.0.1` |
| Hub port | `--port` → `hub.port` → `8765` |
| Agent device_id | `--device-id` → `agent.device_id` → hostname |
| Agent hub_url | `--hub-url` → `agent.hub_url`，缺少时启动报错 |
| 两种角色的 token | `--token` → `CHAT2LOCAL_HUB_TOKEN` → 对应角色的 `token_file`，缺少时启动报错 |

Hub 和 Agent 共用 token 解析及文件读取逻辑。token 文件路径支持 `~`，读取后去除首尾空白/换行；
只有 CLI 和环境变量均未提供 token 时才读取文件。文件不存在、不可读、不是有效 UTF-8 或内容为空
都会报清晰错误，错误不会输出 token 内容。配置继续拒绝未知字段。
原有带 CLI 参数或环境变量启动方式继续有效。standalone 不读取这两个角色的 token 文件。
workspace 不存入配置；Hub/Agent 与 standalone 一样，未传 `--workspace` 时使用启动目录 `Path.cwd()`。

`agent.proxy` 仅控制 Agent → Hub 的 WebSocket 连接，默认 `system` 保持系统代理发现行为：

| agent.proxy | 显式传给 websockets 的参数 |
| --- | --- |
| `system` | `proxy=True`，跟随 OS / 环境变量代理发现及 bypass 规则 |
| `direct` | `proxy=None`，强制直连 |
| `http://127.0.0.1:7897` 等代理 URL | 原样传入 `proxy` |

支持 HTTP/HTTPS、SOCKS4/4a/5/5h 代理 URL。SOCKS 需要额外安装 `python-socks[asyncio]`；
配置侧仅用标准库校验显式 URL 的结构，不读取或解析系统代理；系统代理发现及最终代理解析由
websockets `connect()` 完成。非法显式 URL 在启动阶段报错；系统代理非法或 SOCKS 缺少依赖
在连接建立阶段报清晰配置错误并停止，不无限重试。代理 URL 可以带认证信息，日志不会输出完整认证 URL。
本轮不增加 `--proxy`，通过现有 `--config` 选择配置文件即可。

日志使用 Python 标准库，默认 INFO：显示角色启动/停止、Agent 首次连接和重连成功。
可恢复网络、代理和握手失败记录 WARNING（安全错误类别和重试间隔），保持 1–30 秒重连退避；
设备已连接冲突可以重试，记录 WARNING；无效 token、Hub 本机 device_id 冲突、hello/请求协议
不兼容记录安全的 ERROR 并停止。未知注册拒绝只显示 `Registration rejected` 并停止，
不会回显 Hub 的任意原始 reason。远程请求超时记录 WARNING，不输出异常正文、token 或认证 payload。
无明确异常关闭显示 `connection closed`；实际异常断线/连接建立失败分别显示 `lost` / `failed` 及安全类别。
启动/配置错误直接停止并显示简洁错误；普通 Tool 失败返回可读错误，不输出 traceback。
未知内部错误只向调用方返回 `Internal ... error`，本地 ERROR 日志保留 traceback 用于诊断。
CLI 日志初始化会替换已有 root handlers，确保安装 SafeFormatter；shutdown 失败有本地 traceback，
已有主流程异常时保留主异常，正常主流程退出后 shutdown 失败才成为最终失败。

Console 和文件日志共用 SafeFormatter 脱敏；文件位于 `~/.chat2local/logs/chat2local.log`，
使用 UTF-8 和 UTC 时间戳，单文件 5 MiB，保留 3 个轮转备份。Runtime 日志按 Core/device 统一存放，
不按 workspace 分文件；Handoff 继续按 workspace 隔离。本地 Tool 执行记录开始（DEBUG）、
成功（INFO）或预期失败（WARNING），包含耗时及失败类型，不记录 arguments、results 或内容。
未知内部错误仍只记录一份 ERROR traceback。文件日志初始化或写入失败会安全警告，Console 保持可用，
不阻断 Core 功能。

`--debug` 可放在角色参数前后，例如 `chat2local agent --debug`；启用项目 DEBUG 日志，
增加连接阶段、errno 和 WebSocket close code，不启用第三方认证/帧/完整请求体日志。
Agent runner 精确识别 websockets `connection_lost()` 中缺少 `recv_messages` 的已知 asyncio
二次异常，将其降为 DEBUG；其他 callback 异常仍交给原有/default exception handler，保留 traceback。
这只是降噪，不修复网络断线或第三方连接生命周期问题。

Agent 主动连接，不暴露 MCP。ChatGPT 只连接 Hub MCP：`/mcp`；健康检查为 `/health`。

MCP HTTP 认证可选，standalone 和 Hub 使用相同配置。默认关闭，旧 config 无需修改，
本地运行及 OpenAI Secure MCP Tunnel 保持现有行为：

```yaml
auth:
  mode: none
```

第一版 OAuth 支持 WorkOS AuthKit 作为外部 Authorization Server：

```yaml
auth:
  mode: oauth
  provider: workos
  issuer_url: https://your-env.authkit.app
  resource_server_url: https://example.com/mcp
```

两个 URL 必须是合法 HTTPS，不能含认证信息、query 或 fragment；resource_server_url 的 path 必须严格为 `/mcp`。
issuer / resource 按配置精确
验证 JWT 的 `iss` / `aud`。Chat2Local 只作为 Resource Server，通过 `{issuer_url}/oauth2/jwks`
验证 WorkOS 签发的 RS256 JWT 签名、有效期和必需 claims。JWKS 缓存 5 分钟，未知 kid 按 PyJWT
的 30 秒冷却策略刷新；网络 I/O 在工作线程中完成。无效 JWT 或 JWKS 获取/解析失败返回标准 401，
不记录 token。`scope` 只映射到 SDK，不添加 Tool scope、RBAC 或用户名单。

OAuth 模式的 `GET /mcp` 缺少 Bearer token 时直接返回 401；`WWW-Authenticate` 指向 SDK 提供的
`https://example.com/.well-known/oauth-protected-resource/mcp`，metadata 的 resource 为
`https://example.com/mcp`，authorization_servers 为配置中的 issuer。SDK 应用在 OAuth 模式挂到
根路径，使 well-known 路径正确；默认 none 保留原有 `/mcp/` 挂载行为。
OAuth 模式仍启用 DNS rebinding 校验，允许配置中的公网 Host/Origin 和原有 loopback 开发地址。

在 WorkOS Dashboard 中开启 MCP CIMD，将 Resource Indicator 设置为与 `resource_server_url`
完全一致的 URL；Signup 可由部署者按需关闭。参见 [WorkOS MCP 配置指南](https://workos.com/docs/authkit/mcp)。
Chat2Local 不实现 `/authorize`、`/token`、refresh token 或用户注册，不使用 client secret，
不保存 WorkOS password、API key、billing information 或 OAuth access token。
OAuth 仅作用于 Streamable HTTP MCP；`/health` 继续开放，`/device/ws` 继续使用 Hub shared token，
Agent 不启用 OAuth。认证不改变 workspace 限制、设备路由或任何 Tool 行为。

先调用 `list_devices()`，再为文件或进程 Tool 传 `device="desktop"`。
省略 device 使用服务器本机；workspace 始终是目标设备上的目录，并受该设备本地权限检查。

`apply_patch(patch, workspace=None, device=None)` 支持严格 Codex 风格的文件新增/修改/移动/删除和目录移动。
整份 patch 预检成功才提交，严格匹配 context，保留现有 UTF-8 / BOM UTF-16 和换行风格。
提交失败会报告已完成步骤；不承诺多文件事务 rollback。超时或断线后应先检查实际状态，不能自动重试。

Process Tool 接口：

```text
exec_command(command, cwd=".", workspace=None, device=None)
interact_process(process_id, input=None, device=None)
kill_process(process_id, device=None)
```

`exec_command` 使用目标设备配置的 Shell，默认等待 10 秒；超时仍在运行，不会自动 kill。
三者始终返回 managed `process_id`，不接受任意 OS PID。stdout/stderr 分离，单次共 32 KiB；
快速退出的大输出通过 `interact_process` 继续读取，`stdout_has_more` / `stderr_has_more` 表示还有未读输出。
`stdout_dropped` / `stderr_dropped` 表示本次读取遇到缓冲区淘汰；`draining` 表示尚未读完 pipe EOF。
无 input 立即读取；有 input 原样 UTF-8 写入，不自动加换行，写后短等 250ms。
`kill_process` 返回 `terminated` 或 `already_exited`，并读取一页输出；剩余输出在保留期内仍可读取。
非零 exit code、等待超时、输出分页和 dropped 都是普通结果。

远程进程的后续 `interact_process` / `kill_process` 必须显式带创建时相同的 `device`；Hub 不猜进程归属。
每个 Dispatcher 独立持有 ProcessManager；服务正常退出清理本机进程树，Agent 临时断线保留进程，
仅 Agent runtime 退出时清理。远程 timeout/disconnect 不保证本机操作取消，应先检查目标设备实际状态，
不能自动 retry/replay。`cwd` 受 workspace 限制，Shell 仍拥有 Chat2Local 当前 OS 用户权限。
V0.1 使用 PIPE；不提供 OS sandbox、PTY 或持久化进程恢复，脱离受管理进程树的子程序不保证清理。

Handoff / Resume 为新 Chat 保存某条 workstream 的最新交接状态；长期项目约定仍放在 `AGENTS.md`。

```text
handoff_list(workspace=None, device=None)
handoff_get(workstream, workspace=None, device=None)
handoff_save(workstream, title, summary, content, expected_revision, workspace=None, device=None)
```

`workstream` 是稳定机器 ID / 文件名，`title` 是可读工作线名称，`summary` 是简短当前状态，
`content` 是完整恢复上下文。title / summary 由调用模型生成并显式提供；Chat2Local 本身没有 LLM，
只验证和保存，不自动生成、总结或改写这些语义内容。
title 为 strict string、1–120 字符；summary 为 strict string、1–500 字符。两者允许中文、emoji、冒号，
禁止 CR/LF、空字符串和纯空白；成功保存时保留原字符串，包括首尾空格。

Handoff scope = workspace；Handoff storage = Chat2Local user-data directory。
workstream 必须匹配 `^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$`，固定保存到目标设备的
`~/.chat2local/handoffs/<workspace-key>/<workstream>.md`，复用 config.yaml 的用户数据根目录。
workspace 省略使用目标设备默认目录，绝对路径 override 仍受其 allowed_roots 限制。
普通文件工具保持 workspace-bounded，Handoff 内部数据目录不要求位于 workspace 内。
workspace-key 来自 WorkspaceManager 规范化的绝对路径，将路径结构及非法文件名字符替换为 `-`，
保留中文和普通目录名，例如 `D--Projects-chat2local`。每个目录的 workspace.json 记录 canonical_path
和 display_name；不同 workspace 碰撞时追加规范化路径的 8 位 hash。key 使用 200 UTF-8 bytes 的长度上限，
未超限时原样保留；超限时按完整 Unicode 字符截断可读前缀（最多 191 bytes），再追加 `-<8 hex hash>`。
collision fallback 使用相同字节预算，最终目录名最多 200 bytes，低于常见文件系统的 255-byte component 上限。
已有目录缺失或损坏 workspace.json 时拒绝猜测归属。
不允许任意文件路径或 symlink/junction 别名；远程 Handoff 实际保存在 Agent，Hub 无中央存储。
list 返回排序后的 workstream/title/summary/revision/updated_at（不含正文），让新 Chat 先发现并理解已有工作线，
再选择读取完整 Handoff；存储目录不存在时返回空列表且不创建目录。get 返回同样 metadata 加正文；save 返回完整 metadata。
缺少文件报 `handoff_not_found`，损坏 metadata 报 `invalid_handoff`，非法 slug 报 `invalid_workstream`。

创建时 `expected_revision=0`，成功得到 revision 1。更新先 get，save 提供读取到的 revision；
成功递增 1。冲突报 `revision_conflict` 且不写入，由模型重新 get、阅读和合并正文后再保存。
每个 Dispatcher 共用一个 Store；同一 workstream 的 revision 读取/校验与 temp + replace 全在同一把锁内，
两个使用相同 revision 的并发更新只有一个成功。取消中的后台写入完成前不会释放锁，Agent 重连保留同一个 Store。

文件为 UTF-8、LF；固定 header 包含 revision、UTC updated_at、title 和 summary，无需 YAML parser。
title / summary / content 作为一个状态在同一 revision 下原子保存，content 参数/返回值只含正文。
revision/updated_at/title/summary 必须完整且有效；缺失或无效 metadata 均报 invalid_handoff，不自动修复。
正文自由格式，不自动总结、截断或追加日志；正文换行统一为 LF。写入失败保留原文件并尽力清理 temp。
只保存最新状态，不自动保存每轮聊天、不建立 Task/Session 系统或 revision 历史；V0.1 不提供跨进程/外部编辑器
事务锁或 semantic merge。超时/断线后先 get 实际状态再决定下一步。

MCP Server 在 initialize 阶段广播一份精简的 server-wide instructions：进行项目工作前，
模型应先读取目标 workspace 的 `AGENTS.md`（存在时）并将其作为项目指导。Handoff 的发现与复用、
保存时机、正文范围和 revision 冲突处理统一由 MCP instructions 指导，用户不需要记忆或维护 workstream slug。
`AGENTS.md` 保持精简，只保存项目开发相关的长期约束。具体项目规则仍保存在各项目自己的
`AGENTS.md`，不会把某个项目全文硬编码进全局 MCP instructions。

公网 Agent 必须使用 WSS；内网可信网络可用 WS。Hub/Agent 通信和 MCP 的 Tunnel / HTTPS 暴露方式相互独立。
共享 token 只保护设备 WebSocket；公开 MCP 的访问控制需由部署层提供。

```powershell
uv run pytest
```
