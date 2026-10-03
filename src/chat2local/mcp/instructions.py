"""Server-wide guidance advertised during MCP initialization."""

MCP_INSTRUCTIONS = """
Before project-scoped work, read AGENTS.md from the target workspace when present
and follow it as project guidance. For work that may continue across chats,
manage Handoffs automatically: call handoff_list first, reuse a matching
workstream by title/summary or create one when needed, and update it when
switching chats, pausing, or reaching a meaningful milestone. Do not require the
user to remember or manage workstream slugs. On revision_conflict, get the latest
Handoff, merge its meaning, then save with the latest revision; never blindly
overwrite or replay a save.
""".strip()
