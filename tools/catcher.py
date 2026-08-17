"""Catch what the Mac actually received, and give the clicks somewhere safe.

Everything else in this repo can prove the Pi *sent* the right bytes. Nothing
could prove the Mac *acted* on them — that needs something running on the host,
and the host is deliberately meant to have nothing installed on it.

So: a page. It fills the window, swallows every click and scroll that lands on
it, records what arrived, and posts a summary back here. Which means a demo can
be run over the real radio with the clicks landing on a blank page instead of
on whatever happened to be under the cursor.

    python3 tools/catcher.py            # then open the URL it prints

Runs on the **Mac**, needs nothing but the standard library. Not part of the
device — it's a measuring instrument, which is why it lives in tools/ rather
than in handsfree/.

Two things it can't see, both expected:

* `Ctrl+←/→` never reaches a browser — macOS takes it for Mission Control
  first, which is the entire reason swipes are mapped to it. Run the demo with
  `--swipe-keys shift+left,shift+right` to watch the keyboard path instead.
* Absolute pointer mode moves the system cursor without the page necessarily
  seeing a `mousemove` until it enters the window.
"""

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!doctype html>
<meta charset="utf-8"><title>hands-free catcher</title>
<style>
  html,body{margin:0;height:100%;background:#111;color:#eee;
    font:14px ui-monospace,monospace;overflow:hidden}
  #pad{position:fixed;inset:0;cursor:crosshair}
  #hud{position:fixed;top:0;left:0;padding:14px;pointer-events:none;
    text-shadow:0 0 6px #000;line-height:1.6}
  b{color:#7ee787} i{color:#79c0ff;font-style:normal}
  #dot{position:fixed;width:14px;height:14px;margin:-7px 0 0 -7px;
    border:2px solid #ff7b72;border-radius:50%;pointer-events:none}
  #trail{position:fixed;top:0;left:0;width:100vw;height:100vh;
    pointer-events:none}
</style>
<div id="pad"></div><canvas id="trail"></canvas><div id="dot"></div>
<div id="hud"></div>
<script>
const hud = document.getElementById('hud'), dot = document.getElementById('dot');
const cv = document.getElementById('trail'), cx = cv.getContext('2d');
// Size the *bitmap*, and keep sizing it. A positioned canvas with auto width
// keeps its intrinsic bitmap size rather than stretching, so a bitmap fixed at
// load time becomes a small drawable rectangle in the corner the moment the
// window is resized or made fullscreen — everything outside it is clipped, and
// the trail looks broken while the numbers are perfectly correct.
function fit(){
  const dpr = devicePixelRatio || 1;
  cv.width = Math.round(innerWidth * dpr);
  cv.height = Math.round(innerHeight * dpr);
  cx.setTransform(dpr, 0, 0, dpr, 0, 0);   // draw in CSS pixels
  s.last = null;                            // don't join across the gap
}
addEventListener('resize', fit);
let s = {moves:0, dist:0, downs:0, ups:0, wheel:0, hwheel:0, keys:[],
         x:0, y:0, minx:1e9, maxx:-1e9, miny:1e9, maxy:-1e9, last:null};
function reset(){
  s = {moves:0, dist:0, downs:0, ups:0, wheel:0, hwheel:0, keys:[],
       x:0, y:0, minx:1e9, maxx:-1e9, miny:1e9, maxy:-1e9, last:null};
  cx.clearRect(0, 0, cv.width, cv.height); paint();
}

