"""Run real MV3 authorization checks with an existing (unbranded) Chromium.

Uses a disposable extension/profile and loopback fixtures, without downloading a
browser or touching the user's Chrome profile. No mocked Chrome extension APIs.
Usage: python browser-extension/tests/chromium_authorization.py --chromium <path>
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><title>Authorization fixture</title><h1>Analytics</h1><p>Local SPA fixture</p>'
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        if self.path != "/report" or not 0 < size <= 65536:
            self.send_error(400)
            return
        self.server.report = json.loads(self.rfile.read(size))
        self.send_response(204)
        self.end_headers()
        self.server.finished.set()

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.chromium.is_file():
        parser.error("--chromium must be an existing binary; no browser is downloaded")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.finished, server.report = threading.Event(), None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="chat2local-auth-check-") as directory:
            root = Path(directory)
            extension = root / "extension"
            extension.mkdir()
            for name in ("browser-operations.js", "browser-safety.js", "content-script.js"):
                shutil.copyfile(ROOT / name, extension / name)
            worker = (ROOT / "tests/chromium-authorization.js").read_text(encoding="utf-8")
            base = f"http://127.0.0.1:{server.server_port}"
            (extension / "worker.js").write_text(f"const base = {json.dumps(base)};\n" + worker, encoding="utf-8")
            (extension / "manifest.json").write_text(json.dumps({
                "manifest_version": 3, "name": "Chat2Local authorization checks", "version": "1.0",
                "permissions": ["storage", "tabs", "scripting"],
                "host_permissions": ["http://*/*", "https://*/*"],
                "background": {"service_worker": "worker.js"},
            }), encoding="utf-8")
            process = subprocess.Popen([
                str(args.chromium.resolve()), "--headless=new", "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking",
                "--disable-component-update", "--no-proxy-server", "--remote-debugging-port=0",
                f"--user-data-dir={root / 'profile'}", f"--disable-extensions-except={extension}",
                f"--load-extension={extension}", "about:blank",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                if not server.finished.wait(45):
                    raise RuntimeError(f"Chromium authorization checks did not finish (exit={process.poll()})")
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
            report = server.report
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if report["failed"] else 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
