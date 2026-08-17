"""Stream annotated frames to a browser as MJPEG.

The Pi is headless over SSH, and Pi Connect's screen share costs real CPU on a
2GB board — which would skew the very measurements we're recording. This streams
frames over HTTP instead, so you can watch from your laptop while the session
runs in a terminal.

    open http://chloespie.local:8080

Frames are only encoded while a browser is actually connected, so leaving this
enabled with nobody watching costs nothing.
"""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2


def lan_ip():
    """Best-effort local IP, so we can print a URL that actually works.

    Doesn't send anything — opening a UDP socket is just how you ask the
    routing table which interface would be used to get out.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return socket.gethostname()
    finally:
        s.close()


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>hands-free</title>
<style>
  body { margin:0; background:#111; color:#eee; font:14px system-ui;
         display:flex; flex-direction:column; align-items:center; gap:12px;
         padding:16px; }
  img { max-width:100%; border-radius:8px; }
</style>
<h3>hands-free &mdash; live view</h3>
<img src="/stream">
""".encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            self.wfile.write(PAGE)
            return

        if self.path != "/stream":
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header(
            "Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
        self.end_headers()

        state = self.server.state
        with state.lock:
            state.clients += 1
        try:
            last = None
            while True:
                with state.lock:
                    while state.seq == last:
                        state.changed.wait(timeout=5)
                    last, jpeg = state.seq, state.jpeg
                if jpeg is None:
                    continue
                self.wfile.write(b"--FRAME\r\n")
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(jpeg)))
                self.end_headers()
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with state.lock:
                state.clients -= 1

    def log_message(self, *args):
        pass  # don't spam the terminal the prompts are in


class _State:
    def __init__(self):
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.jpeg = None
        self.seq = 0
        self.clients = 0


class Preview:
    def __init__(self, port=8080, quality=70):
        self.port = port
        self.quality = quality
        self.state = _State()
        self.server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
        self.server.state = self.state
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def watching(self):
        with self.state.lock:
            return self.state.clients > 0

    def banner(self):
        """Where to point a browser. Print this or nobody will find it.

        A tab left open from a previous run won't reconnect by itself — the
        image stream died with the old process — so it needs a reload, not
        just switching back to it.
        """
        return (f"\n  live view:  http://{lan_ip()}:{self.port}"
                f"\n              http://{socket.gethostname()}.local:{self.port}"
                f"\n  (if a tab is already open from an earlier run, reload it)\n")

    def update(self, frame):
        """Encode and publish a frame. No-op when nobody is watching."""
        if not self.watching:
            return
        ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
        if not ok:
            return
        with self.state.lock:
            self.state.jpeg = buf.tobytes()
            self.state.seq += 1
            self.state.changed.notify_all()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
