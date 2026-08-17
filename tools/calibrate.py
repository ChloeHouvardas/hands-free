"""Measure whether the Pi's signals become *accurate* Mac cursor movement.

Runs on the **Mac**. Drives `tools/emit.py` on the Pi over one long-lived SSH
session, reads what actually arrived from `tools/catcher.py`, and reports the
difference.

    python3 tools/catcher.py --open        # fullscreen it, click once
    python3 tools/calibrate.py             # then hands off

The question it exists to answer is not "did anything happen" — that was
settled — but "when the hand goes there, does the cursor go there". Two things
make that non-trivial:

* **macOS accelerates relative pointer input.** N units is not N pixels, and
  the ratio depends on speed. Good for a mouse, wrong for a hand tracker, and
  not something a device can switch off without installing software on the
  host — which this project exists not to do.
* **Absolute mode should sidestep it entirely** and has never been tested
  against a real Mac. Both live in one descriptor now, on different report IDs,
  so this can measure them back to back without re-pairing.

Every measurement is synchronous: reset the catcher, send one known thing, wait
for quiet, read. Correlating two free-running clocks across SSH would add error
for no benefit.

**It cannot tell your trackpad from the Pi.** Stray input shows up as movement
the pattern can't explain, and gets flagged rather than silently averaged in.
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request

CATCHER = "http://127.0.0.1:8770"
PI = "chloewashere@192.168.2.188"
REPO = "~/Code/hands-free"


# -- talking to the catcher -------------------------------------------------

def get(path):
    with urllib.request.urlopen(CATCHER + path, timeout=5) as r:
        return json.loads(r.read())


def post(path, payload=b""):
    req = urllib.request.Request(CATCHER + path, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def quiet(settle=0.45, timeout=4.0):
    """Wait until the catcher stops seeing new events, then return the log."""
    deadline = time.monotonic() + timeout
    last, stable = -1, 0.0
    while time.monotonic() < deadline:
        snap = get("/state")
        if snap["seq"] == last:
            stable += 0.1
            if stable >= settle:
                break
        else:
            last, stable = snap["seq"], 0.0
        time.sleep(0.1)
    return get("/events")


def moves(log):
    return [e for e in log["events"] if e["kind"] == "move"]


def displacement(log):
    """Net cursor movement over a log, in CSS pixels."""
    m = moves(log)
    if len(m) < 2:
        return (0.0, 0.0)
    return (m[-1]["x"] - m[0]["x"], m[-1]["y"] - m[0]["y"])


# -- talking to the Pi ------------------------------------------------------

class Pi:
    """One SSH session running emit.py, spoken to a line at a time."""

    def __init__(self, transport="bluetooth"):
        self.proc = subprocess.Popen(
            ["ssh", "-tt", PI,
             f"cd {REPO} && sudo venv/bin/python -u tools/emit.py "
             f"--transport {transport}"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)
        hello = self._read()
        if not hello or not hello.get("ready"):
            raise SystemExit(f"the emitter never came up: {hello}")
        self.peer = hello.get("peer")

    def _read(self):
        while True:
            line = self.proc.stdout.readline()
            if not line:
                return None
            line = line.strip()
            if line.startswith("{"):
                try:
                    return json.loads(line)
                except ValueError:
                    continue

    def send(self, command):
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()
        return self._read()

    def close(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=8)
        except Exception:
            self.proc.kill()


def measure(pi, command, settle=0.45):
    """Reset, send, wait, read. The whole protocol."""
    post("/reset")
    time.sleep(0.15)
    ack = pi.send(command)
    log = quiet(settle=settle)
    return ack, log


# -- the measurements -------------------------------------------------------

def check_setup():
    try:
        snap = get("/state")
    except (urllib.error.URLError, OSError):
        raise SystemExit(
            "no catcher on 127.0.0.1:8770 — start it first:\n"
            "  python3 tools/catcher.py --open")
    geom = snap.get("geom") or {}
    if not geom:
        raise SystemExit("the catcher is up but no page is connected — open "
                         f"{CATCHER} in a browser")
    if not geom.get("full"):
        print("  WARNING: the page is not fullscreen. Absolute-mode maths "
              "assumes the page covers the screen, and clicks may land "
              "outside it.\n")
    if not geom.get("focus"):
        print("  WARNING: the page is not focused — key events won't be seen.\n")
    return geom


def gain_curve(pi, geom, results):
    """Displacement per unit, for a range of step sizes. Flat means linear."""
    print("\n  relative gain — is N units N pixels?")
    print(f"    {'units':>6} {'dx px':>8} {'px/unit':>9}")
    pi.send("mode relative")
    rows = []
    for units in (1, 2, 4, 8, 16, 32, 64):
        # 20 discrete steps, spaced, so acceleration sees each as its own move.
        _ack, log = measure(pi, f"step {units} 0 20 0.05")
        dx, _dy = displacement(log)
        per = dx / (units * 20) if units else 0
        rows.append({"units": units, "dx": dx, "per_unit": per,
                     "moves": len(moves(log))})
        print(f"    {units:>6} {dx:>8.1f} {per:>9.3f}")
    results["gain_curve"] = rows
    ratios = [r["per_unit"] for r in rows if r["per_unit"]]
    if len(ratios) >= 2 and min(ratios) > 0:
        spread = max(ratios) / min(ratios)
        results["gain_spread"] = spread
        print(f"    spread {spread:.2f}x  "
              f"({'linear' if spread < 1.15 else 'ACCELERATED'})")


def absolute_grid(pi, geom, results):
    """Ask for a position; measure where it landed."""
    print("\n  absolute accuracy — does it land where it was told?")
    print(f"    {'asked':>12} {'landed':>14} {'error px':>12}")
    pi.send("mode absolute")
    w, h = geom.get("iw", 1), geom.get("ih", 1)
    rows = []
    for fx, fy in ((0.25, 0.25), (0.75, 0.25), (0.5, 0.5),
                   (0.25, 0.75), (0.75, 0.75), (0.1, 0.9), (0.9, 0.1)):
        _ack, log = measure(pi, f"goto {fx} {fy}")
        m = moves(log)
        if not m:
            print(f"    {fx:>5.2f},{fy:<6.2f} {'nothing arrived':>14}")
            rows.append({"fx": fx, "fy": fy, "landed": None})
            continue
        got = (m[-1]["x"], m[-1]["y"])
        want = (fx * w, fy * h)
        err = (got[0] - want[0], got[1] - want[1])
        rows.append({"fx": fx, "fy": fy, "want": want, "got": got, "err": err})
        print(f"    {fx:>5.2f},{fy:<6.2f} {got[0]:>6.0f},{got[1]:<7.0f} "
              f"{err[0]:>+6.0f},{err[1]:<+6.0f}")
    good = [r for r in rows if r.get("err")]
    if good:
        worst = max(max(abs(r["err"][0]), abs(r["err"][1])) for r in good)
        results["absolute_worst_px"] = worst
        print(f"    worst error {worst:.0f}px "
              f"({100 * worst / max(w, h):.1f}% of the screen)")
    results["absolute_grid"] = rows


def closure(pi, results):
    """A closed path has to close. Relative won't; absolute must."""
    print("\n  closed-path drift — does a square come back?")
    for mode in ("relative", "absolute"):
        if mode == "absolute":
            # Walk the corners absolutely and return to the first.
            pi.send("mode absolute")
            post("/reset")
            time.sleep(0.15)
            for fx, fy in ((0.3, 0.3), (0.7, 0.3), (0.7, 0.7),
                           (0.3, 0.7), (0.3, 0.3)):
                pi.send(f"goto {fx} {fy}")
            log = quiet()
        else:
            pi.send("mode relative")
            _ack, log = measure(pi, "square 400", settle=0.6)
        dx, dy = displacement(log)
        drift = (dx ** 2 + dy ** 2) ** 0.5
        results[f"closure_{mode}"] = drift
        print(f"    {mode:>9}: drift {drift:6.1f}px  "
              f"({'closes' if drift < 5 else 'DOES NOT CLOSE'})")


