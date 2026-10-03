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

多设备：在 Hub 和每台 Agent 上设置同一个 `CHAT2LOCAL_HUB_TOKEN`，也可以用 `--token` 覆盖。
Standalone 无需 token 或 Hub。

```powershell
$env:CHAT2LOCAL_HUB_TOKEN = '<your shared secret>'
uv run chat2local hub --device-id hub --host 0.0.0.0 --port 8765
uv run chat2local agent --device-id desktop --hub-url wss://hub.example.com/device/ws
```

Agent 主动连接，不暴露 MCP。ChatGPT 只连接 Hub MCP：`/mcp`；健康检查为 `/health`。
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

workstream 必须匹配 `^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$`，固定保存到目标设备所选项目的
`HANDOFFS/<workstream>.md`。workspace 省略使用目标设备默认目录，绝对路径 override 仍受其 allowed_roots 限制。
不允许任意文件路径或 symlink/junction 别名；远程 Handoff 实际保存在 Agent，Hub 无中央存储。
list 返回排序后的 workstream/title/summary/revision/updated_at（不含正文），让新 Chat 先发现并理解已有工作线，
再选择读取完整 Handoff；目录不存在时返回空列表且不创建目录。get 返回同样 metadata 加正文；save 返回完整 metadata。
缺少文件报 `handoff_not_found`，损坏 metadata 报 `invalid_handoff`，非法 slug 报 `invalid_workstream`。

创建时 `expected_revision=0`，成功得到 revision 1。更新先 get，save 提供读取到的 revision；
成功递增 1。冲突报 `revision_conflict` 且不写入，由模型重新 get、阅读和合并正文后再保存。
每个 Dispatcher 共用一个 Store；同一 workstream 的 revision 读取/校验与 temp + replace 全在同一把锁内，
两个使用相同 revision 的并发更新只有一个成功。取消中的后台写入完成前不会释放锁，Agent 重连保留同一个 Store。

文件为 UTF-8、LF；固定 header 包含 revision、UTC updated_at、title 和 summary，无需 YAML parser。
title / summary / content 作为一个状态在同一 revision 下原子保存，content 参数/返回值只含正文。
旧格式（只有 revision/updated_at）可正常 list/get，返回 title=null、summary=null 且不改文件；
下一次正常 save 必须提供有效 title/summary，匹配旧 revision 后自然升级格式。
只有 title 或只有 summary 的半升级 header 以及不合法 metadata 都报 invalid_handoff，不自动修复或批量迁移。
正文自由格式，不自动总结、截断或追加日志；正文换行统一为 LF。写入失败保留原文件并尽力清理 temp。
只保存最新状态，不自动保存每轮聊天、不建立 Task/Session 系统或 revision 历史；V0.1 不提供跨进程/外部编辑器
事务锁或 semantic merge。超时/断线后先 get 实际状态再决定下一步。
Stage 4 正式只使用 `HANDOFFS/*.md`；不会创建、迁移或依赖 legacy 根目录 `HANDOFF.md`。

MCP Server 在 initialize 阶段广播一份精简的 server-wide instructions：进行项目工作前，
模型应先读取目标 workspace 的 `AGENTS.md`（存在时）并将其作为项目指导；需要跨 Chat 延续的工作
由模型主动通过 Handoff 管理，用户不需要记忆或维护 workstream slug。具体项目规则仍保存在各项目自己的
`AGENTS.md`，不会把某个项目全文硬编码进全局 MCP instructions。

公网 Agent 必须使用 WSS；内网可信网络可用 WS。Hub/Agent 通信和 MCP 的 Tunnel / HTTPS 暴露方式相互独立。
共享 token 只保护设备 WebSocket；公开 MCP 的访问控制需由部署层提供。

```powershell
uv run pytest
```
