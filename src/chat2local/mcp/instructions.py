"""Server-wide guidance advertised during MCP initialization."""

MCP_INSTRUCTIONS = """
Before project-scoped work, read AGENTS.md from the target workspace when present
and follow it as project guidance. For work that may continue across chats,
manage Handoffs automatically: call handoff_list first, reuse a matching
workstream by title/summary or create one when needed. Treat Handoffs as
cross-chat checkpoints, not progress logs: do not save after routine tool calls,
investigation findings, code changes, or each completed subtask while the same
chat is actively continuing. Update when switching chats, pausing for later,
the user explicitly asks to save, or a major phase boundary materially changes
what a future chat must know to resume. If unsure whether a milestone is
significant enough, defer saving. Do not require the user to remember or manage
workstream slugs. On revision_conflict, get the latest Handoff, merge its
meaning, then save with the latest revision; never blindly overwrite or replay
a save.
""".strip()
