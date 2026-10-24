"""Verify that built wheels/sdists can serve the Web UI without Node.js."""

from pathlib import Path
import re
import sys
import tarfile
import zipfile


def check(path: Path) -> None:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            root = "chat2local/management/static/"
            verify(archive.read(root + "index.html"), lambda name: archive.read(root + name))
    else:
        with tarfile.open(path) as archive:
            indices = [name for name in archive.getnames() if name.endswith("/src/chat2local/management/static/index.html")]
            if len(indices) != 1:
                raise ValueError("sdist must contain exactly one packaged Web UI")
            root = indices[0].removesuffix("index.html")
            def read(name):
                handle = archive.extractfile(root + name)
                if handle is None:
                    raise ValueError("Missing static resource")
                return handle.read()
            verify(read("index.html"), read)
    print(f"Web UI resources verified: {path.name}")


def verify(index: bytes, read) -> None:
    html = index.decode("utf-8")
    if 'name="chat2local-transport" content="http"' not in html:
        raise ValueError("Packaged UI must use HttpTransport")
    assets = re.findall(r'(?:src|href)="\./(assets/[^\"]+)"', html)
    if not any(name.endswith(".js") for name in assets) or not any(name.endswith(".css") for name in assets):
        raise ValueError("Missing compiled JS/CSS references")
    for name in assets:
        if ".." in Path(name).parts or not read(name):
            raise ValueError("Missing or invalid compiled resource")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python scripts/check_web_ui_package.py <wheel-or-sdist> ...")
    for argument in sys.argv[1:]:
        check(Path(argument))
