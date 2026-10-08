"""English client-facing metadata for the ten Chat2Local MCP tools.

This module describes the existing behavior. No business logic runs here.
"""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations


TOOL_METADATA: dict[str, dict[str, Any]] = {'read': {'title': 'Read File or Directory',
          'description': 'Read a text file at a known path or list the immediate entries of one '
                         'directory inside the selected workspace. Directory listings are not '
                         'recursive; use search to locate unknown paths or matching content. File reads '
                         'are bounded by runtime-configured line and byte limits, not extra tool '
                         'arguments. Results report truncation and its reason; a line longer than the '
                         'entire byte budget yields only a prefix, and its remainder cannot be '
                         'retrieved by line-based continuation. Binary files return metadata only.\n'
                         '\n'
                         'Text is decoded strictly as UTF-8, BOM-marked UTF-8, or BOM-marked UTF-16; '
                         'invalid or unsupported encodings cause a Tool Error instead of replacement '
                         'text. Every path remains inside the selected workspace.',
          'params': {'path': 'File or directory path, relative to the selected workspace or an absolute '
                             'path within it.',
                     'start_line': 'First line to read, 1-based; defaults to line 1. Must be at least '
                                   '1.',
                     'end_line': 'Last line to read, 1-based and inclusive; defaults to EOF or the '
                                 'configured read cap. Must be at least 1 and not precede start_line.',
                     'workspace': 'Absolute workspace directory on the selected device. Omit to use its '
                                  "startup default; it must lie within that device's allowed roots and "
                                  'applies only to this call.',
                     'device': "Target device ID. Omit to use the MCP server's local device. Discover "
                               'remote agents with list_devices on a Hub; workspace paths and '
                               'permissions belong to the selected device.'},
          'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
 'search': {'title': 'Search Files',
            'description': 'Search file names (mode=name) or matching lines of text (mode=content) '
                           'within the selected workspace. Use read to obtain full content once the '
                           'file is known. Search timeout is configured by the device, not accepted as '
                           'a tool parameter. Results contain workspace-relative paths; reaching the '
                           'result-count cap, runtime timeout, or output-size budget yields an ordinary '
                           'truncated result rather than a Tool Error.\n'
                           '\n'
                           'Always exclude .git, node_modules, .venv, __pycache__, dist, build, and '
                           'coverage; honor .gitignore, .ignore, and .rgignore rules. Prefer ripgrep '
                           'when installed, otherwise use the Python fallback. Both backends apply the '
                           'same user include/exclude filters; absence of matches does not imply '
                           'excluded paths were searched.',
            'params': {'query': 'Nonempty literal search text, or a regular-expression pattern when '
                                "regex=true. In name mode, a query containing '/' matches the "
                                'workspace-relative path rather than only the basename.',
                       'mode': 'Required search mode: name for file names or content for matching text '
                               'lines.',
                       'path': 'Workspace-relative file or directory at which to start the search; '
                               "defaults to '.' (workspace root).",
                       'include': 'Optional glob patterns restricting candidate files; match basenames '
                                  'or workspace-relative paths.',
                       'exclude': 'Optional glob patterns excluding candidate files; built-in '
                                  'exclusions and ignore-file rules remain in effect.',
                       'regex': 'Whether query is interpreted as a regular expression; defaults to '
                                'false (literal matching).',
                       'case_sensitive': 'Whether matching is case-sensitive; defaults to false.',
                       'max_results': 'Positive maximum number of results. Omit to use the selected '
                                      "device's configured default. The effective hard cap is 500.",
                       'workspace': 'Absolute workspace path on the selected device; omit for its '
                                    'startup default. Must be inside its allowed roots; applies only to '
                                    'this call.',
                       'device': "Target device ID. Omit for the MCP server's local device; use "
                                 'list_devices on a Hub to discover remote agents.'},
            'annotations': {'readOnlyHint': True, 'destructiveHint': False, 'openWorldHint': False}},
 'apply_patch': {'title': 'Apply File Patch',
                 'description': 'Apply a strict Codex-style patch to one workspace on one device. '
                                'Support Add File, Update File, Delete File, Move Directory, and an '
                                'Update File followed by Move to (with or without content hunks). '
                                'Update hunks require exact, unambiguous context; fuzzy matching, '
                                'directory deletion, and copying are not supported. Source and '
                                'destination paths cannot overlap, and add/move destinations must not '
                                'already exist.\n'
                                '\n'
                                'Prepare and validate the entire patch before writing: preparation '
                                'failure is a Tool Error with no writes. Commit changes sequentially; a '
                                'commit-time failure may return success=false with completed operations '
                                'and partial changes, without rollback. Preserve existing UTF-8 or '
                                'BOM-marked UTF-16 encodings and newline styles when updating; newly '
                                'added files use BOM-free UTF-8 and LF. Side effects may survive a '
                                'failed or timed-out remote request: inspect state before retrying and '
                                'never replay blindly.',
                 'params': {'patch': 'Complete strict patch text between *** Begin Patch and *** End '
                                     'Patch. Use *** Add File: (+ lines), *** Update File: (@@ '
                                     'exact-context hunks with context, - old lines and + new lines), '
                                     '*** Delete File:, or *** Move Directory: followed by *** Move '
                                     'to:. Update File may also include *** Move to: with or without '
                                     'hunks. Not an arbitrary unified diff.',
                            'workspace': 'Absolute workspace on the selected device; omit for its '
                                         'startup default. Every patch source and destination must '
                                         'remain within this workspace and its allowed roots.',
                            'device': "Target device ID; omit for the MCP server's local device. Use "
                                      'list_devices when the intended device is unclear.'},
                 'annotations': {'readOnlyHint': False,
                                 'destructiveHint': True,
                                 'idempotentHint': False,
                                 'openWorldHint': False}},
 'exec_command': {'title': 'Execute Command',
                  'description': "Run a complete shell command using the selected device's configured "
                                 'shell, creating a Chat2Local-managed process. The command has the OS '
                                 "account's permissions and may affect files, processes, or networks; a "
                                 'workspace-bounded cwd is not an OS sandbox. A nonzero exit code and a '
                                 'foreground wait timeout are normal process results, not Tool Errors; '
                                 'timeout does not terminate the process.\n'
                                 '\n'
                                 'Use interact_process for later output or stdin and kill_process to '
                                 'terminate the process, always on the same device. An exited process '
                                 'may retain unread output until its retention period ends. The '
                                 'process_id never automatically routes across devices. Remote timeout '
                                 'or disconnection does not prove the command was canceled: inspect the '
                                 'actual state before retrying and never automatically replay it.',
                  'params': {'command': "Complete command string passed to the selected device's "
                                        'configured shell without splitting it into an argument vector.',
                             'cwd': 'Existing working directory within the selected workspace; defaults '
                                    "to '.'. This is not a sandbox for the command's OS side effects.",
                             'workspace': 'Absolute workspace on the selected device; omit for that '
                                          "device's startup default. cwd must remain inside it and its "
                                          'allowed roots; selection affects this call only.',
                             'device': "Device on which to run the command. Omit for the MCP server's "
                                       'local device; reuse this same device for subsequent '
                                       'managed-process calls.'},
                  'annotations': {'readOnlyHint': False,
                                  'destructiveHint': True,
                                  'idempotentHint': False,
                                  'openWorldHint': True}},
 'interact_process': {'title': 'Interact with Process',
                      'description': 'Read incremental stdout/stderr from a Chat2Local-managed process '
                                     'created by exec_command on the same device. Optionally write the '
                                     'exact UTF-8 input to stdin, without automatically appending a '
                                     'newline. After writing, wait up to 250 ms for output; with input '
                                     'omitted, read currently retained output immediately. Reading '
                                     'advances each output cursor; the two streams share the response '
                                     'output budget, and bounded buffers may drop older data.\n'
                                     '\n'
                                     'Cannot attach to arbitrary OS PIDs. Exited or terminated process '
                                     'records remain readable during their retention period. Unknown '
                                     'process IDs and writes to closed stdin are Tool Errors. A false '
                                     'has_more flag does not guarantee no future output while pipes may '
                                     'still drain. Sending input may have side effects; after remote '
                                     'timeout/disconnection, inspect state and never blindly resend it.',
                      'params': {'process_id': 'Chat2Local-managed process ID returned by exec_command '
                                               'on the same device; not an OS PID.',
                                 'input': 'Optional exact UTF-8 text to send to stdin before reading '
                                          "output. No newline is added; explicitly include '\\n' if "
                                          'needed. Wait up to 250 ms after sending; omit to read '
                                          'immediately.',
                                 'device': 'Same device used to create the managed process with '
                                           "exec_command. Omission selects the MCP server's local "
                                           'device; process_id does not infer its owner.'},
                      'annotations': {'readOnlyHint': False,
                                      'destructiveHint': True,
                                      'idempotentHint': False,
                                      'openWorldHint': True}},
 'kill_process': {'title': 'Terminate Process',
                  'description': 'Terminate a Chat2Local-managed process tree on the device where it '
                                 'was created; arbitrary OS PIDs are not supported. On Windows, '
                                 'force-terminate the process tree directly. On Linux/macOS, attempt '
                                 'graceful termination, then force after the configured grace period. '
                                 'Return outcome=terminated or outcome=already_exited and the common '
                                 'process output fields. The call consumes one page of retained output; '
                                 'further unread output may be retrieved using interact_process during '
                                 'retention.\n'
                                 '\n'
                                 'An already-exited process is a normal outcome, whereas '
                                 'unknown_process is a Tool Error. A remote timeout or disconnection '
                                 'does not establish that termination failed; check the target state '
                                 'before trying again, and do not automatically replay the operation.',
                  'params': {'process_id': 'Device-local Chat2Local-managed process ID obtained from '
                                           'exec_command, not an arbitrary OS PID.',
                             'device': 'Device that created this managed process, matching '
                                       "exec_command. Omitting device selects the MCP server's own "
                                       'device; process_id does not route automatically.'},
                  'annotations': {'readOnlyHint': False,
                                  'destructiveHint': True,
                                  'idempotentHint': False,
                                  'openWorldHint': False}},
 'handoff_list': {'title': 'List Handoffs',
                  'description': 'List the latest metadata of resumable Handoff workstreams belonging '
                                 'to the selected project workspace on the selected device. Use title '
                                 'and summary to identify a matching workstream before reading its full '
                                 'body with handoff_get. Records are sorted and do not include content. '
                                 'If storage has not been created, return an empty list without '
                                 'creating it; malformed records raise invalid_handoff rather than '
                                 'being silently skipped.\n'
                                 '\n'
                                 'Handoffs are grouped by workspace but physically stored under the '
                                 "selected device's ~/.chat2local/handoffs/<workspace-key>/ user-data "
                                 'directory, not in the source workspace; separate devices do not '
                                 'automatically synchronize.',
                  'params': {'workspace': 'Absolute project workspace on the selected device; omit for '
                                          'its startup default. Must be within allowed roots. It '
                                          'determines Handoff scope, not the physical storage path.',
                             'device': "Device holding these Handoffs; omit to use the MCP server's "
                                       'local device. Handoffs on separate devices are not '
                                       'automatically synchronized.'},
                  'annotations': {'readOnlyHint': True,
                                  'destructiveHint': False,
                                  'openWorldHint': False}},
 'handoff_get': {'title': 'Read Handoff',
                 'description': 'Read the latest metadata and continuation body of a known Handoff '
                                "workstream in the selected device's workspace scope; use handoff_list "
                                'to locate workstreams first. The returned content excludes the '
                                'server-managed file header. Missing records raise handoff_not_found '
                                'and invalid records raise invalid_handoff instead of returning '
                                'invented or empty records.',
                 'params': {'workstream': 'Stable workstream slug, not a file path: 1–80 ASCII letters, '
                                          'digits, hyphens, or underscores, starting with an ASCII '
                                          'letter or digit.',
                            'workspace': 'Absolute project workspace on the selected device; omit for '
                                         'its startup default and stay inside allowed roots. This '
                                         'selects Handoff scope, not its physical storage path.',
                            'device': 'Device on which this Handoff is stored; omit for the MCP '
                                      "server's local device. Remote Handoffs live on their respective "
                                      'agents.'},
                 'annotations': {'readOnlyHint': True,
                                 'destructiveHint': False,
                                 'openWorldHint': False}},
 'handoff_save': {'title': 'Save Handoff',
                  'description': 'Create or atomically replace the latest cross-chat continuation '
                                 'checkpoint for one workstream. Handoffs are not chat transcripts or '
                                 'routine progress logs: save when switching chats, pausing work for '
                                 'later, explicitly requested by the user, or crossing a meaningful '
                                 'phase boundary; defer if unsure. The calling model supplies the '
                                 'title, summary, and Markdown body; Chat2Local only validates and '
                                 'persists them, never generates their meaning.\n'
                                 '\n'
                                 'Use expected_revision=0 when creating or the latest revision from '
                                 'handoff_get when updating. A revision_conflict writes nothing: reread '
                                 'the latest record, reconcile its meaning, and save with the new '
                                 'revision; there is no automatic merge or version history. Title, '
                                 'summary, and body are updated atomically; body newlines are '
                                 'normalized to LF while title/summary text is preserved. Concurrent '
                                 'calls within one device runtime cannot silently overwrite, but '
                                 'cross-process locking is not guaranteed. A timeout or disconnect may '
                                 'follow a completed write: verify actual state before retrying; do not '
                                 'replay blindly.',
                  'params': {'workstream': 'Stable workstream slug, not a file path: 1–80 ASCII '
                                           'letters, digits, hyphens, or underscores, starting with an '
                                           'ASCII letter or digit.',
                             'title': 'Required strict Unicode string, 1–120 characters, single line '
                                      'and nonblank. Supplied by the caller and stored unchanged.',
                             'summary': 'Required strict Unicode string, 1–500 characters, single line '
                                        'and nonblank. Supplied by the caller and stored unchanged.',
                             'content': 'Required Markdown continuation body as a string, excluding '
                                        'server-managed front matter. Line endings are normalized to '
                                        'LF; keep only information useful for future resumption.',
                             'expected_revision': 'Required strict integer >= 0. Use 0 for new '
                                                  'workstreams or the latest revision from handoff_get '
                                                  'for updates; a mismatch raises revision_conflict '
                                                  'without writing.',
                             'workspace': 'Absolute project workspace on the selected device, within '
                                          'its allowed roots. Omit for its startup default. Handoffs '
                                          "are stored in device user data under this workspace's scope.",
                             'device': 'Device on which to save this Handoff; omit to use the MCP '
                                       "server's local device. No automatic cross-device "
                                       'synchronization.'},
                  'annotations': {'readOnlyHint': False,
                                  'destructiveHint': True,
                                  'idempotentHint': False,
                                  'openWorldHint': False}},
 'list_devices': {'title': 'List Devices',
                  'description': "List the MCP server's local device and currently connected remote "
                                 'agents to discover device IDs, device roles, and supported tool '
                                 'names. Disconnected agents are not listed until they reconnect. '
                                 'Device filesystem permissions and allowed workspace roots are not '
                                 "exposed. The local entry means the server's own device, not "
                                 "necessarily the user's current computer.",
                  'params': {},
                  'annotations': {'readOnlyHint': True,
                                  'destructiveHint': False,
                                  'openWorldHint': False}}}


def annotations_for(name: str) -> ToolAnnotations:
    """Advisory MCP behavior hints, not authorization checks."""
    return ToolAnnotations(**TOOL_METADATA[name]["annotations"])
