# Chat2Local pywebview desktop GUI

Local management shell for the existing Core. Core auto-start is optional and off by default.
On Windows, the tray exists throughout the GUI session. Closing the window hides it to the
tray by default; Settings > Desktop can change X to exit. Minimize still uses the taskbar.
Tray > Exit Chat2Local always exits the GUI. A started Core continues running.

## Install and launch

Python 3.12+, Node.js 22.12+, pnpm 10 and Windows WebView2 Runtime:

```powershell
# Repository root
uv sync --group pywebview-gui
cd gui/pywebview/frontend
pnpm install
pnpm build
cd ../../..
uv run --group pywebview-gui python gui/pywebview/python/app.py
```

The build loads directly from a file URI with relative assets; no extra HTTP server.
For development, run `pnpm dev` in frontend, then in another terminal from the root:

```powershell
uv run --group pywebview-gui python gui/pywebview/python/app.py --dev-url http://127.0.0.1:5173
```

Only loopback dev URLs are accepted. An ordinary browser cannot access the Python bridge.
Use Tray > Exit Chat2Local when finished; stop Vite separately with Ctrl+C.

## Pages

The GUI defaults to Simplified Chinese. Settings > Display language switches between
Chinese and English immediately, without saving Core configuration. Language is persisted
in `~/.chat2local/gui/preferences.json`, so the Python Tray and React UI share the same choice.
The previous WebView localStorage value is migrated once and then kept only as a boot cache.
Device IDs, workspace paths and original log entries are not translated.

- **Overview** discovers `~/.chat2local/runtime.json`, verifies control ping and instance identity,
  and displays lifecycle plus Core state. Choose Standalone/Hub/Agent and a startup workspace
  (native Browse or typed path), then Start. Stop uses local control IPC; Restart stops before
  starting the same mode/workspace. No PID killing. Running mode/workspace cannot be edited.
- **Settings** edits the real `~/.chat2local/config.yaml`. The current mode shows Agent,
  Hub plus Auth, or no role section for Standalone. Security, Process, Read and Search
  remain available under Advanced (collapsed by default). Running Core determines the mode;
  otherwise the next startup selection does. Hidden sections retain their persisted values.
  Unset values show defaults. Reset removes an explicit field; Set null
  preserves an explicit null. Save validates through Core and writes only explicit fields.
  Saving while Core runs requires a manual Restart. Token fields contain paths only;
  a custom proxy URL stays hidden. YAML comments are normalized by the Core saver.
  The separate **Desktop** section saves immediately to `~/.chat2local/gui/preferences.json`.
  Windows login startup, silent login startup, Core auto-start and the close button behavior
  are independent of the Core config candidate and its Save button.
- **Logs** reads the existing `~/.chat2local/logs/chat2local.log`, initially tails up to 256 KiB /
  1000 lines, then reads by byte cursor. Follow polls every second; Pause stops reads.
  Search and level filters are local. Rotation/truncation reset the cursor; no file is altered.
  The viewer fills the remaining page height and is the only scrolling area on this page.
- **Devices** polls the existing local control IPC every two seconds while the page is active.
  It is available only for a Running Hub and shows the local Hub plus connected Agents.
  When the Hub stops or the mode changes, an open Devices page returns to Overview.
  Disconnected remote devices are not retained as history. No public MCP call is made.

The document and app shell do not scroll. Overview, Devices and Settings scroll inside
the page content area when needed; Logs scrolls only inside its viewer, including after resize.

Start uses the current Python interpreter and persisted configuration; Hub/Agent must have
valid role settings and authentication configured. Startup/stop timeouts are reported rather
than force-killing Core. A timed-out launch may still complete; status remains discoverable.

## Structure and checks

`python/app.py` composes the desktop lifecycle; `bridge.py` forwards management requests,
hosts the native folder picker and exposes the three small desktop preference/navigation APIs.
`preferences.py` validates and atomically saves desktop preferences; `autostart.py` owns only
the current-user `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\Chat2Local` entry.
It quotes the source command with `subprocess.list2cmdline`:
`<pythonw.exe> <absolute gui/pywebview/python/app.py> --startup` on Windows when the
current interpreter has a sibling `pythonw.exe`. Missing pythonw falls back to the
current interpreter with a diagnostic that a console may appear. The frozen branch uses
`<executable> --startup`. Each primary startup reconciles this entry with the persisted
desktop preference: enabled creates/updates an outdated command, disabled removes only
Chat2Local's value, and already matching state is a no-op. This migrates older python.exe
commands to pythonw and future source commands to a frozen executable automatically.
Reconcile failure reports a desktop error and keeps the GUI running and preferences intact.
If the preferences file exists but is invalid or unreadable, the GUI may use safe defaults for
the current session but skips Registry reconciliation so fallback values cannot delete or rewrite
the user's existing login-startup entry.
Changing the setting still updates Registry then saves preferences, rolling back Registry
if saving fails; startup reconcile never rewrites preferences.
No admin rights, services or scheduled tasks are involved. Non-Windows login startup is unsupported.

