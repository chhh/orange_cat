"""Debug listener: log every incoming HTTP request to screen + file.

Run where the real server would be (stop it first):
  uv run snoop.py

Binds 0.0.0.0:8080, answers 200 to everything, prints + appends to
frames/snoop.log. Purely to see whether HA actually posts.
"""

import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG = "frames/snoop.log"


class Handler(BaseHTTPRequestHandler):
    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        line = (f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]} "
                f"{self.command} {self.path} body={body[:200]!r}")
        print(line, flush=True)
        with open(LOG, "a") as fh:
            fh.write(line + "\n")
            for k, v in self.headers.items():
                fh.write(f"  {k}: {v}\n")
            fh.write("\n")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle

    def log_message(self, *args):
        pass  # silence the default access log; we print our own


if __name__ == "__main__":
    os.makedirs("frames", exist_ok=True)
    print(f"listening on 0.0.0.0:8080, logging to {LOG}", flush=True)
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()