"""Exercise the actual release ZIP recipe, including Options' static dependencies."""

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import textwrap
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


class PageResources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.paths.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.paths.append(attrs["href"])


def test_release_zip_contains_options_resources(tmp_path, monkeypatch):
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    recipe = re.search(r"python - <<'PY'\n(.*?)\n\s+PY", workflow, re.DOTALL)
    assert recipe, "Keep this check aligned with the Chrome extension packaging step"
    extension = tmp_path / "browser-extension"
    extension.mkdir()
    for source in (ROOT / "browser-extension").iterdir():
        if source.is_file():
            shutil.copyfile(source, extension / source.name)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REF_NAME", "v-test")
    exec(compile(textwrap.dedent(recipe[1]), "release-extension-recipe", "exec"), {})

    with ZipFile(tmp_path / "release-assets/Chat2Local-v-test-chrome-extension.zip") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["manifest_version"] == 3
        parser = PageResources()
        parser.feed(archive.read(manifest["options_page"]).decode("utf-8"))
        assert "options.css" in parser.paths
        for resource in parser.paths:
            assert ":" not in resource, "Options resources must be local"
            assert archive.read(resource), f"Missing or empty Options resource: {resource}"
        assert not any(name.startswith(("tests/", "node_modules/")) for name in archive.namelist())
