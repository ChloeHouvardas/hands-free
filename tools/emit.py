"""Send precise, known HID patterns. Runs on the **Pi**.

The measuring half of `calibrate.py`. It reads commands on stdin and acks each
one on stdout as JSON, so the orchestrator on the Mac can do the only thing
that gives a trustworthy number: send one known thing, wait, and read back what
arrived.

    sudo venv/bin/python tools/emit.py --transport bluetooth
    > mode absolute
    > goto 0.5 0.5
    > click

Long-lived on purpose. Bringing the Bluetooth transport up takes seconds and
re-establishes the link each time; keeping one connection open across a whole
calibration run is faster, and closer to how the device actually runs.

Everything here goes through `handsfree.hid` and `handsfree.transport`, so what
gets measured is what ships — there is no second implementation of a report to
drift out of sync.
"""

import argparse
import json
import math
import sys
import time

from handsfree import hid


class Emitter:
    def __init__(self, transport, pointer="relative", settle=0.35):
        self.wire = transport
        self.pointer = pointer
        self.settle = settle
        self.sent = 0

    # -- one report --------------------------------------------------------

    def report(self, buttons=0, x=0, y=0, wheel=0, pan=0):
        rid = self.wire.pointer_id(self.pointer) or self.wire.mouse_id
        self.wire.send_mouse(hid.mouse_report(
            buttons=buttons, x=x, y=y, wheel=wheel, pan=pan,
            report_id=rid, pointer=self.pointer))
        self.sent += 1

    def keys(self, modifiers=0, pressed=()):
        self.wire.send_keyboard(hid.keyboard_report(
            modifiers=modifiers, keys=pressed,
            report_id=self.wire.keyboard_id))
        self.sent += 1

    # -- patterns ----------------------------------------------------------

    def step(self, dx, dy, n=1, gap=0.02):
        """`n` relative reports of exactly (dx, dy). The gain probe."""
        for _ in range(n):
            self.report(x=int(dx), y=int(dy))
            time.sleep(gap)
        return {"reports": n, "dx": int(dx), "dy": int(dy)}

    def goto(self, fx, fy, dwell=0.12):
        """Absolute: put the pointer at these fractions of the screen."""
        x = int(round(_clamp01(fx) * hid.ABSOLUTE_MAX))
        y = int(round(_clamp01(fy) * hid.ABSOLUTE_MAX))
        was, self.pointer = self.pointer, "absolute"
        # Twice: some hosts treat the first absolute report after a mode change
        # as an arrival rather than a move.
        self.report(x=x, y=y)
        time.sleep(dwell)
        self.report(x=x, y=y)
        self.pointer = was
        return {"fx": fx, "fy": fy, "units": [x, y]}

    def square(self, side, gap=0.008):
        """A closed loop. Whatever else is true, it has to come back."""
        legs = [(1, 0), (0, 1), (-1, 0), (0, -1)]
        per = 20
        for dx, dy in legs:
            for _ in range(per):
                self.report(x=dx * side // per, y=dy * side // per)
                time.sleep(gap)
        return {"side": side, "legs": len(legs), "per_leg": per}

    def circle(self, radius, points=64, gap=0.008):
        last = (radius, 0)
        for i in range(1, points + 1):
            a = 2 * math.pi * i / points
            here = (radius * math.cos(a), radius * math.sin(a))
            self.report(x=int(round(here[0] - last[0])),
                        y=int(round(here[1] - last[1])))
            last = here
            time.sleep(gap)
        return {"radius": radius, "points": points}

    def click(self, n=1, gap=0.12):
        for _ in range(n):
            self.report(buttons=1)
            time.sleep(0.05)
            self.report(buttons=0)
            time.sleep(gap)
        return {"clicks": n}

    def scroll(self, ticks, gap=0.06):
        step = 1 if ticks > 0 else -1
        for _ in range(abs(int(ticks))):
            self.report(wheel=step)
            time.sleep(gap)
        return {"ticks": int(ticks)}

    def combo(self, spec):
        modifiers, key = hid.combo(spec)
        self.keys(modifiers, [key])
        time.sleep(0.05)
        self.keys(0, ())
        return {"combo": spec, "modifiers": modifiers, "key": key}

    def rest(self):
        self.report()
        return {"rest": True}


def _clamp01(v):
    v = float(v)
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def run(emitter, line):
    """One command. Returns the ack payload."""
    parts = line.split()
    if not parts:
        return None
    verb, args = parts[0], parts[1:]

    if verb == "mode":
        if args[0] not in hid.POINTERS:
            raise ValueError(f"mode must be one of {hid.POINTERS}")
        emitter.pointer = args[0]
        return {"mode": args[0]}
    if verb == "step":
        return emitter.step(float(args[0]), float(args[1]),
                            int(args[2]) if len(args) > 2 else 1,
                            float(args[3]) if len(args) > 3 else 0.02)
    if verb == "goto":
        return emitter.goto(float(args[0]), float(args[1]))
    if verb == "square":
        return emitter.square(int(args[0]))
    if verb == "circle":
        return emitter.circle(int(args[0]))
    if verb == "click":
        return emitter.click(int(args[0]) if args else 1)
    if verb == "scroll":
        return emitter.scroll(int(args[0]))
    if verb == "keys":
        return emitter.combo(args[0])
    if verb == "rest":
        return emitter.rest()
    if verb == "sleep":
        time.sleep(float(args[0]))
        return {"slept": float(args[0])}
    raise ValueError(f"unknown command {verb!r}")


def main():
    ap = argparse.ArgumentParser(prog="sudo venv/bin/python tools/emit.py")
    ap.add_argument("--transport", default="bluetooth")
    ap.add_argument("--pointer", default="relative", choices=hid.POINTERS)
    args = ap.parse_args()

    from handsfree import transport as transports
    from handsfree.config import load_config

    cfg = load_config().get("hid", {})
    wire = transports.open(args.transport, cfg, **(
        {"verbose": False} if args.transport == "bluetooth" else {}))
    if hasattr(wire, "wait_for_host") and not wire.wait_for_host(40):
        print(json.dumps({"ok": False, "error": "no host connected"}),
              flush=True)
        wire.close()
        return 1

    emitter = Emitter(wire, args.pointer)
    print(json.dumps({"ok": True, "ready": True,
                      "peer": getattr(wire, "peer", None),
                      "absolute_max": hid.ABSOLUTE_MAX}), flush=True)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line == "quit":
                break
            try:
                payload = run(emitter, line)
                print(json.dumps({"ok": True, "cmd": line,
                                  "sent": emitter.sent, **(payload or {})}),
                      flush=True)
            except Exception as e:
                print(json.dumps({"ok": False, "cmd": line,
                                  "error": f"{type(e).__name__}: {e}"}),
                      flush=True)
    finally:
        try:
            emitter.report()          # neutral, so nothing is left held
        except Exception:
            pass
        wire.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
