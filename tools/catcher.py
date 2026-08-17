"""Catch what the Mac actually received, and give the clicks somewhere safe.

Everything else in this repo can prove the Pi *sent* the right bytes. Nothing
could prove the Mac *acted* on them — that needs something running on the host,
and the host is deliberately meant to have nothing installed on it.

So: a page. It fills the screen, swallows every click and scroll that lands on
it, records **where** each one landed, and posts the log back here. Which means
a demo can be driven over the real radio with the clicks landing on a blank
page instead of on whatever happened to be under the cursor — and, more
usefully, that `calibrate.py` can ask "I sent this; what arrived?"

    python3 tools/catcher.py --open        # then fullscreen it and click once

Runs on the **Mac**, needs nothing but the standard library. Not part of the
device — it's a measuring instrument, which is why it lives in tools/ rather
than in handsfree/.

    GET  /          the page
    GET  /state     totals, sequence number, and the screen geometry
    GET  /events    every event since the last reset, with coordinates
    POST /reset     start clean

**It cannot tell your trackpad from the Pi.** Both are just input. Hands off
while a measurement runs; `calibrate.py` reports anything it can't account for.

Two things it can't see, both expected:

* `Ctrl+left/right` never reaches a browser — macOS takes it for Mission
  Control first, which is the entire reason swipes are mapped to it. Drive the
  demo with `--swipe-keys shift+left,shift+right` to watch the keyboard path.
* Coordinates are CSS pixels. On a Retina display that is half the device
  pixels, and `devicePixelRatio` is reported in /state so the maths can account
  for it.
"""

import argparse
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Keep the log bounded — a long run at 120 Hz would otherwise eat the heap.
MAX_EVENTS = 200_000

PAGE = """<!doctype html>
<meta charset="utf-8"><title>hands-free catcher</title>
<style>
  html,body{margin:0;height:100%;background:#111;color:#eee;
    font:14px ui-monospace,monospace;overflow:hidden}
  #pad{position:fixed;inset:0;cursor:crosshair}
  #hud{position:fixed;top:0;left:0;padding:14px;pointer-events:none;
    text-shadow:0 0 6px #000;line-height:1.6}
  b{color:#7ee787} i{color:#79c0ff;font-style:normal} u{color:#ffa657;
    text-decoration:none}
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

let s, pending, generation = -1;
function reset(gen){
  s = {moves:0, dist:0, downs:0, ups:0, wheel:0, hwheel:0, keys:[],
       x:0, y:0, minx:1e9, maxx:-1e9, miny:1e9, maxy:-1e9, last:null};
  pending = [];
  if (gen !== undefined) generation = gen;
  cx.clearRect(0, 0, cv.width, cv.height);
  paint();
}

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
  if (s) s.last = null;                    // don't join across the gap
}
addEventListener('resize', fit);

function geom(){
  return {sw: screen.width, sh: screen.height,
          iw: innerWidth, ih: innerHeight,
          dpr: devicePixelRatio || 1,
          full: !!document.fullscreenElement ||
                (innerHeight >= screen.height - 2),
          focus: document.hasFocus()};
}

function paint(){
  const g = geom();
  hud.innerHTML =
    `<b>hands-free catcher</b> - click here safely, press r to clear<br>`+
    `moves <i>${s.moves}</i>   travel <i>${Math.round(s.dist)}</i>px   `+
    `box <i>${s.maxx>s.minx?Math.round(s.maxx-s.minx):0}</i>x`+
    `<i>${s.maxy>s.miny?Math.round(s.maxy-s.miny):0}</i><br>`+
    `clicks down <i>${s.downs}</i> up <i>${s.ups}</i>   `+
    `wheel <i>${s.wheel}</i>   hwheel <i>${s.hwheel}</i><br>`+
    `keys <i>${s.keys.slice(-6).join(' ') || '-'}</i><br>`+
    `<span style="opacity:.6">at ${Math.round(s.x)},${Math.round(s.y)} - `+
    `viewport ${g.iw}x${g.ih} dpr ${g.dpr} - </span>`+
    (g.full ? `<u>fullscreen</u>` : `<u>NOT fullscreen</u>`)+
    (g.focus ? `` : ` <u>NOT focused</u>`);
}

function record(kind, x, y, v){
  pending.push({t: performance.now(), kind: kind,
                x: Math.round(x*100)/100, y: Math.round(y*100)/100, v: v});
  if (pending.length > 5000) pending.splice(0, pending.length - 5000);
}

reset(); fit();

addEventListener('mousemove', e => {
  if (s.last) {
    s.dist += Math.hypot(e.clientX-s.last[0], e.clientY-s.last[1]);
    cx.strokeStyle = 'rgba(126,231,135,.55)'; cx.lineWidth = 2;
    cx.beginPath(); cx.moveTo(s.last[0], s.last[1]);
    cx.lineTo(e.clientX, e.clientY); cx.stroke();
  }
  s.last = [e.clientX, e.clientY];
  s.moves++; s.x = e.clientX; s.y = e.clientY;
  s.minx = Math.min(s.minx, e.clientX); s.maxx = Math.max(s.maxx, e.clientX);
  s.miny = Math.min(s.miny, e.clientY); s.maxy = Math.max(s.maxy, e.clientY);
  dot.style.left = e.clientX+'px'; dot.style.top = e.clientY+'px';
  record('move', e.clientX, e.clientY, 0);
  paint();
});
addEventListener('mousedown', e => {
  s.downs++; record('down', e.clientX, e.clientY, e.button);
  e.preventDefault(); paint();
});
addEventListener('mouseup', e => {
  s.ups++; record('up', e.clientX, e.clientY, e.button);
  e.preventDefault(); paint();
});
addEventListener('wheel', e => {
  s.wheel += Math.sign(e.deltaY); s.hwheel += Math.sign(e.deltaX);
  record('wheel', e.clientX, e.clientY,
         [Math.sign(e.deltaY), Math.sign(e.deltaX), e.deltaY, e.deltaX]);
  e.preventDefault(); paint();
}, {passive:false});
addEventListener('keydown', e => {
  const name = (e.ctrlKey?'ctrl+':'')+(e.metaKey?'cmd+':'')+
               (e.shiftKey?'shift+':'')+(e.altKey?'alt+':'')+e.key;
  s.keys.push(name); record('key', s.x, s.y, name);
  if (e.key === 'r') reset(generation);
  e.preventDefault(); paint();
});
addEventListener('contextmenu', e => e.preventDefault());

// Push often while things are happening, so a synchronous measurement doesn't
// have to wait out a fixed interval every single time.
async function push(){
  const body = JSON.stringify({totals: s, geom: geom(),
                               events: pending, generation: generation});
  pending = [];
  try {
    const r = await fetch('/report', {method:'POST', body: body});
    const j = await r.json();
    if (j.generation !== generation) reset(j.generation);
  } catch (e) { /* server went away; keep going */ }
}
setInterval(push, 60);
</script>
""".encode("utf-8")


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.totals = {}
        self.geom = {}
        self.events = []
        self.seq = 0
        self.generation = 0
        self.seen = False

    def add(self, payload):
        with self.lock:
            self.seen = True
            self.totals = payload.get("totals", {})
            self.geom = payload.get("geom", {})
            # Events from a stale generation belong to the previous run.
            if payload.get("generation") == self.generation:
                self.events.extend(payload.get("events", []))
                if len(self.events) > MAX_EVENTS:
                    del self.events[:len(self.events) - MAX_EVENTS]
                self.seq += len(payload.get("events", []))
            return self.generation

    def snapshot(self):
        with self.lock:
            return {"seq": self.seq, "generation": self.generation,
                    "totals": dict(self.totals), "geom": dict(self.geom),
                    "count": len(self.events)}

    def log(self):
        with self.lock:
            return {"seq": self.seq, "generation": self.generation,
                    "geom": dict(self.geom), "events": list(self.events)}

    def reset(self):
        with self.lock:
            self.generation += 1
            self.events.clear()
            self.seq = 0
            self.totals = {}
            return self.generation


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        state = self.server.state
        if path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(PAGE)))
            self.end_headers()
            return self.wfile.write(PAGE)
        if path == "/state":
            return self._json(state.snapshot())
        if path == "/events":
            return self._json(state.log())
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]
        state = self.server.state
        if path == "/reset":
            return self._json({"generation": state.reset()})
        if path != "/report":
            return self.send_error(404)
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        self._json({"generation": state.add(payload)})

    def log_message(self, *args):
        pass


