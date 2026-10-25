"""Keep Python models, the wire validator and published schemas in step."""
import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from chat2local.browser.bridge import BrowserBridge
from chat2local.browser.capabilities import CapabilityRegistry
from chat2local.browser.errors import CAPABILITY_NAMES
from chat2local.runtime.config import BrowserConfig

ROOT = Path(__file__).resolve().parents[1]


def test_documented_browser_schemas_match_live_registry():
    entries = CapabilityRegistry(BrowserBridge(BrowserConfig())).discover(detail="full")["result"]["capabilities"]
    result = CapabilityRegistry(BrowserBridge(BrowserConfig())).discover(detail="full")["result"]
    expected = {**result, "capabilities": [{key: value for key, value in item.items() if key != "available"} for item in entries]}
    saved = json.loads((ROOT / "browser-extension/capability-schemas.json").read_text(encoding="utf-8"))
    assert saved == expected, "Regenerate capability-schemas.json from CapabilityRegistry after model changes"
    assert {item["name"] for item in entries} == set(CAPABILITY_NAMES)


def variants(value, schema, definitions, path="arguments"):
    """Derive field/type/required/boundary probes from the actual model schema."""
    if "$ref" in schema:
        schema = definitions[schema["$ref"].split("/")[-1]]
    if "anyOf" in schema and "properties" not in schema:
        schema = next(branch for branch in schema["anyOf"] if branch.get("type") != "null")
        if "$ref" in schema:
            schema = definitions[schema["$ref"].split("/")[-1]]
    for probe in (None, True, False, 1, 1.5, "", [], {}):
        yield f"{path}: {probe!r}", probe
    if isinstance(value, dict):
        if path == "arguments":  # Action-specific examples jointly cover the root fields below.
            assert set(value) <= set(schema["properties"]), f"Unknown sample fields at {path}"
        else:
            assert set(value) == set(schema["properties"]), f"Add samples for new fields at {path}"
        yield f"{path}: unknown field", {**value, "unexpected_field": True}
        for key, child in value.items():
            yield f"{path}.{key}: missing", {k: v for k, v in value.items() if k != key}
            for label, probe in variants(child, schema["properties"][key], definitions, f"{path}.{key}"):
                yield label, {**value, key: probe}
    elif isinstance(value, str):
        for choice in schema.get("enum", []):
            yield f"{path}: enum {choice}", choice
        for size in (schema.get("minLength", 0), schema.get("maxLength", 256)):
            for length in (max(0, size - 1), size, size + 1):
                yield f"{path}: length {length}", (value + "a" * length)[:length]
        # JSON Schema and Python count Unicode characters, not UTF-16 code units.
        yield f"{path}: Unicode length boundary", "😀" * schema.get("maxLength", 256)
    elif type(value) is int:
        for boundary in (schema.get("minimum", 0), schema.get("maximum", 2**53 - 1)):
            for number in (boundary - 1, boundary, boundary + 1):
                yield f"{path}: number {number}", number
    elif isinstance(value, list):
        for length in (0, 1, schema.get("maxItems", 1), schema.get("maxItems", 1) + 1):
            yield f"{path}: items {length}", (value or [None]) * length
        if value:
            for label, probe in variants(value[0], schema["items"], definitions, f"{path}[0]"):
                yield label, [probe, *value[1:]]