`tray.py` owns a Windows pystray loop and a desktop worker, with one bounded management
operation at a time on a separate executor. Open/Logs/Exit stay responsive during Core work.
We use owned `run()` rather than `run_detached()` to supervise failures and join the loop.
A small pystray 0.19.5 setup override avoids its stranded setup helper on early native failure.
the GUI dependency is pinned to `pystray==0.19.5` because these private APIs are version-specific.
The short visibility setup runs on the owned loop. A scoped native-result check also catches
the failed icon-add BOOL that pystray otherwise ignores. There are no mouse-message hooks. The default
Open action uses pystray's Windows left-button activation. `show/restore/show` restores and
activates the existing window; pywebview 6.2.1's WinForms `show()` performs activation itself.
Open Logs uses a single pending navigation value consumed by the frontend runtime poll.
Desktop shutdown stops the tray and joins the workers; it never stops Core.

Only `--startup` plus `silent_login_start=true` plus a ready tray creates a hidden window.
The Python shell sets `hidden` before native display, without waiting for React. If the tray
fails, the window is visible and X safely exits; later tray failures restore the window too.
Manual launch always shows the window. Core auto-start checks real management status once:
only Stopped with a confirmed context starts. Running and transitional/error states are left
alone. Core management revalidates workspace access and guards concurrent starts. Only a
successful Start or discovered Running Core updates the saved mode/workspace, never UI inputs.

Framework-independent clients live in `src/chat2local/management/` and reuse Core config,
WorkspaceManager and runtime IPC. GUI never configures Core logging. Desktop diagnostics,
including corrupt preference fallback and tray errors, use the separate rotating
`~/.chat2local/gui/desktop.log`; the Logs page continues to show the existing Core log.
Unknown exceptions persist only a fixed diagnostic, exception type and traceback
file/function/line locations, never exception values, source lines, locals or chained values.
Desktop action errors are also exposed in the GUI.

`single_instance.py` enforces one Windows desktop per interactive session using
`Local\Chat2Local.Desktop`, independently of Core locking. Ownership is checked before
opening the persistent desktop log or creating the desktop, tray or WebView. Ownership
diagnostics use safe stderr only (or no handler output when no console exists), without
a bootstrap log file. A manual duplicate requests activation of the
existing window and exits; a `--startup` duplicate exits silently without activation.
The primary publishes an atomic `gui/instance-<session_id>.json` descriptor for a tiny
127.0.0.1 TCP endpoint authenticated by a fresh nonce. It only accepts `activate`, which
signals the existing desktop worker to show/restore/activate the existing window. An exiting
desktop rejects activation without setting the event or acknowledging success.
Manual duplicates retry readiness for up to three seconds and never bypass an existing
owner. Shutdown closes the socket, joins its bounded listener, retires its descriptor
and releases the mutex handle after closing the persistent log handler, keeping a single
desktop.log writer even while ownership changes. After a crash, mutex abandonment permits a new primary
to replace any stale descriptor; descriptor contents do not establish ownership.
Display language is a Desktop Preference, not a Core config field. The Tray uses the same language
for its menu and lifecycle labels. Tray graphics come from `gui/pywebview/assets/`; the multi-size
`chat2local.ico` is reserved for Windows packaging while the Tray loads `chat2local_64.png`.

Desktop preference defaults (UTF-8 JSON, atomic replacement; invalid files fall back with a log):

```json
{
  "schema_version": 1,
  "language": "zh-CN",
  "launch_at_login": false,
  "silent_login_start": false,
  "close_behavior": "tray",
  "auto_start_core": false,
  "startup_mode": null,
  "startup_workspace": null
}
```

```powershell
# frontend
pnpm build
pnpm lint
# repository root
uv run --group pywebview-gui pytest
uv lock --check
```

Tests use temporary configuration/runtime/log paths. Do not smoke-test Stop/Restart against
an existing production Core; use an isolated test process instead.