def discrete(pi, geom, results):
    """Clicks, scroll and keys — counts and signs, not distances."""
    print("\n  clicks, scroll and keys")
    pi.send("mode absolute")
    pi.send("goto 0.5 0.5")
    time.sleep(0.3)

    _ack, log = measure(pi, "click 5", settle=0.6)
    downs = [e for e in log["events"] if e["kind"] == "down"]
    ups = [e for e in log["events"] if e["kind"] == "up"]
    drift = 0.0
    if downs and ups:
        drift = max(abs(d["x"] - downs[0]["x"]) + abs(d["y"] - downs[0]["y"])
                    for d in downs + ups)
    results["clicks"] = {"sent": 5, "down": len(downs), "up": len(ups),
                         "drift": drift}
    print(f"    clicks: sent 5, got {len(downs)} down / {len(ups)} up, "
          f"cursor moved {drift:.0f}px during them")

    for ticks in (5, -5):
        _ack, log = measure(pi, f"scroll {ticks}", settle=0.7)
        wheels = [e["v"][0] for e in log["events"] if e["kind"] == "wheel"]
        results.setdefault("scroll", []).append(
            {"sent": ticks, "events": len(wheels), "sum": sum(wheels)})
        print(f"    scroll {ticks:+d}: {len(wheels)} events, "
              f"net sign {sum(wheels):+d}")

    for spec in ("shift+left", "shift+right"):
        _ack, log = measure(pi, f"keys {spec}", settle=0.6)
        got = [e["v"] for e in log["events"] if e["kind"] == "key"]
        results.setdefault("keys", []).append({"sent": spec, "got": got})
        print(f"    keys {spec}: {got or 'nothing arrived'}")