def test_extension_and_python_agree_on_schema_field_boundaries():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the cross-language browser contract check")
    tab = {"tab_id": 1, "url": "https://example.com/", "title": "Example"}
    target = {key: "Example" for key in ("role", "name", "aria_label", "label", "placeholder", "href", "text", "id", "css")}
    target["ref"] = "el_example"
    samples = {
        "browser.tabs.create": {"url": "https://example.com/"},
        "browser.tabs.list": {"tab": tab, "limit": 20},
        "browser.tabs.resolve": {"tab": tab},
        "browser.tabs.manage": [{"action": "activate", "tab": tab}, {"action": "close", "tab": tab},
                                {"action": "move", "tab": tab, "index": 0, "window_id": 1},
                                {"action": "set_pinned", "tab": tab, "pinned": True}],
        "browser.windows.list": {"window_id": 1, "limit": 20, "tab_offset": 50, "tab_limit": 50,
                                 "tab_snapshot": "a" * 64},
        "browser.windows.manage": [{"action": "create", "url": "https://example.com/"},
                                   {"action": "focus", "window_id": 1},
                                   {"action": "close", "window_id": 1, "expected_tab_ids": [1]}],
        "browser.navigation.go": [{"action": "navigate", "tab": tab, "url": "https://example.com/"},
                                  *[{"action": action, "tab": tab} for action in ("reload", "back", "forward")]],
        "browser.page.read": {"tab": tab, "cursor": "example", "max_chars": 8000},
        "browser.page.inspect": {"tab": tab, "target": target, "limit": 20, "wait_ms": 0,
                                 "select_options": {"offset": 0, "limit": 20}},
        "browser.page.dom.inspect": {"tab": tab, "css": "span", "text": "Example", "tag": "span", "scope": "#dialog",
                                     "limit": 5, "depth": 1, "wait_ms": 0},
        "browser.page.click": {"tab": tab, "target": target, "wait_ms": 0},
        "browser.page.fill": {"tab": tab, "target": target, "wait_ms": 0, "value": "text"},
        **{f"browser.page.{name}": {"tab": tab, "target": target, "wait_ms": 0}
           for name in ("double_click", "hover", "focus")},
        "browser.page.select": {"tab": tab, "target": target, "wait_ms": 0,
                                "options": [{"value": "a", "label": "Option", "index": 0}]},
        "browser.page.set_checked": {"tab": tab, "target": target, "wait_ms": 0, "checked": True},
        "browser.page.scroll": {"tab": tab, "container": "element", "target": target,
                                "delta_x": 0, "delta_y": 100, "wait_ms": 0},
        "browser.page.press_key": {"tab": tab, "target": target, "wait_ms": 0, "key": "Enter", "modifiers": ["Control"]},
        "browser.page.wait_for": {"tab": tab, "target": target, "state": "actionable", "timeout_ms": 5000},
    }
    entries = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries
    assert set(samples) == set(entries), "Add contract samples for new capabilities"
    cases, expected, labels = [], [], []
    for name, entry in entries.items():
        schema = entry.arguments.model_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        examples = samples[name] if isinstance(samples[name], list) else [samples[name]]
        assert set().union(*(set(example) for example in examples)) == set(schema["properties"])
        probes = [(label, value) for example in examples for label, value in
                  [("example", example), *variants(example, schema, schema.get("$defs", {}))]]
        for label, arguments in probes:
            try:
                wire = entry.arguments.model_validate(arguments).model_dump(exclude_none=True)
                accepted = True
            except ValidationError:
                wire, accepted = arguments, False
            assert jsonschema.Draft202012Validator(schema).is_valid(arguments) == accepted, f"Schema/runtime drift: {name}: {label}"
            cases.append({"name": name, "arguments": wire})
            expected.append(accepted)
            labels.append(f"{name}: {label}")
    completed = subprocess.run([node, str(ROOT / "browser-extension/tests/contract-helper.mjs")],
                               input=json.dumps(cases), text=True, encoding="utf-8", capture_output=True, timeout=30, check=True)
    actual = json.loads(completed.stdout)
    assert set(actual["capabilities"]) == set(entries)
    assert len(actual["accepted"]) == len(expected)
    mismatches = [label for label, py, js in zip(labels, expected, actual["accepted"]) if py != js]
    assert not mismatches, "Python/extension contract drift: " + "; ".join(mismatches)


