"""Safe browser protocol errors shared by discovery, transport and execution."""

from typing import Any

CAPABILITY_NAMES = (
    "browser.tabs.create", "browser.tabs.list", "browser.tabs.resolve",
    "browser.page.inspect", "browser.page.click", "browser.page.fill",
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
    "page_changed": "The tab navigated or its document changed; inspect again before retrying",
    "injection_failed": "Could not reach the page; check website permissions and page state",
    "target_not_found": "No element matches all target conditions",
    "ambiguous_target": "Multiple elements match; refine the target using candidate information",
    "target_hidden": "The selected element is hidden",
    "target_disabled": "The selected element is disabled or inert",
    "target_readonly": "The selected field is read-only",
    "protected_input": "Password fields cannot be filled by this capability",
    "unsupported_element": "This element type is not supported by the requested operation",
    "stale_target": "The element changed before execution; inspect again before retrying",
    "page_too_large": "Too many candidate controls to establish a unique target safely",
    "result_too_large": "Browser response exceeds the size limit; use more specific filters",
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
                       {"role": 64, "name": 160, "id": 160, "href": 2048,
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
            for key in ("visible", "disabled", "read_only") if code == "ambiguous_target" else ():
                if type(candidate.get(key)) is bool:
                    item[key] = candidate[key]
            cleaned.append(item)
        result["error"]["details"] = {"candidates": cleaned}
    return result
