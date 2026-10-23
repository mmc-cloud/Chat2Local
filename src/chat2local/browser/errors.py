"""Safe browser protocol errors shared by discovery, transport and execution."""

from typing import Any

CAPABILITY_NAMES = (
    "browser.tabs.create", "browser.tabs.list", "browser.tabs.resolve",
    "browser.page.read", "browser.page.inspect", "browser.page.dom.inspect", "browser.page.click", "browser.page.fill",
)

EXTENSION_ERROR_MESSAGES = {
    "unknown_capability": "Browser extension does not support the requested capability",
    "invalid_arguments": "Browser extension rejected the operation arguments",
    "request_expired": "Browser request expired before execution",
    "browser_operation_failed": "Chrome could not complete the operation; verify page state before retrying",
    "tab_not_found": "No matching Chrome tab exists",
    "ambiguous_tab": "Multiple tabs match; select a native Chrome tab_id from the candidates",
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
    "protected_input": "Password fields cannot be filled by this capability",
    "unsupported_element": "This element type is not supported by the requested operation",
    "stale_target": "The element changed before execution; inspect again before retrying",
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
    "target_not_found": "No target satisfies all conditions: observe again and refine, do not loosen conditions blindly.",
    "ambiguous_target": "Multiple visible action targets: choose a fresh candidate ref or refine conditions; no click/fill performed.",
    "target_hidden": "Target is hidden: stop and observe; no click/fill performed.",
    "target_disabled": "Target or ancestor is disabled/inert: stop; no click/fill performed. Do not bypass disabled state.",
    "target_readonly": "Field is read-only: stop; no fill performed.",
    "protected_input": "Protected password input: stop; no fill performed.",
    "stale_target": "Reference expired or target changed: obtain a fresh ref via read/inspect/dom.inspect. Never substitute positional CSS automatically.",
    "page_changed": "Navigation, readable content or tab selection changed: discard old cursor/ref and observe again; start read without cursor.",
    "permission_denied": "User must grant website access in extension options; MCP cannot grant it. Re-observe after authorization.",
    "unsupported_page": "Use an authorized HTTP(S) top-level page; browser internal pages and embedded documents are unsupported.",
    "unsupported_element": "Choose a supported HTML target; fill only supported text input/textarea types.",
    "scope_not_found": "Local DOM scope missing: re-observe and correct scope.",
    "ambiguous_scope": "Scope must resolve to exactly one root; refine CSS.",
    "page_too_large": "DOM/text work budget exceeded: narrow target or dom.inspect scope; lowering read max_chars does not lower extraction work.",
    "result_too_large": "Response byte budget exceeded: lower read max_chars / inspection limit or depth, or narrow scope.",
    "request_expired": "Deadline elapsed: re-observe first; never automatically replay side-effecting requests.",
    "injection_failed": "Check permission and page state; observe again before retrying.",
    "capability_busy": "Too many in-flight calls: wait for completion; do not duplicate pending actions.",
    "capability_timeout": "Outcome may be unknown: inspect actual state before any retry of create/click/fill.",
    "capability_unavailable": "Extension absent/disconnected: reconnect and discover again. An interrupted create/click/fill may already have executed; inspect state before retrying.",
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
        result["error"]["details"] = {"candidates": cleaned}
    return result
