"""Real Chrome acceptance via the local MCP Hub/Agent fixture harness.

Run only after manually reloading the extension and granting the local fixture
website in extension options. Opens only temporary local fixture tabs, never
chooses an existing personal tab, and does not request browser permissions.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def check(report_path: Path | None):
    steps = []
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "device": "browser-smoke-agent", "steps": steps}
    async with streamable_http_client("http://127.0.0.1:18765/mcp/") as streams:
        async with ClientSession(streams[0], streams[1]) as client:
            await client.initialize()

            async def tool(name, arguments):
                reply = await client.call_tool(name, {"device": report["device"], **arguments})
                if reply.is_error:
                    raise RuntimeError("MCP tool failed")
                return json.loads(reply.content[0].text)

            async def execute(name, arguments):
                return await tool("capability_execute", {"capability": name, "arguments": arguments})

            def passed(label):
                steps.append(label)
                print("PASS " + label, flush=True)

            def success(result):
                if result.get("ok") is not True:
                    raise RuntimeError("Browser operation failed: " + result.get("error", {}).get("code", "unknown"))
                return result["result"]

            def error(result, expected):
                if result.get("ok") is not False or result.get("error", {}).get("code") != expected:
                    raise RuntimeError("Expected error: " + expected)

            discovery = success(await tool("capability_discover", {"query": "browser"}))
            if len(discovery["capabilities"]) != 6 or not all(item["available"] for item in discovery["capabilities"]):
                raise RuntimeError("Reload the Phase 2 extension first; all six capabilities must be available")
            passed("six independently registered capabilities are online")
            run_id = uuid4().hex[:10]

            async def create(path, host="127.0.0.1"):
                url = f"http://{host}:18766{path}?run={run_id}"
                created = success(await execute("browser.tabs.create", {"url": url}))
                report.setdefault("test_tab_ids", []).append(created["tab_id"])
                # tabs.create can resolve while Chrome still reports about:blank.
                # Wait through read-only lookup before inspecting the new page;
                # never replay creation or a page action to handle navigation.
                for _ in range(50):
                    tab = success(await execute("browser.tabs.resolve", {"tab": {"tab_id": created["tab_id"]}}))
                    if tab["url"] == url:
                        return created["tab_id"]
                    await asyncio.sleep(0.1)
                raise RuntimeError("The local fixture tab did not finish its initial URL transition")

            async def inspect(tab_id, target):
                # Inspections may be retried while an initial navigation loads;
                # clicks and fills are never replayed by this helper.
                for attempt in range(30):
                    result = await execute("browser.page.inspect", {"tab": {"tab_id": tab_id}, "target": target, "wait_ms": 1000})
                    if result.get("ok"):
                        return result["result"]
                    if result.get("error", {}).get("code") not in ("page_changed", "injection_failed", "target_not_found") or attempt == 29:
                        return success(result)
                    await asyncio.sleep(0.1)

            basic = await create("/")
            await inspect(basic, {"role": "textbox", "label": "Search query"})
            resolved = success(await execute("browser.tabs.resolve", {"tab": {"tab_id": basic}}))
            listed = success(await execute("browser.tabs.list", {"tab": {"url": resolved["url"]}}))
            if len(listed["tabs"]) != 1 or listed["tabs"][0]["tab_id"] != basic:
                raise RuntimeError("Native tab lookup failed")
            passed("list and resolve locate the explicit native Chrome tab")
            success(await execute("browser.page.fill", {"tab": {"tab_id": basic}, "target": {"label": "Search query"}, "value": "Phase2 MCP text"}))
            success(await execute("browser.page.click", {"tab": {"tab_id": basic}, "target": {"role": "button", "name": "Run search"}}))
            await inspect(basic, {"id": "result", "text": "Clicked: Phase2 MCP text"})
            passed("HTML input replacement and button click update actual page state")
            success(await execute("browser.page.fill", {"tab": {"tab_id": basic}, "target": {"label": "Notes"}, "value": "Phase2 textarea"}))
            await inspect(basic, {"id": "notes-result", "text": "Phase2 textarea"})
            passed("HTML textarea input event updates page state")
            ambiguous = await execute("browser.page.click", {"tab": {"tab_id": basic}, "target": {"role": "button", "name": "Duplicate"}})
            error(ambiguous, "ambiguous_target")
            if not 1 <= len(ambiguous["error"]["details"]["candidates"]) <= 5:
                raise RuntimeError("Missing bounded ambiguity candidates")
            passed("duplicate controls are rejected with bounded candidates")
            for target, code in (({"name": "Password"}, "protected_input"), ({"name": "Read only"}, "target_readonly")):
                error(await execute("browser.page.fill", {"tab": {"tab_id": basic}, "target": target, "value": "not applied"}), code)
            for target, code in (({"name": "Hidden"}, "target_hidden"), ({"name": "Disabled"}, "target_disabled")):
                error(await execute("browser.page.click", {"tab": {"tab_id": basic}, "target": target}), code)
            passed("password, read-only, hidden and disabled controls are protected")
            success(await execute("browser.page.click", {"tab": {"tab_id": basic}, "target": {"name": "Add delayed button"}}))
            success(await execute("browser.page.click", {"tab": {"tab_id": basic}, "target": {"name": "Delayed action"}, "wait_ms": 1500}))
            passed("bounded wait finds a dynamically added control without replay")
            frameworks = await create("/frameworks")
            await inspect(frameworks, {"label": "React query"})
            for label, output, value in (("React query", "react-output", "React native setter"), ("Vue notes", "vue-output", "Vue model event")):
                success(await execute("browser.page.fill", {"tab": {"tab_id": frameworks}, "target": {"label": label}, "value": value}))
                await inspect(frameworks, {"id": output, "text": value})
                passed(label + " controlled component model updates")
            duplicate = await create("/duplicate")
            await inspect(duplicate, {"name": "Run search"})
            error(await execute("browser.tabs.resolve", {"tab": {"title": "Chat2Local Phase 2 Fixture"}}), "ambiguous_tab")
            passed("duplicate tab titles are rejected instead of selecting the first")
            success(await execute("browser.page.click", {"tab": {"tab_id": basic}, "target": {"role": "link", "name": "Jump to section"}}))
            passed("link click returns execution status without claiming navigation completion")
            unauthorized = await create("/", host="localhost")
            error(await execute("browser.page.inspect", {"tab": {"tab_id": unauthorized}}), "permission_denied")
            passed("a distinct ungranted localhost origin is rejected before DOM access")
            original = await tool("read", {"path": "AGENTS.md", "start_line": 1, "end_line": 1})
            if original.get("kind") != "file" or original.get("lines_returned") != 1:
                raise RuntimeError("Original file tool returned unexpected data")
            passed("original file tool remains available on the same Agent")
    report["ok"] = True
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    if report_path:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    asyncio.run(check(args.report))
