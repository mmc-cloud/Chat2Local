"""Replaceable pywebview shell. Run from any working directory."""

from __future__ import annotations

import argparse
import sys
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from bridge import GuiBridge
from chat2local.runtime import config as core_config

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def frontend_url(dev_url: str | None) -> str:
    if dev_url:
        message = "--dev-url must be a loopback http(s) URL without credentials, query or fragment"
        try:
            parsed = urlsplit(dev_url)
            hostname = parsed.hostname
            if parsed.port is not None and parsed.port == 0:
                raise ValueError
            loopback = hostname == "localhost"
            if hostname and not loopback:
                try:
                    loopback = ip_address(hostname).is_loopback
                except ValueError:
                    loopback = False
        except ValueError:
            raise ValueError(message) from None
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or not loopback
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(message)
        return dev_url
    index = FRONTEND / "dist" / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            "Frontend build missing. Run pnpm install and pnpm build "
            "in gui/pywebview/frontend first."
        )
    # An explicit file URI avoids pywebview's automatic server for local paths.
    return index.as_uri()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dev-url", help="Loopback Vite URL, usually http://127.0.0.1:5173"
    )
    args = parser.parse_args()
    try:
        url = frontend_url(args.dev_url)
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f"{exc}\n")
    import webview

    bridge = GuiBridge()
    bridge._window = webview.create_window(
        "Chat2Local",
        url=url,
        js_api=bridge,
        width=1100,
        height=720,
        min_size=(900, 600),
        resizable=True,
    )
    webview.start(
        gui="edgechromium" if sys.platform == "win32" else None,
        http_server=False,
        private_mode=False,
        storage_path=str(core_config.user_data_directory() / "gui" / "webview"),
    )


if __name__ == "__main__":
    main()
