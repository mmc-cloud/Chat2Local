"""Loopback-only manual fixtures; serves an explicit allowlist, never the repo."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
ROUTES = {
    "/": ("tests/fixtures/basic.html", "text/html"),
    "/duplicate": ("tests/fixtures/basic.html", "text/html"),
    "/frameworks": ("tests/fixtures/frameworks.html", "text/html"),
    "/phase3": ("tests/fixtures/phase3.html", "text/html"),
    "/assets/react.js": ("node_modules/react/umd/react.development.js", "text/javascript"),
    "/assets/react-dom.js": ("node_modules/react-dom/umd/react-dom.development.js", "text/javascript"),
    "/assets/vue.js": ("node_modules/vue/dist/vue.global.prod.js", "text/javascript"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = ROUTES.get(urlsplit(self.path).path)
        if route is None:
            self.send_error(404)
            return
        try:
            content = (ROOT / route[0]).read_bytes()
        except OSError:
            self.send_error(503, "Run npm ci in browser-extension first")
            return
        self.send_response(200)
        self.send_header("Content-Type", route[1] + "; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_):
        pass  # URLs and entered data do not belong in logs.


if __name__ == "__main__":
    print("Manual fixtures: http://127.0.0.1:18766/ and /frameworks", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 18766), Handler).serve_forever()