function paint(){
  hud.innerHTML =
    `<b>hands-free catcher</b> — click here safely · press r to clear<br>`+
    `moves <i>${s.moves}</i>   travel <i>${Math.round(s.dist)}</i>px   `+
    `box <i>${s.maxx>s.minx?Math.round(s.maxx-s.minx):0}</i>x`+
    `<i>${s.maxy>s.miny?Math.round(s.maxy-s.miny):0}</i><br>`+
    `clicks down <i>${s.downs}</i> up <i>${s.ups}</i>   `+
    `wheel <i>${s.wheel}</i>   hwheel <i>${s.hwheel}</i><br>`+
    `keys <i>${s.keys.slice(-8).join(' ') || '-'}</i><br>`+
    `<span style="opacity:.6">at ${Math.round(s.x)},${Math.round(s.y)}</span>`;
}
addEventListener('mousemove', e => {
  if (s.last) {
    const d = Math.hypot(e.clientX-s.last[0], e.clientY-s.last[1]);
    s.dist += d;
    cx.strokeStyle = 'rgba(126,231,135,.55)'; cx.lineWidth = 2;
    cx.beginPath(); cx.moveTo(s.last[0], s.last[1]);
    cx.lineTo(e.clientX, e.clientY); cx.stroke();
  }
  s.last = [e.clientX, e.clientY];
  s.moves++; s.x = e.clientX; s.y = e.clientY;
  s.minx = Math.min(s.minx, e.clientX); s.maxx = Math.max(s.maxx, e.clientX);
  s.miny = Math.min(s.miny, e.clientY); s.maxy = Math.max(s.maxy, e.clientY);
  dot.style.left = e.clientX+'px'; dot.style.top = e.clientY+'px';
  paint();
});
addEventListener('mousedown', e => { s.downs++; e.preventDefault(); paint(); });
addEventListener('mouseup',   e => { s.ups++;   e.preventDefault(); paint(); });
addEventListener('wheel', e => {
  s.wheel += Math.sign(e.deltaY); s.hwheel += Math.sign(e.deltaX);
  e.preventDefault(); paint();
}, {passive:false});
addEventListener('keydown', e => {
  const name = (e.ctrlKey?'ctrl+':'')+(e.metaKey?'cmd+':'')+
               (e.shiftKey?'shift+':'')+(e.altKey?'alt+':'')+e.key;
  s.keys.push(name); e.preventDefault(); paint();
});
addEventListener('contextmenu', e => e.preventDefault());
// Press r to clear, so a run starts from a known-empty slate.
addEventListener('keydown', e => { if (e.key === 'r') reset(); });
setInterval(() => {
  fetch('/report', {method:'POST', body:JSON.stringify(s)}).catch(()=>{});
}, 500);
fit(); paint();
</script>
""".encode("utf-8")


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest = {}
        self.seen = False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.split("?")[0] != "/":
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            data = {}
        with self.server.state.lock:
            self.server.state.latest = data
            self.server.state.seen = True
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def summarise(s):
    if not s:
        return "  nothing received"
    box = ""
    if s.get("maxx", -1) > s.get("minx", 0):
        box = (f"   box {round(s['maxx'] - s['minx'])}"
               f"x{round(s.get('maxy', 0) - s.get('miny', 0))}px")
    return (f"  moves {s.get('moves', 0)}   travel "
            f"{round(s.get('dist', 0))}px{box}\n"
            f"  clicks {s.get('downs', 0)} down / {s.get('ups', 0)} up   "
            f"wheel {s.get('wheel', 0)}   hwheel {s.get('hwheel', 0)}\n"
            f"  keys {' '.join(s.get('keys', [])[-10:]) or '-'}")


def main():
    ap = argparse.ArgumentParser(prog="python3 tools/catcher.py")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--seconds", type=float, default=180.0,
                    help="how long to keep catching before printing the total")
    ap.add_argument("--open", action="store_true",
                    help="open it in the default browser")
    ap.add_argument("--out", default=None,
                    help="keep the latest totals in this file, as JSON, so "
                         "another process can read them while it runs")
    args = ap.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.state = State()
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  catcher listening on {url}")
    if args.open:
        import subprocess
        subprocess.run(["open", url])
    print("  Open that, click once on the page so it has keyboard focus,\n"
          "  and leave it frontmost. Clicks that land on it do nothing.\n")

    start = time.monotonic()
    last = None
    try:
        while time.monotonic() - start < args.seconds:
            time.sleep(0.5)
            with server.state.lock:
                now = dict(server.state.latest)
            if args.out:
                with open(args.out, "w") as fh:
                    json.dump(now, fh)
            if now and now != last:
                print(f"\r  moves {now.get('moves', 0):5d}  "
                      f"travel {round(now.get('dist', 0)):6d}px  "
                      f"clicks {now.get('downs', 0)}/{now.get('ups', 0)}  "
                      f"wheel {now.get('wheel', 0):+4d}  "
                      f"keys {len(now.get('keys', []))}   ",
                      end="", flush=True)
                last = now
    except KeyboardInterrupt:
        pass

    print("\n\n  what the Mac actually received:")
    with server.state.lock:
        print(summarise(server.state.latest))
        got = server.state.seen
    server.shutdown()
    return 0 if got else 1


if __name__ == "__main__":
    sys.exit(main())
