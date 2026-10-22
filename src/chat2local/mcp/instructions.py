"""Server-wide English guidance advertised during MCP initialization."""

MCP_INSTRUCTIONS = """
Device and workspace selection

Omitting device targets the MCP server's local device, which may be a Hub rather than the user's intended endpoint. Use the device ID explicitly when the user names a device; call list_devices if IDs or availability are unknown. Do not guess among multiple possible targets for consequential actions such as writing files, executing commands, or terminating processes.

workspace is an absolute directory on the selected device, within that device's configured allowed roots. It selects the workspace for this call only. File paths and the command's working directory are workspace-bounded, but shell commands still run with the OS account's permissions; the workspace is not an OS sandbox.

Tool coordination and recovery

Use read for a known file or directory path and search to locate file names or matching content. exec_command starts a device-local managed process; use interact_process to read later output or send input and kill_process to stop it. Always reuse the same device as exec_command for a given process_id; process IDs cannot infer or change device routing.

A timed-out or disconnected remote call with side effects may already have executed on the target. Check the actual file, process, or Handoff state before attempting another operation; do not blindly replay it.

Optional capabilities

Use capability_discover on the intended device to obtain optional capability names, input JSON Schemas and current availability, then capability_execute with the same device. A known capability with available=false is temporarily unavailable; an unmatched query returns an empty list, and executing an unknown name returns unknown_capability. Browser V1 supports only browser.tabs.create with an absolute HTTP(S) URL. Browser actions affect Chrome outside the filesystem workspace. After a timeout or disconnect, verify actual tabs before retrying; an action may already have completed.

Project guidance

Before project-scoped work, read AGENTS.md from the target workspace when present and follow its project guidance. Keep durable, long-term project conventions there.

Handoff lifecycle

Use scope="workspace" (the default) when work or an idea belongs to a specific project; prefer that project's workspace. Use scope="personal" for research topics, plans, or early ideas without a concrete project, and omit workspace. Personal scope does not use the default workspace. Keep the same scope and device when listing, reading, and updating a workstream; identical slugs in different scopes identify independent records.

Manage Handoffs for work or ideas worth preserving across chats, including ongoing development, research topics, plans, and early-stage ideas. When resuming project context or a saved topic, call handoff_list first, use titles and summaries to find an existing workstream, then handoff_get for the full context. Prefer reusing relevant workstreams; create new ones only when needed, and do not require the user to remember or manage slugs.

A workstream represents independently resumable work or a subject worth revisiting, not a chat session. One chat may contain several workstreams, and several chats may continue the same one. Keep slugs stable; prefer short, descriptive, lowercase kebab-case slugs for new workstreams. Do not create a Handoff for a transient question or an immediately resolved side issue unless the user explicitly asks to preserve it. Split off a side issue only when it needs independent continuation later.

Treat Handoffs as cross-chat checkpoints, not progress logs. Do not save after routine tool calls, investigation findings, code changes, or each completed subtask while the same chat is actively continuing. Save when switching chats, pausing for later, explicitly requested by the user, or at a major phase boundary that materially changes what a future chat must know to resume. If uncertain whether a milestone warrants saving, defer.

The calling model must supply a readable title, concise current-state summary, and continuation body; Chat2Local only validates and persists these, without an LLM. Save just the context a future chat needs: current work, recent results, key decisions and reasons, code/test/deployment status, unresolved issues, concrete next steps, and essential file/command/reference pointers when relevant. For early-stage ideas, a short summary and a few notes are enough; do not force development-specific sections or a minimum body length. Do not copy entire conversations, tool logs, stdout/stderr, full diffs, or large amounts of information readily recoverable from files or Git.

Use the revision obtained from handoff_get when updating. On revision_conflict, read the latest Handoff, reconcile its meaning, then save with the new revision; never overwrite or replay blindly. When a workstream is complete, save its completion status and final conclusions once. Completed Handoffs are retained and are not automatically deleted.

Both workspace and personal Handoffs are physically stored in the selected device's Chat2Local user-data directory, not in source workspace files. Workspace records are isolated by project; personal records are independent of any workspace. They are not automatically synchronized across devices.
""".strip()
