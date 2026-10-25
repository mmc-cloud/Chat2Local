"""Safe browser protocol errors shared by discovery, transport and execution."""

from typing import Any

CAPABILITY_NAMES = (
    "browser.tabs.create", "browser.tabs.list", "browser.tabs.resolve",
    "browser.tabs.manage", "browser.windows.list", "browser.windows.manage", "browser.navigation.go",
    "browser.page.read", "browser.page.inspect", "browser.page.dom.inspect", "browser.page.click", "browser.page.fill",
    "browser.page.double_click", "browser.page.hover", "browser.page.focus", "browser.page.select",
    "browser.page.set_checked", "browser.page.scroll", "browser.page.press_key", "browser.page.wait_for",
)

EXTENSION_ERROR_MESSAGES = {
    "unknown_capability": "Browser extension does not support the requested capability",
    "invalid_arguments": "Browser extension rejected the operation arguments",
    "request_expired": "Browser request deadline elapsed; observe actual state before retrying",
    "browser_operation_failed": "Chrome could not complete the operation; verify page state before retrying",
    "tab_not_found": "No matching Chrome tab exists",
    "ambiguous_tab": "Multiple tabs match; select a native Chrome tab_id from the candidates",
    "window_not_found": "No matching Chrome window exists",
    "unsupported_window": "Only normal Chrome windows are supported for this operation",
    "window_changed": "Window tab membership or order changed; restart pagination and obtain fresh confirmation before closing",
    "history_unavailable": "No history entry is available in the requested direction",
    "permission_denied": "Website access is not granted; authorize this website in extension options",
    "unsupported_page": "This page cannot be automated; only authorized HTTP(S) top-level documents are supported",
    "page_changed": "The page, content or tab selection changed; read again without the old cursor before retrying",
    "injection_failed": "Could not reach the page; check website permissions and page state",
    "target_not_found": "No element matches all target conditions",
    "invalid_selector": "Invalid CSS selector; use CSS syntax supported by Chrome",
    "invalid_cursor": "Invalid page reading cursor; restart page.read without a cursor",
    "scope_not_found": "No element matches the DOM inspection scope",
    "ambiguous_scope": "Multiple elements match the DOM inspection scope; narrow the CSS scope",
    "ambiguous_target": "Multiple elements match; refine the target using candidate information",
    "target_hidden": "The selected element is hidden",
    "target_disabled": "The selected element is disabled or inert",
    "target_readonly": "The selected field is read-only",
    "protected_input": "Password input or sensitive option data is protected from this operation",
    "unsupported_element": "This element type is not supported by the requested operation",
    "unsupported_state": "A checked radio cannot be unchecked directly; select another radio in its group",
    "state_not_reached": "The browser or page did not retain the requested state; observe actual state before retrying",
    "option_not_found": "No native select option matches all option conditions",
    "ambiguous_option": "Multiple native options match; refine value/label or supply an exact index",
    "option_disabled": "The requested option or its optgroup is disabled",
    "target_not_scrollable": "The explicit element is not scrollable on every requested axis",
    "wait_timeout": "The requested element state was not observed before the wait timeout",
    "stale_target": "The element reference expired or target changed; observe actual state before retrying",
    "page_too_large": "Page exceeds the bounded DOM/text reading or target-discovery limits",
    "result_too_large": "Browser response exceeds the size limit; use more specific filters",
}

