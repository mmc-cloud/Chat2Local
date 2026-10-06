"""Build the portable Windows desktop folder with PyInstaller."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui" / "pywebview"
FRONTEND = GUI / "frontend"
ASSETS = GUI / "assets"
ENTRY = GUI / "python" / "launcher.py"
DIST = ROOT / "dist" / "windows"
WORK = ROOT / "build" / "pyinstaller"
SPEC = ROOT / "build" / "pyinstaller-spec"
VERSION_DIR = ROOT / "build" / "windows-version"


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def runtime_requirement_specs() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return [
        *data["project"]["dependencies"],
        *data.get("dependency-groups", {}).get("pywebview-gui", []),
    ]


def runtime_distributions() -> list[metadata.Distribution]:
    """Resolve the installed runtime dependency closure used by the portable build."""
    queue = [Requirement(spec) for spec in runtime_requirement_specs()]
    resolved: dict[str, metadata.Distribution] = {}
    seen_extras: dict[str, set[str]] = {}

    while queue:
        requirement = queue.pop()
        try:
            distribution = metadata.distribution(requirement.name)
        except metadata.PackageNotFoundError:
            continue
        name = str(distribution.metadata["Name"] or requirement.name)
        key = name.lower().replace("_", "-")
        extras = set(requirement.extras)
        previous_extras = seen_extras.setdefault(key, set())
        if key in resolved and extras <= previous_extras:
            continue
        previous_extras.update(extras)
        resolved[key] = distribution

        for dependency_text in distribution.requires or []:
            dependency = Requirement(dependency_text)
            if dependency.marker is not None:
                contexts = [{"extra": ""}, *({"extra": extra} for extra in extras)]
                if not any(dependency.marker.evaluate(context) for context in contexts):
                    continue
            queue.append(dependency)

    return [resolved[key] for key in sorted(resolved)]


def copy_distribution_licenses(target: Path) -> None:
    """Ship our license plus license texts for the runtime components we redistribute."""
    shutil.copy2(ROOT / "LICENSE", target / "LICENSE")
    destination = target / "THIRD_PARTY_LICENSES"
    destination.mkdir(parents=True, exist_ok=True)

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, destination / "Python-LICENSE.txt")

    prefixes = ("license", "licence", "copying", "notice")
    for distribution in runtime_distributions():
        name = str(distribution.metadata["Name"] or "unknown")
        package_dir = destination / name
        copied = False
        for item in distribution.files or []:
            item_path = Path(str(item))
            if not item_path.name.lower().startswith(prefixes):
                continue
            source = Path(distribution.locate_file(item))
            if not source.is_file():
                continue
            package_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, package_dir / item_path.name)
            copied = True
        if copied:
            continue

        package_dir.mkdir(parents=True, exist_ok=True)
        license_value = distribution.metadata.get("License") or ""
        license_expression = distribution.metadata.get("License-Expression") or ""
        classifiers = [
            value for value in distribution.metadata.get_all("Classifier", [])
            if value.startswith("License ::")
        ]
        (package_dir / "METADATA.txt").write_text(
            "\n".join([
                f"Name: {name}",
                f"Version: {distribution.version}",
                f"License: {license_value}",
                f"License-Expression: {license_expression}",
                *[f"Classifier: {value}" for value in classifiers],
                "",
            ]),
            encoding="utf-8",
            newline="\n",
        )


def write_version_file(version: str) -> Path:
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise SystemExit(f"Windows portable build requires X.Y.Z version, got {version!r}")
    numbers = tuple(int(part) for part in parts) + (0,)
    VERSION_DIR.mkdir(parents=True, exist_ok=True)
    path = VERSION_DIR / "Chat2Local.version.txt"
    path.write_text(
        f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers!r},
    prodvers={numbers!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0),
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'mmc-cloud'),
          StringStruct('FileDescription', 'Chat2Local Desktop'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'Chat2Local'),
          StringStruct('LegalCopyright', 'Copyright (c) 2026 mmc-cloud'),
          StringStruct('OriginalFilename', 'Chat2Local.exe'),
          StringStruct('ProductName', 'Chat2Local'),
          StringStruct('ProductVersion', '{version}'),
        ],
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
""",
        encoding="utf-8",
        newline="\n",
    )
    return path


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Portable desktop builds must be created on Windows")

    pnpm = shutil.which("pnpm.cmd") or shutil.which("pnpm")
    if pnpm is None:
        raise SystemExit("pnpm was not found on PATH")
    run([pnpm, "build"], cwd=FRONTEND)
    index = FRONTEND / "dist" / "index.html"
    if not index.is_file():
        raise SystemExit("Frontend build did not produce dist/index.html")

    target = DIST / "Chat2Local"
    if target.exists():
        shutil.rmtree(target)
    WORK.mkdir(parents=True, exist_ok=True)
    SPEC.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    version_file = write_version_file(project_version())

    from PyInstaller.__main__ import run as pyinstaller_run

    pyinstaller_run(
        [
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--name=Chat2Local",
            f"--icon={ASSETS / 'chat2local.ico'}",
            f"--version-file={version_file}",
            f"--paths={ROOT / 'src'}",
            f"--paths={GUI / 'python'}",
            f"--add-data={FRONTEND / 'dist'}:gui/pywebview/frontend/dist",
            f"--add-data={ASSETS}:gui/pywebview/assets",
            f"--distpath={DIST}",
            f"--workpath={WORK}",
            f"--specpath={SPEC}",
            str(ENTRY),
        ]
    )

    executable = target / "Chat2Local.exe"
    if not executable.is_file():
        raise SystemExit("PyInstaller completed without Chat2Local.exe")
    copy_distribution_licenses(target)
    print(f"Portable desktop build: {target}")


if __name__ == "__main__":
    main()
