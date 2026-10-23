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
from chat2local.browser.errors import CAPABILITY_NAMES


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

            discovery = success(await tool("capability_discover", {"query": "browser", "detail": "summary"}))
            if {item["name"] for item in discovery["capabilities"]} != set(CAPABILITY_NAMES) or not all(item["available"] for item in discovery["capabilities"]):
                raise RuntimeError("Reload the current extension first; all registered capabilities must be available")
            passed(f"all {len(CAPABILITY_NAMES)} independently registered capabilities are online")
            definition = success(await tool("capability_discover", {"query": "browser.page.wait_for", "detail": "full"}))
            if "actionable" not in definition["capabilities"][0]["input_schema"]["properties"]["state"]["enum"]:
                raise RuntimeError("Update the isolated test Core and extension to the reviewed Phase 3 contract")
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
                    if tab["url"] == f"http://{host}:18766{path}":  # Returned URLs deliberately omit the query.
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
            listed = success(await execute("browser.tabs.list", {"tab": {"tab_id": basic}}))
            if resolved["tab_id"] != basic or len(listed["tabs"]) != 1 or listed["tabs"][0]["tab_id"] != basic:
                raise RuntimeError("Native tab lookup failed")
            passed("list and resolve locate the explicit native Chrome tab")
            read = success(await execute("browser.page.read", {"tab": {"tab_id": basic}, "max_chars": 1000}))
            dom = success(await execute("browser.page.dom.inspect", {"tab": {"tab_id": basic}, "css": "#result", "depth": 0}))
            if not read["content_is_untrusted"] or not read["blocks"] or dom["elements"][0]["text"] != "Waiting":
                raise RuntimeError("Phase 2 reading/DOM inspection returned unexpected content")
            passed("Phase 2 page.read and local dom.inspect remain available")
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
            phase3 = await create("/phase3")
            await inspect(phase3, {"id": "double"})

            async def action(operation, arguments):
                return success(await execute(f"browser.page.{operation}", {"tab": {"tab_id": phase3}, **arguments}))

            await action("double_click", {"target": {"id": "double"}})
            await inspect(phase3, {"id": "double-result", "text": "2:1"})
            await action("hover", {"target": {"id": "hover"}})
            await inspect(phase3, {"id": "hover-result", "text": "hovered"})
            await action("focus", {"target": {"id": "keys"}})
            await action("press_key", {"target": {"id": "keys"}, "key": "Enter", "modifiers": ["Control"]})
            await inspect(phase3, {"id": "key-result", "text": "Control+Enter"})
            passed("Phase 3 double-click, hover, focus and keyboard DOM event handlers")
            await action("select", {"target": {"id": "choice"}, "options": [{"value": "b"}]})
            await inspect(phase3, {"id": "choice-result", "text": "b"})
            for options, expected in (([{"value": "same"}], "ambiguous_option"), ([{"index": 4}], "option_disabled"),
                                      ([{"value": "missing"}], "option_not_found")):
                error(await execute("browser.page.select", {"tab": {"tab_id": phase3}, "target": {"id": "choice"}, "options": options}), expected)
            if (await action("select", {"target": {"id": "multi"}, "options": [{"label": "Alpha"}, {"index": 1}]}))["selected_count"] != 2:
                raise RuntimeError("Native multiple selection failed")
            await action("set_checked", {"target": {"id": "check"}, "checked": True})
            if (await action("set_checked", {"target": {"id": "check"}, "checked": True}))["changed"]:
                raise RuntimeError("Matching checked state must be idempotent")
            await inspect(phase3, {"id": "check-result", "text": "true"})
            error(await execute("browser.page.set_checked", {"tab": {"tab_id": phase3}, "target": {"id": "radio-one"}, "checked": False}), "unsupported_state")
            await action("set_checked", {"target": {"id": "radio-two"}, "checked": True})
            for framework in ("react", "vue"):
                await action("set_checked", {"target": {"id": framework + "-check"}, "checked": True})
                await action("select", {"target": {"id": framework + "-choice"}, "options": [{"value": "b"}]})
                await inspect(phase3, {"id": framework + "-state", "text": "true:b"})
            passed("native select, idempotent checkbox, radio semantics and React/Vue state models")
            moved = await action("scroll", {"container": "element", "target": {"id": "scroll-box"}, "delta_y": 70})
            if moved["after"]["y"] <= moved["before"]["y"]:
                raise RuntimeError("Explicit element scroll did not move")
            await action("scroll", {"container": "page", "delta_y": 300})
            await action("wait_for", {"target": {"id": "add-later"}, "state": "attached", "timeout_ms": 0})
            await action("wait_for", {"target": {"id": "add-later"}, "state": "actionable", "timeout_ms": 0})
            for button, state in (("add-later", "visible"), ("disable-later", "disabled"), ("enable-later", "enabled"),
                                  ("hide-later", "hidden"), ("remove-later", "detached")):
                await action("click", {"target": {"id": button}})
                await action("wait_for", {"target": {"id": "later"}, "state": state, "timeout_ms": 2000})
            error(await execute("browser.page.wait_for", {"tab": {"tab_id": phase3}, "target": {"id": "missing"}, "timeout_ms": 0}), "wait_timeout")
            observed = await inspect(phase3, {"id": "double"})
            old_ref = observed["elements"][0]["ref"]
            await action("click", {"target": {"id": "replace"}})
            error(await execute("browser.page.wait_for", {"tab": {"tab_id": phase3}, "target": {"ref": old_ref}, "state": "detached", "timeout_ms": 0}), "stale_target")
            passed("explicit element/page scrolling, dynamic state waits, timeout and stale ref rejection")
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
