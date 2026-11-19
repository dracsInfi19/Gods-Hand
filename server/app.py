"""Gateway web server (standard library only).

Serves the dashboard and a small API over the gateway engine output:
  GET  /api/stream                      Server-Sent Events, one JSON frame per state change
  GET  /api/snapshot                    current frame
  GET  /api/registry                    device registry
  GET  /api/incidents                   incident status table
  POST /api/incidents/<node>/status     {"status": "acknowledged|dispatched|resolved|new"}
  POST /api/play {"playing": bool} | /api/replay | /api/seek {"i": int}

Data source: the simulator scenario, stepped in real time (replay mode).
"""
import contextlib
import functools
import io
import json
import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(ROOT, "dashboard")
STATUSES = {"new", "acknowledged", "dispatched", "resolved"}
TICK_S = 1.1


def load_frames():
    from sosmesh import simulator
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            simulator.main()
    finally:
        os.chdir(cwd)
    with open(os.path.join(DASH, "frames.json")) as f:
        return json.load(f)


class State:
    def __init__(self):
        self.cv = threading.Condition()
        self.frames = load_frames()
        self.idx, self.playing, self.version = 0, True, 0
        self.status = {}                      # node -> {"status", "ts"}

    def payload(self):
        fr = dict(self.frames[self.idx])
        fr["sos"] = [dict(q, status=self.status.get(q["node"], {}).get("status", "new"),
                          status_ts=self.status.get(q["node"], {}).get("ts"))
                     for q in fr["sos"]]
        return {"index": self.idx, "total": len(self.frames), "playing": self.playing,
                "source": "simulator-replay", "frame": fr}

    def bump(self):
        self.version += 1
        self.cv.notify_all()

    def tick_loop(self):
        while True:
            time.sleep(TICK_S)
            with self.cv:
                if self.playing:
                    self.idx = (self.idx + 1) % len(self.frames)
                    if self.idx == 0:
                        self.status.clear()
                    self.bump()


S = None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=DASH, **k)

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/stream":
            return self._stream()
        if p == "/api/snapshot":
            with S.cv:
                return self._json(S.payload())
        if p == "/api/registry":
            with open(os.path.join(DASH, "registry.json")) as f:
                return self._json(json.load(f))
        if p == "/api/incidents":
            with S.cv:
                return self._json(S.status)
        return super().do_GET()

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        seen = -1
        try:
            while True:
                with S.cv:
                    if S.version == seen:
                        S.cv.wait(timeout=2)
                    seen, data = S.version, json.dumps(S.payload())
                self.wfile.write(b"data: " + data.encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def do_POST(self):
        p = urlparse(self.path).path.strip("/").split("/")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "invalid JSON"}, 400)
        with S.cv:
            if p == ["api", "play"]:
                S.playing = bool(body.get("playing", True))
            elif p == ["api", "replay"]:
                S.idx, S.playing = 0, True
                S.status.clear()
            elif p == ["api", "seek"]:
                try:
                    S.idx = max(0, min(int(body.get("i", 0)), len(S.frames) - 1))
                except (TypeError, ValueError):
                    return self._json({"error": "invalid index"}, 400)
                S.playing = False
            elif len(p) == 4 and p[:2] == ["api", "incidents"] and p[3] == "status":
                st = body.get("status")
                if not p[2].isdigit() or st not in STATUSES:
                    return self._json({"error": "invalid node or status"}, 400)
                S.status[int(p[2])] = {"status": st, "ts": time.time()}
            else:
                return self._json({"error": "not found"}, 404)
            S.bump()
        self._json({"ok": True})


def main(port=8000):
    global S
    S = State()
    threading.Thread(target=S.tick_loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True
    print(f"Gods Hand dashboard: http://localhost:{port}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
