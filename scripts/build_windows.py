"""Build the portable Windows desktop folder with PyInstaller."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "gui" / "pywebview"
FRONTEND = GUI / "frontend"
ASSETS = GUI / "assets"
ENTRY = GUI / "python" / "launcher.py"
DIST = ROOT / "dist" / "windows"
WORK = ROOT / "build" / "pyinstaller"
SPEC = ROOT / "build" / "pyinstaller-spec"


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


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

    from PyInstaller.__main__ import run as pyinstaller_run

    pyinstaller_run(
        [
            "--noconfirm",
            "--clean",
            "--onedir",
            "--windowed",
            "--name=Chat2Local",
            f"--icon={ASSETS / 'chat2local.ico'}",
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
    print(f"Portable desktop build: {target}")


if __name__ == "__main__":
    main()
