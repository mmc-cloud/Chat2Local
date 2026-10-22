"""Keep Python models, the wire validator and published schemas in step."""
import json
from pathlib import Path
import shutil
import subprocess

from pydantic import ValidationError
import pytest

from chat2local.browser.bridge import BrowserBridge
from chat2local.browser.capabilities import CapabilityRegistry
from chat2local.browser.errors import CAPABILITY_NAMES
from chat2local.runtime.config import BrowserConfig

ROOT = Path(__file__).resolve().parents[1]


def test_documented_browser_schemas_match_live_registry():
    entries = CapabilityRegistry(BrowserBridge(BrowserConfig())).discover()["result"]["capabilities"]
    expected = {item["name"]: {key: item[key] for key in ("description", "input_schema")} for item in entries}
    saved = json.loads((ROOT / "browser-extension/capability-schemas.json").read_text(encoding="utf-8"))
    assert saved == expected, "Regenerate capability-schemas.json from CapabilityRegistry after model changes"
    assert set(expected) == set(CAPABILITY_NAMES)


def variants(value, schema, definitions, path="arguments"):
    """Derive field/type/required/boundary probes from the actual model schema."""
    if "$ref" in schema:
        schema = definitions[schema["$ref"].split("/")[-1]]
    if "anyOf" in schema:
        schema = next(branch for branch in schema["anyOf"] if branch.get("type") != "null")
        if "$ref" in schema:
            schema = definitions[schema["$ref"].split("/")[-1]]
    for probe in (None, True, False, 1, 1.5, "", [], {}):
        yield f"{path}: {probe!r}", probe
    if isinstance(value, dict):
        assert set(value) == set(schema["properties"]), f"Add samples for new fields at {path}"
        yield f"{path}: unknown field", {**value, "unexpected_field": True}
        for key, child in value.items():
            yield f"{path}.{key}: missing", {k: v for k, v in value.items() if k != key}
            for label, probe in variants(child, schema["properties"][key], definitions, f"{path}.{key}"):
                yield label, {**value, key: probe}
    elif isinstance(value, str):
        for size in (schema.get("minLength", 0), schema.get("maxLength", 256)):
            for length in (max(0, size - 1), size, size + 1):
                yield f"{path}: length {length}", (value + "a" * length)[:length]
        # JSON Schema and Python count Unicode characters, not UTF-16 code units.
        yield f"{path}: Unicode length boundary", "😀" * schema.get("maxLength", 256)
    elif type(value) is int:
        for boundary in (schema.get("minimum", 0), schema.get("maximum", 2**53 - 1)):
            for number in (boundary - 1, boundary, boundary + 1):
                yield f"{path}: number {number}", number


def test_extension_and_python_agree_on_schema_field_boundaries():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed for the cross-language browser contract check")
    tab = {"tab_id": 1, "url": "https://example.com/", "title": "Example"}
    target = {key: "Example" for key in ("role", "name", "aria_label", "label", "placeholder", "href", "text", "id", "css")}
    samples = {
        "browser.tabs.create": {"url": "https://example.com/"},
        "browser.tabs.list": {"tab": tab, "limit": 20},
        "browser.tabs.resolve": {"tab": tab},
        "browser.page.inspect": {"tab": tab, "target": target, "limit": 20, "wait_ms": 0},
        "browser.page.dom.inspect": {"tab": tab, "css": "span", "text": "Example", "tag": "span", "scope": "#dialog",
                                     "limit": 5, "depth": 1, "wait_ms": 0},
        "browser.page.click": {"tab": tab, "target": target, "wait_ms": 0},
        "browser.page.fill": {"tab": tab, "target": target, "wait_ms": 0, "value": "text"},
    }
    entries = CapabilityRegistry(BrowserBridge(BrowserConfig()))._entries
    assert set(samples) == set(entries), "Add contract samples for new capabilities"
    cases, expected, labels = [], [], []
    for name, entry in entries.items():
        schema = entry.arguments.model_json_schema()
        probes = [("all fields", samples[name]), *variants(samples[name], schema, schema.get("$defs", {}))]
        if name == "browser.tabs.create":
            probes += [("URL rule", {"url": url}) for url in (
                "file:///a", "javascript:alert(1)", "https://user:pass@example.com/",
                "https://example.com:0/", "https://example.com/a b", "https://example.com/\u200b",
            )]
        for label, arguments in probes:
            try:
                wire = entry.arguments.model_validate(arguments).model_dump(exclude_none=True)
                accepted = True
            except ValidationError:
                wire, accepted = arguments, False
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
