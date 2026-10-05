## 项目原则

Chat2Local 是轻量本地 MCP Agent，设计文档位于 `docs/`。

## Tool 约束

公共 Tool surface 固定为：
`read`、`search`、`list_devices`、`apply_patch`、`exec_command`、
`interact_process`、`kill_process`、`handoff_list`、`handoff_get`、`handoff_save`。
除非任务明确要求重新设计，不新增、删除、拆分或合并 Tool。

Tool 保持薄层，复杂生命周期和底层机制放在对应 Runtime / Service。
通用 MCP 调用及 Handoff 使用规范维护在 MCP instructions / Tool 描述中。

## Workspace 安全

- 普通文件路径和 process `cwd` 必须经过 `WorkspaceManager`。
- workspace 必须位于本机 configured allowed_roots 内，文件 Tool path 必须位于当前 workspace 内。
- 阻止 `..`、workspace 外绝对路径和 symlink / junction 逃逸，Filesystem Tool 保持 workspace-bounded。
- `cwd` 限制不构成 OS sandbox；`exec_command` 仍具有当前 OS 用户权限。

## 状态与持久化

- 避免持久化可从真实系统重新获得的业务状态；Process 生命周期状态默认只保存在内存中。
- Process Runtime、Handoff 和 Chat 会话状态保持独立。
- Handoff scope = workspace，storage = Chat2Local user-data directory；复用 config 用户数据根目录，
  各 workspace 独立存储。内部存储不要求位于 workspace 内，仍须防止 symlink / junction 别名。
- Handoff 只保存最新跨 Chat 交接状态，不扩展为 Task Manager、会话记录器或聊天历史。
  Chat2Local 不包含 LLM，只做校验与持久化。

## 实现原则

- Python 3.12+，优先标准库；路径使用 `pathlib.Path`，Process 代码使用 `asyncio`，测试使用 `pytest`。
- 修改前阅读现有实现和测试，优先扩展现有模块，不重复职责或增加不必要的抽象层。
- 代码保持简洁、整洁，并考虑整体架构。
- 提交包含多个功能时，在 commit description 中简要列出。
