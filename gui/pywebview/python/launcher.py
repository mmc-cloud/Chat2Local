"""Frozen desktop entry point that can also host a detached Core process."""

from __future__ import annotations

import sys

CORE_FLAG = "--core"


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == CORE_FLAG:
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        from chat2local.main import main as core_main

        core_main()
        return

    from app import main as desktop_main

    desktop_main()


if __name__ == "__main__":
    main()