def script_path(pi, geom, results, name="move"):
    """The real thing: a synth script over the real radio, and the shape of
    the path it traced."""
    print(f"\n  end to end — the '{name}' script over the radio")
    post("/reset")
    time.sleep(0.15)
    out = subprocess.run(
        ["ssh", PI, f"cd {REPO} && sudo timeout 60 venv/bin/python -u "
                    f"-m handsfree run --source synth:{name} --no-preview "
                    f"--swipe-keys shift+left,shift+right"],
        capture_output=True, text=True, timeout=120)
    log = quiet(settle=1.0, timeout=8.0)
    m = moves(log)
    if not m:
        print("    nothing arrived")
        results["script"] = {"name": name, "moves": 0}
        return
    xs = [p["x"] for p in m]
    ys = [p["y"] for p in m]
    span = (max(xs) - min(xs), max(ys) - min(ys))
    results["script"] = {
        "name": name, "moves": len(m), "span": span,
        "clicks": sum(1 for e in log["events"] if e["kind"] == "down"),
        "events": [ln for ln in out.stdout.splitlines() if "s  " in ln][:12],
    }
    print(f"    {len(m)} moves, span {span[0]:.0f}x{span[1]:.0f}px, "
          f"{results['script']['clicks']} click(s)")
    for line in results["script"]["events"][:6]:
        print(f"      {line.strip()}")


def main():
    ap = argparse.ArgumentParser(prog="python3 tools/calibrate.py")
    ap.add_argument("--out", default="/tmp/calibration.json")
    ap.add_argument("--only", default=None,
                    help="gain | absolute | closure | discrete | script")
    ap.add_argument("--script", default="move")
    args = ap.parse_args()

    geom = check_setup()
    print(f"  screen {geom.get('sw')}x{geom.get('sh')}  "
          f"viewport {geom.get('iw')}x{geom.get('ih')}  "
          f"dpr {geom.get('dpr')}  "
          f"{'fullscreen' if geom.get('full') else 'WINDOWED'}")

    pi = Pi()
    print(f"  emitter up, host {pi.peer}")
    results = {"geom": geom, "peer": pi.peer, "when": time.time()}
    try:
        if args.only in (None, "gain"):
            gain_curve(pi, geom, results)
        if args.only in (None, "absolute"):
            absolute_grid(pi, geom, results)
        if args.only in (None, "closure"):
            closure(pi, results)
        if args.only in (None, "discrete"):
            discrete(pi, geom, results)
    finally:
        pi.close()

    if args.only in (None, "script"):
        script_path(None, geom, results, args.script)

    with open(args.out, "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"\n  raw numbers written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
