# Chat2Local pywebview GUI · Stage 2

Local management shell for the existing Core. No business mock data and no automatic Core start.
The GUI closes independently; a started Core continues running.

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
Close the window when finished; stop Vite separately with Ctrl+C.

## Pages

The GUI defaults to Simplified Chinese. Settings > Display language switches between
Chinese and English immediately, without saving Core configuration. The selection is
stored in the GUI's WebView profile at `~/.chat2local/gui/webview/` and survives GUI restarts.
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

`python/bridge.py` only forwards requests and hosts the native folder picker.
Framework-independent clients live in `src/chat2local/management/` and reuse Core config,
WorkspaceManager and runtime IPC. Core is the sole log writer; GUI reads the log and
does not configure Core logging or create a GUI log file. No additional server.
Display language uses browser local storage in the separate GUI profile; it is not a Core config field.

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
