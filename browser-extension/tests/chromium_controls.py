"""Controlled native-layout checks via Chromium's CLI, no automation/input backend.

Usage: python browser-extension/tests/chromium_controls.py --chromium <chrome.exe>
Requires an existing Chromium binary and the repository's existing npm dev deps.
Uses one disposable profile, loopback-only fixtures, and a runtime authorization
stub. This verifies the packaged DOM code, not the MV3/Core transport chain.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
FILES = {
    "/browser-safety.js": ROOT / "browser-safety.js",
    "/content-script.js": ROOT / "content-script.js",
    "/checks.js": ROOT / "tests/chromium-controls.js",
    "/react.js": ROOT / "node_modules/react/umd/react.development.js",
    "/react-dom.js": ROOT / "node_modules/react-dom/umd/react-dom.development.js",
    "/vue.js": ROOT / "node_modules/vue/dist/vue.global.prod.js",
}
HTML = b'''<!doctype html><meta charset="utf-8"><title>Chat2Local isolated controls</title>
<style>body{margin:0}#fixture{min-height:1px}button{min-width:100px;min-height:24px}</style>
<div id="fixture"></div><div id="portals"></div><pre id="test-report">pending</pre>
<script>
window.domListener = null;
window.chrome = {runtime:{id:"controlled-test",sendMessage:async()=>({ok:true}),
onMessage:{addListener(fn){window.domListener=fn}}}};
</script>
<script src="/react.js"></script><script src="/react-dom.js"></script><script src="/vue.js"></script>
<script src="/browser-safety.js"></script><script src="/content-script.js"></script><script src="/checks.js"></script>
'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        if self.path == "/":
            body, content_type = HTML, "text/html; charset=utf-8"
        elif self.path in FILES:
            body, content_type = FILES[self.path].read_bytes(), "application/javascript; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/report", "/progress"):
            self.send_error(404)
            return
        size = int(self.headers.get("Content-Length", "0"))
        if size > 65536:
            self.send_error(413)
            return
        self.server.report = json.loads(self.rfile.read(size))
        if self.path == "/report":
            self.server.finished.set()
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.chromium.is_file():
        parser.error("--chromium must name an existing binary; this helper never downloads browsers")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests, server.report, server.finished = [], None, threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="chat2local-browser-check-") as profile:
            process = subprocess.Popen([
                str(args.chromium.resolve()), "--headless=new", "--dump-dom", "--virtual-time-budget=15000",
                "--window-size=1000,800", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
                "--disable-background-networking", "--disable-component-update", "--no-proxy-server",
                f"--user-data-dir={profile}", f"http://127.0.0.1:{server.server_port}/",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 35
                while not server.finished.wait(0.1):
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(f"Chromium did not finish: requests={server.requests}; progress={server.report}")
                report = server.report
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
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
