## 项目原则

Chat2Local 是一个轻量使用的本地 MCP Agent。

相关设计文档放在docs目录下。

## Tool 约束

当前公共 Tool surface 已确定为：

- `read`
- `search`
- `list_devices`
- `apply_patch`
- `exec_command`
- `interact_process`
- `kill_process`
- `handoff_list`
- `handoff_get`
- `handoff_save`

除非任务明确要求重新设计，否则不要擅自新增、删除、拆分或合并这些 Tool。

Tool 应保持薄层，复杂生命周期和底层机制放到对应 Runtime / Service 中。

## Workspace 安全

所有文件路径和 process `cwd` 必须经过 `WorkspaceManager`。

Workspace 本身必须位于本地 configured allowed_roots 中；Tool path 又必须位于当前 workspace 中。

必须阻止：

- `..` 越界
- workspace 外绝对路径
- symlink / junction 逃逸

Filesystem Tool 必须保持 workspace-bounded。

注意：

`cwd` 受 workspace 限制，不代表 Shell 是 OS sandbox。
`exec_command` 仍具有当前 OS 用户权限。

## 状态与持久化

优先避免保存可以从真实系统重新获得的业务状态。

不要把 Process Runtime、Handoff / Resume、Chat 会话状态混在一起。

Handoff 只保存按 workstream 划分的最新跨 Chat 交接状态，长期项目约定继续放在 `AGENTS.md`；
不要把它扩展成 Task Manager、Session/Conversation Recorder 或完整聊天历史。

Handoff 的 `workstream` 是稳定机器 ID / 文件名，`title` 是可读工作线名称，
`summary` 是简短当前状态，`content` 是完整恢复上下文。
title / summary 由调用模型生成并显式传入；Chat2Local 本身没有 LLM，只做严格校验和持久化。
新 Chat 先通过 `handoff_list` 的 title / summary 发现并理解已有工作线，再选择完整 Handoff。
旧格式文件可读取（title / summary 为 null），下一次正式 save 才升级格式，不批量迁移或自动猜测。

Process 生命周期状态默认只保存在内存中，除非任务明确要求持久化。

## 实现原则

- Python 3.12+
- 优先标准库
- 路径使用 `pathlib.Path`
- Process 相关代码使用 `asyncio`
- 测试使用 `pytest`
- 修改前先阅读现有实现和测试
- 优先扩展现有模块，不重复实现相同职责
- 不为了“架构完整”增加不必要的抽象层

## DO

代码实现需符合简洁，整洁的代码规范， 需要考虑整体架构。

git 提交时如果有多个功能，需要在提交时description简单列出。
