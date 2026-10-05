"""Server-wide guidance advertised during MCP initialization."""

MCP_INSTRUCTIONS = """
Before project-scoped work, read AGENTS.md from the target workspace when present
and follow it as project guidance. Keep long-term project conventions there.

Manage Handoffs automatically for work that may continue across chats. To resume
project context or begin sustained independent work, call handoff_list first,
use title/summary to find a matching workstream, and handoff_get to read its full
context. Reuse that workstream or create one when needed; do not require the user
to remember or manage workstream slugs. A workstream represents independently
resumable work, not a conversation; one chat may involve several workstreams and
several chats may continue the same one. Keep slugs stable; prefer short,
descriptive lowercase kebab-case names for new workstreams. Do not create a
Handoff for a transient question or an immediately resolved side issue. Split
off a side issue only when it needs independent continuation later.

Treat Handoffs as cross-chat checkpoints, not progress logs: do not save after
routine tool calls, investigation findings, code changes, or each completed
subtask while the same chat is actively continuing. Update when switching chats, pausing for later,
the user explicitly asks to save, or a major phase boundary materially changes
what a future chat must know to resume. If unsure whether a milestone is
significant enough, defer saving.

The calling model supplies a readable title, a brief current-state summary and
the full continuation context as content. Chat2Local only validates and persists
them; it has no LLM. Save only what a future chat needs: current work, recent
results, key decisions and reasons, code/test/deployment status, unresolved
issues, concrete next steps and essential file/command/reference pointers.
Do not copy full conversations, tool logs, stdout/stderr, entire diffs or large
amounts of information readily recoverable from files or Git.

Use the revision just read by handoff_get when updating. On revision_conflict,
get the latest Handoff, merge its meaning, then save with the latest revision;
never blindly overwrite or replay a save. When a workstream is complete, save
its completion status and final conclusions once. Completed Handoffs are
retained; do not delete them automatically.
""".strip()