# Published once per discovery response, not copied into every capability.
ERROR_GUIDANCE = {
    "invalid_arguments": "Input/schema/URL error: correct arguments before retrying; no action dispatched by the rejecting validator.",
    "invalid_selector": "CSS syntax error: correct CSS using a fresh local DOM observation.",
    "invalid_cursor": "Malformed or invalid continuation: restart page.read without cursor.",
    "unknown_capability": "Unknown name: discover supported capabilities on the same device.",
    "tab_not_found": "No exact tab match: list tabs and choose a native tab_id.",
    "ambiguous_tab": "Multiple tabs: choose a native tab_id from candidates; never pick the first implicitly.",
    "window_not_found": "Re-list normal windows on the same device and choose an explicit native window_id.",
    "unsupported_window": "Choose a normal Chrome window; popup/app/devtools windows cannot be managed or used for tab moves.",
    "window_changed": "Window continuation/close refused: discard old pages and re-list the exact window. For close, obtain fresh user confirmation for its complete tab ID set. Never silently update the snapshot or expected set. Pagination checks order too; close checks membership.",
    "history_unavailable": "No previous/next history entry; observe the current tab and choose another navigation explicitly. Never retry automatically.",
    "target_not_found": "No target satisfies all conditions: observe again and refine, do not loosen conditions blindly.",
    "ambiguous_target": "Multiple eligible matches (wait_for includes hidden elements): refine conditions or use a fresh ref. Stop; preparation or an earlier event in a sequence may already have run.",
    "target_hidden": "Target is hidden: stop and observe; preparation or an earlier event in a sequence may already have run.",
    "target_disabled": "Target or ancestor is disabled/inert: stop. Do not bypass disabled state; preparation or an earlier event may already have run.",
    "target_readonly": "Field is read-only: fill or state-setting is refused; press_key may dispatch supported events without editing the value. Re-observe if preparation already ran.",
    "protected_input": "Protected password input or sensitive select/ancestor metadata: stop; do not bypass this protection.",
    "stale_target": "Node removed/replaced, document/URL changed, reference evicted or preparation locator no longer matched: observe again. Unrelated DOM/text/attribute updates do not invalidate node identity. Never substitute positional CSS automatically.",
    "page_changed": "Navigation, readable content or tab selection changed: discard old cursor/ref and observe again; start read without cursor.",
    "permission_denied": "User must grant website access in extension options; MCP cannot grant it. Re-observe after authorization.",
    "unsupported_page": "Use an authorized HTTP(S) top-level page; browser internal pages and embedded documents are unsupported.",
    "unsupported_element": "select/option discovery require native select; set_checked supports native checkbox/radio and ARIA checkbox/switch; fill requires supported text inputs. Custom dropdowns use explicit inspect/click/wait/dom.inspect/verify steps, no auto-selection fallback.",
    "unsupported_state": "A checked radio cannot be directly unchecked; select another radio in its group. An already unchecked radio requested false is a no-op.",
    "state_not_reached": "Tab activation/move/pinning, window focus or DOM focus/selection/checked state was unreadable, prevented, reverted or did not reach the explicit target (mixed is not unchecked). Actions run at most once; inspect state before any next action.",
    "option_not_found": "No option matches all conditions: inspect the control and correct known option conditions; no selection changed by this operation.",
    "ambiguous_option": "More than one option matches: refine value/label/index, never choose the first implicitly. No selection changed by this operation.",
    "option_disabled": "Requested option or optgroup is disabled: stop; do not bypass it. No selection changed by this operation.",
    "target_not_scrollable": "The explicit element cannot scroll on a requested axis; inspect layout and choose the intended container. Never fall back to a parent or page automatically.",
    "wait_timeout": "The requested state did not hold before the bounded wait ended. Read-only; re-observe and choose the next step explicitly. Stale refs, navigation and permission errors are distinct.",
    "scope_not_found": "Local DOM scope missing: re-observe and correct scope.",
    "ambiguous_scope": "Scope must resolve to exactly one root; refine CSS.",
    "page_too_large": "DOM/text work budget exceeded: narrow target or dom.inspect scope; lowering read max_chars does not lower extraction work.",
    "result_too_large": "Response byte budget exceeded: lower read max_chars / inspection limit or depth, or narrow scope.",
    "request_expired": "Deadline elapsed: re-observe first; never automatically replay side-effecting requests.",
    "injection_failed": "Check permission and page state; observe again before retrying.",
    "capability_busy": "Too many in-flight calls: wait for completion; do not duplicate pending actions.",
    "capability_timeout": "Outcome may be unknown: inspect actual state before any retry of tab/window management, navigation or DOM actions; never replay automatically.",
    "capability_unavailable": "Extension absent/disconnected: reconnect and discover again. Interrupted tab/window management, navigation or DOM actions may already have executed; inspect state before retrying.",
    "device_unavailable": "Target device offline: reconnect and discover on that same device; do not reroute actions to another device.",
    "capability_error": "Device dispatch failed: check the target device and re-observe actual state before retrying side-effecting calls.",
    "browser_operation_failed": "Execution failed or outcome unknown: inspect actual state before retrying any action; diagnostics are intentionally sanitized.",
}


def failure(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def extension_failure(error: dict[str, Any]) -> dict[str, Any]:
    code = error.get("code")
    if not isinstance(code, str) or code not in EXTENSION_ERROR_MESSAGES:
        code = "browser_operation_failed"
    result = failure(code, EXTENSION_ERROR_MESSAGES[code])
    # Candidate summaries are intentional page metadata, never exception text.
    details = error.get("details")
    dispatched = details.get("events_dispatched") if isinstance(details, dict) else None
    if (isinstance(dispatched, dict) and set(dispatched) == {"keydown", "keyup"}
            and all(type(value) is bool for value in dispatched.values())
            and (not dispatched["keyup"] or dispatched["keydown"])):
        result["error"]["details"] = {"events_dispatched": dict(dispatched)}
    candidates = details.get("candidates") if isinstance(details, dict) else None
    if code in ("ambiguous_tab", "ambiguous_target") and isinstance(candidates, list):
        text_fields = ({"title": 200, "url": 2048} if code == "ambiguous_tab" else
                       {"ref": 128, "role": 64, "name": 160, "id": 160, "href": 2048,
                        "aria_label": 160, "placeholder": 160, "input_type": 32})
        cleaned = []
        for candidate in candidates[:5]:
            if not isinstance(candidate, dict):
                continue
            item = {key: candidate[key][:limit] for key, limit in text_fields.items()
                    if isinstance(candidate.get(key), str)}
            for key in ("tab_id", "window_id") if code == "ambiguous_tab" else ():
                if type(candidate.get(key)) is int and candidate[key] >= 0:
                    item[key] = candidate[key]
            for key in ("visible", "disabled", "read_only", "truncated") if code == "ambiguous_target" else ():
                if type(candidate.get(key)) is bool:
                    item[key] = candidate[key]
            cleaned.append(item)
        result["error"].setdefault("details", {})["candidates"] = cleaned
    return result