def serve(port):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.state = State()
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def summarise(totals, geom):
    if not totals:
        return "  nothing received"
    box = ""
    if totals.get("maxx", -1) > totals.get("minx", 0):
        box = (f"   box {round(totals['maxx'] - totals['minx'])}"
               f"x{round(totals.get('maxy', 0) - totals.get('miny', 0))}px")
    where = ""
    if geom:
        where = (f"\n  viewport {geom.get('iw')}x{geom.get('ih')} "
                 f"dpr {geom.get('dpr')}  "
                 f"{'fullscreen' if geom.get('full') else 'NOT fullscreen'}")
    return (f"  moves {totals.get('moves', 0)}   travel "
            f"{round(totals.get('dist', 0))}px{box}\n"
            f"  clicks {totals.get('downs', 0)} down / {totals.get('ups', 0)} "
            f"up   wheel {totals.get('wheel', 0)}   "
            f"hwheel {totals.get('hwheel', 0)}\n"
            f"  keys {' '.join(totals.get('keys', [])[-10:]) or '-'}{where}")


def main():
    ap = argparse.ArgumentParser(prog="python3 tools/catcher.py")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--seconds", type=float, default=1800.0)
    ap.add_argument("--open", action="store_true",
                    help="open it in the default browser")
    ap.add_argument("--out", default=None,
                    help="keep the latest totals in this file, as JSON")
    args = ap.parse_args()

    server = serve(args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"\n  catcher listening on {url}")
    if args.open:
        subprocess.run(["open", url])
    print("  Fullscreen it and click once, then keep your hands off the\n"
          "  trackpad while a measurement runs. Clicks on it do nothing.\n")

    start = time.monotonic()
    last = None
    try:
        while time.monotonic() - start < args.seconds:
            time.sleep(0.4)
            snap = server.state.snapshot()
            if args.out:
                with open(args.out, "w") as fh:
                    json.dump(snap, fh)
            if snap != last:
                t, g = snap["totals"], snap["geom"]
                print(f"\r  moves {t.get('moves', 0):5d}  "
                      f"travel {round(t.get('dist', 0)):6d}px  "
                      f"clicks {t.get('downs', 0)}/{t.get('ups', 0)}  "
                      f"wheel {t.get('wheel', 0):+4d}  "
                      f"events {snap['count']:6d}  "
                      f"{'full' if g.get('full') else 'WINDOWED'}   ",
                      end="", flush=True)
                last = snap
    except KeyboardInterrupt:
        pass

    print("\n\n  what the Mac actually received:")
    snap = server.state.snapshot()
    print(summarise(snap["totals"], snap["geom"]))
    server.shutdown()
    return 0 if server.state.seen else 1


if __name__ == "__main__":
    sys.exit(main())