def test_url_semantics_are_additional_to_schema_without_exempting_action_contracts():
    """Only URL parsing semantics exceed JSON Schema, never whole action branches."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the cross-language browser contract check")
    entries = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries
    cases = []
    for name, base in (
        ("browser.tabs.create", {}),
        ("browser.windows.manage", {"action": "create"}),
        ("browser.navigation.go", {"action": "navigate", "tab": {"tab_id": 1}}),
    ):
        schema = entries[name].arguments.model_json_schema()
        for url, schema_valid in (
            ("file:///a", False), ("javascript:alert(1)", False),
            ("https://user:pass@example.com/", True), ("https://example.com:0/", True),
            ("https://example.com/a b", False), ("https://example.com/\u200b", True),
            ("https://", False), ("https:///example.com", True),
            ("https://[invalid", True), ("http://example.com:65536/", True),
        ):
            arguments = {**base, "url": url}
            assert jsonschema.Draft202012Validator(schema).is_valid(arguments) is schema_valid
            with pytest.raises(ValidationError):
                entries[name].arguments.model_validate(arguments)
            cases.append({"name": name, "arguments": arguments})
    completed = subprocess.run([node, str(ROOT / "browser-extension/tests/contract-helper.mjs")],
                               input=json.dumps(cases), text=True, encoding="utf-8", capture_output=True, timeout=30, check=True)
    assert json.loads(completed.stdout)["accepted"] == [False] * len(cases)


@pytest.mark.parametrize("name,arguments,valid", [
    ("browser.tabs.resolve", {"tab": {}}, False),
    ("browser.tabs.resolve", {"tab": {"tab_id": None, "url": None, "title": None}}, False),
    ("browser.tabs.resolve", {"tab": {"tab_id": 0, "title": None}}, True),
    ("browser.page.click", {"tab": {"tab_id": 1}, "target": {}}, False),
    ("browser.page.click", {"tab": {"tab_id": 1}, "target": {"ref": None, "css": None}}, False),
    ("browser.page.click", {"tab": {"tab_id": 1}, "target": {"ref": "el_example", "css": None}}, True),
    ("browser.page.click", {"tab": {"tab_id": 1}, "target": {"ref": "not a ref"}}, False),
    ("browser.page.inspect", {"tab": {"tab_id": 1}, "select_options": {}}, False),
    ("browser.page.inspect", {"tab": {"tab_id": 1}, "target": None, "select_options": {}}, False),
    ("browser.page.inspect", {"tab": {"tab_id": 1}, "select_options": None}, True),
    ("browser.page.inspect", {"tab": {"tab_id": 1}, "target": {"id": "x"}, "select_options": {}}, True),
    ("browser.page.dom.inspect", {"tab": {"tab_id": 1}}, False),
    ("browser.page.dom.inspect", {"tab": {"tab_id": 1}, "scope": "body", "css": None, "text": None, "tag": None}, False),
    ("browser.page.dom.inspect", {"tab": {"tab_id": 1}, "css": None, "text": "Send"}, True),
    ("browser.page.select", {"tab": {"tab_id": 1}, "target": {"id": "x"}, "options": [{}]}, False),
    ("browser.page.select", {"tab": {"tab_id": 1}, "target": {"id": "x"}, "options": [{"value": None}]}, False),
    ("browser.page.select", {"tab": {"tab_id": 1}, "target": {"id": "x"}, "options": [{"value": ""}]}, True),
    ("browser.page.select", {"tab": {"tab_id": 1}, "target": {"id": "x"}, "options": []}, True),
    ("browser.page.scroll", {"tab": {"tab_id": 1}, "container": "page", "delta_y": 1}, True),
    ("browser.page.scroll", {"tab": {"tab_id": 1}, "container": "page", "target": None, "delta_y": 1}, True),
    ("browser.page.scroll", {"tab": {"tab_id": 1}, "container": "page", "target": {"id": "x"}, "delta_y": 1}, False),
    ("browser.page.scroll", {"tab": {"tab_id": 1}, "container": "element", "delta_y": 1}, False),
    ("browser.page.scroll", {"tab": {"tab_id": 1}, "container": "element", "target": None, "delta_y": 1}, False),
    ("browser.page.scroll", {"tab": {"tab_id": 1}, "container": "page", "delta_y": 0}, False),
    ("browser.page.press_key", {"tab": {"tab_id": 1}, "target": {"id": "x"}, "key": "Tab", "modifiers": ["Shift", "Shift"]}, False),
])
def test_discovered_schema_expresses_non_null_selector_relations(name, arguments, valid):
    entries = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries
    model = entries[name].arguments
    assert jsonschema.Draft202012Validator(model.model_json_schema()).is_valid(arguments) is valid
    if valid:
        model.model_validate(arguments)
    else:
        with pytest.raises(ValidationError):
            model.model_validate(arguments)


def test_discovery_agent_contracts_are_complete_compact_and_public():
    from chat2local.browser.errors import (
        ERROR_GUIDANCE,
        EXTENSION_ERROR_MESSAGES,
        extension_failure,
    )
    from chat2local.mcp.metadata import TOOL_METADATA
    from chat2local.mcp.output_schema import OUTPUT_SCHEMAS
    result = CapabilityRegistry(BrowserBridge(BrowserConfig())).discover(detail="full")
    jsonschema.validate(result, OUTPUT_SCHEMAS['capability_discover'])
    assert len(TOOL_METADATA) == 12
    assert len(result['result']['capabilities']) == 20
    for entry in result['result']['capabilities']:
        assert entry['description'] and entry['result_contract']
        schema = entry['input_schema']
        for definition in [schema, *schema.get('$defs', {}).values()]:
            assert all(field.get('description') for field in definition.get('properties', {}).values())
        assert not entry['available']
    assert set(EXTENSION_ERROR_MESSAGES) <= set(ERROR_GUIDANCE)
    assert 'may already have executed' in ERROR_GUIDANCE['capability_unavailable']
    assert 'stop' in ERROR_GUIDANCE['target_disabled']
    cleaned = extension_failure({'code': 'ambiguous_target', 'details': {'candidates': [
        {'ref': 'el_safe_ref', 'name': 'Run', 'visible': True, 'disabled': False, 'truncated': False, 'untrusted_extra': 'SECRET'}]}})
    assert cleaned['error']['details']['candidates'][0]['ref'] == 'el_safe_ref'
    assert 'SECRET' not in json.dumps(cleaned)
    jsonschema.validate(cleaned, OUTPUT_SCHEMAS['capability_execute'])
