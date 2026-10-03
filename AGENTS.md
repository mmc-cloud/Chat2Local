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

### Handoff 使用规范

Handoff 的生命周期默认由调用模型管理，用户不需要手动创建、命名、记忆或维护 workstream。

- 新 Chat 在需要恢复项目上下文时，先调用 `handoff_list`，根据 title / summary 判断相关工作线；
  需要继续某条工作线时再调用 `handoff_get` 读取完整正文。不要要求用户记住或提供 slug。
- 开始一项明显会持续、多步骤、可能跨 Chat 继续的独立工作时，模型应先 `handoff_list`
  检查是否已有对应 workstream；已有则复用，没有则自行创建合适的 workstream / title / summary。
- workstream 表示“可独立续接的一条工作线”，不是 Conversation。一次 Chat 可以涉及多条 workstream，
  多个 Chat 也可以分别推进不同 workstream。
- 临时问题、一次性旁支、已经当场解决且不需要未来独立续接的内容，不创建 Handoff。
  只有当旁支本身需要以后单独恢复继续时，才拆成新的 workstream。
- workstream 名称应保持稳定；新建时优先使用简短、明确的 lowercase kebab-case slug。
  title 面向人和模型阅读，summary 用一句简短文字说明当前做到哪里。
- Handoff 不要求每轮聊天或每次代码修改都保存。通常在以下时机更新：
  1. 用户准备切换到新 Chat；
  2. 当前工作暂停、明确以后继续；
  3. 用户明确要求保存 / 更新 Handoff；
  4. 工作达到重要阶段边界，当前状态、关键决定或下一步已经发生实质变化，且需要为后续续接保留。
- 保存正文时只保留“无历史对话也能继续工作”所需的信息：当前工作、最近完成、关键决定及必要原因、
  当前代码 / 测试 / 部署状态、未解决问题、明确下一步和重要文件 / 命令 / 引用。
  不复制完整聊天历史、完整 Tool 日志、完整 stdout/stderr、整份 diff 或可以直接从文件 / Git 重新获得的大量事实。
- 更新已有 Handoff 时使用刚读取到的 revision。发生 `revision_conflict` 时必须重新
  `handoff_get` 最新版本，由模型合并语义后再保存；不得盲目覆盖或自动 replay。
- workstream 完成时，最后更新一次 Handoff，把完成状态和最终结论写清楚。完成的 Handoff 不自动删除；
  如以后积累过多，由用户或模型在确认不再需要后使用现有文件工具手动清理，不为此额外扩展 Handoff Tool。

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
