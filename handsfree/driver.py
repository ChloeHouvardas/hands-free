"""Gesture events to input reports, and the thread that smooths them out.

    engine.update() -> [Event] -> Driver.handle() -> Transport -> the Mac

Stdlib only, and the transport is injected, so the whole thing is testable on a
laptop against `transport.null` — which matters more here than anywhere else in
the project, because the failure this code has to not have is a **mouse button
left held down**, and that makes a Mac unusable rather than merely wrong.

Two jobs.

**The interpolation thread.** The vision loop produces a new cursor target
about nine times a second. Sending those straight through gives a cursor that
arrives in 110 ms jumps, which reads as lag even though the latency is the same
as it would be otherwise. So a thread runs at `interpolate_hz` and eases toward
the target, turning one jump into a stream of small deltas. It can't remove
latency; it hides the staircase, which is most of what "laggy" actually means.
The engine keeps its cursor in normalized screen space specifically so this can
sit in front of it.

**Never leaving the button down.** The engine already routes every state change
through one place that releases it, and `recordings/drag.jsonl` still shows a
thumb that never fully lets go — a pinch ratio sitting at 0.3 for twenty
seconds instead of returning to 1.5. Upstream that means a missed drag. Down
here it would mean a Mac you have to reboot. So there are four independent
guards, and none of them trusts the others:

* `close()` releases, and is idempotent, and runs from `finally`
* SIGINT/SIGTERM release before the process goes away
* a **watchdog** releases if the vision loop stops feeding us at all
* a **ceiling** releases after `max_drag` seconds no matter what is happening

The watchdog is the one that catches a hung MediaPipe; the ceiling is the one
that catches a gesture layer that thinks you're still pinching.
"""

import atexit
import math
import signal
import sys
import threading
import time

from handsfree import hid

DEFAULTS = {
    "pointer": "relative",
    "speed": 1400.0,
    "max_speed": 2600.0,
    "ease": 0.045,
    "interpolate_hz": 120.0,
    "watchdog": 1.0,
    "max_drag": 6.0,
    "scroll": 1.0,
    "swipe_left": "ctrl+left",
    "swipe_right": "ctrl+right",
}


class Driver:
    """Consumes gesture events, drives a transport.

    `clock` is injectable so tests can step time rather than sleep through it.
    Pass `thread=False` and call `pump()` by hand to run it deterministically.
    """

    def __init__(self, transport, cfg=None, clock=time.monotonic, thread=True):
        self.transport = transport
        self.cfg = dict(DEFAULTS, **(cfg or {}))
        self.clock = clock

        self.pointer = self.cfg["pointer"]
        if self.pointer not in hid.POINTERS:
            raise ValueError(f"[hid] pointer must be one of {hid.POINTERS}, "
                             f"not {self.pointer!r}")

        self._lock = threading.RLock()
        self._closed = False
        self._thread = None

        # Normalized screen space, matching the engine's own cursor.
        self.pos = [0.5, 0.5]
        self.target = [0.5, 0.5]
        self._residue = [0.0, 0.0]      # sub-pixel carry, so slow drift counts
        self._last_absolute = None
        self._armed = False             # seen a cursor event yet?

        self.buttons = 0
        self.drag_since = None
        self.released_by = None         # which guard fired, for the log
        self.thread_stuck = False       # set if the cursor thread outlived close()

        now = clock()
        self._fed = now
        self._pumped = now

        atexit.register(self.close)
        if thread:
            self.start()

    # -- lifecycle ---------------------------------------------------------

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="cursor")
        self._thread.start()

    def _loop(self):
        period = 1.0 / max(1.0, float(self.cfg["interpolate_hz"]))
        while not self._closed:
            self.pump()
            time.sleep(period)

    def install_signal_guards(self):
        """Release the button before the process dies, then carry on dying."""
        def handler(signum, frame):
            self.close()
            if signum == signal.SIGINT:
                raise KeyboardInterrupt
            raise SystemExit(128 + signum)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, handler)
            except ValueError:
                pass                    # not on the main thread; caller's problem

    def close(self):
        """Let go of everything. Safe to call twice, and it will be."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.buttons:
                self.buttons = 0
                self.released_by = self.released_by or "close"
            # A neutral report on the way out, so the host can't be left
            # holding a button we stopped talking about.
            self._mouse()
            self._keys(0, ())

        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
            # join() returns None whether it worked or timed out, so without
            # this check a wedged loop looks exactly like a clean shutdown —
            # in the one module whose whole promise is guards that don't trust
            # each other. The button is already released above, so this is a
            # leak to report rather than a danger to prevent.
            if self._thread.is_alive():
                self.thread_stuck = True
                print("warning: the cursor thread did not stop within 1s",
                      file=sys.stderr, flush=True)

        self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- input -------------------------------------------------------------

    def handle(self, events):
        """Consume one frame's worth of events from the engine."""
        with self._lock:
            self._fed = self.clock()
            for event in events:
                name = event.name if hasattr(event, "name") else event[0]
                value = event.value if hasattr(event, "value") else event[1]

                if name == "cursor":
                    self.target = [float(value[0]), float(value[1])]
                    self._armed = True
                elif name == "click_down":
                    self._button(1)
                elif name in ("click_up", "hand_lost"):
                    # hand_lost can't normally arrive with the button still
                    # down — the engine releases first — but this costs nothing
                    # and it's the exact case that ruins a Mac.
                    self._button(0)
                elif name == "scroll":
                    self._scroll(int(value))
                elif name == "swipe":
                    self._swipe(str(value))

    def pump(self):
        """One interpolation step. Called by the thread, or by tests."""
        with self._lock:
            if self._closed:
                return
            now = self.clock()
            dt = max(0.0, now - self._pumped)
            self._pumped = now

            self._guard(now)
            if not self._armed:
                return
            if self.pointer == "absolute":
                self._pump_absolute(dt)
            else:
                self._pump_relative(dt)

    # -- the safety net ----------------------------------------------------

    def _guard(self, now):
        if not self.buttons:
            return

        ceiling = float(self.cfg["max_drag"])
        # `is not None`, not truthiness — a drag that began at clock zero is a
        # perfectly good drag, and testing it for truth silently disables the
        # ceiling for it. Which is the one guard you least want to be optional.
        if ceiling and self.drag_since is not None and \
                now - self.drag_since > ceiling:
            return self._release("max_drag")

        watchdog = float(self.cfg["watchdog"])
        if watchdog and now - self._fed > watchdog:
            return self._release("watchdog")

        if not self.transport.connected:
            # Nothing we send lands anyway; drop the logical state so a
            # reconnect doesn't inherit a held button.
            self.buttons = 0
            self.drag_since = None
            self.released_by = "disconnect"

    def _release(self, why):
        self.buttons = 0
        self.drag_since = None
        self.released_by = why
        self._mouse()

    # -- movement ----------------------------------------------------------

    def _ease(self, dt):
        """Fraction of the remaining distance to cover this tick.

        Exponential rather than linear, so a target that stops being updated
        settles instead of overshooting, and the response doesn't depend on the
        tick rate happening to be what we asked for.
        """
        tau = float(self.cfg["ease"])
        if tau <= 0 or dt <= 0:
            return 1.0
        return 1.0 - math.exp(-dt / tau)

    def _pump_relative(self, dt):
        alpha = self._ease(dt)
        speed = float(self.cfg["speed"])
        moved = []
        for axis in (0, 1):
            step_n = (self.target[axis] - self.pos[axis]) * alpha
            self.pos[axis] += step_n
            whole, self._residue[axis] = self._quantize(
                step_n * speed + self._residue[axis], dt)
            moved.append(whole)

        if moved[0] or moved[1]:
            self._mouse(x=moved[0], y=moved[1])

    def _quantize(self, value, dt):
        """Split into whole units and a carried remainder, capped by speed.

        Two different remainders live here and they want opposite treatment.

        The **sub-pixel fraction** must be carried, or a hand creeping across
        the frame produces less than one unit every tick, truncates to zero,
        and never moves at all.

        The **clipped excess** must not be. Carrying it means a cap that
        defers movement rather than refusing it: one bad landmark frame turns
        into a cursor that keeps sliding for a second after your hand stopped,
        and — worse — straight through an open palm, so the clutch doesn't stop
        the cursor dead. Dropping it is what "cap" was supposed to mean.

        The cap itself is a **speed**, not a per-report distance. A per-report
        cap silently means different things at 9.5 Hz and 120 Hz, which is the
        one thing the rest of this project is careful never to do — every other
        threshold is in seconds or hand-sizes for exactly this reason. At 120 Hz
        the interpolation thread moves a few units a tick and never approaches
        it; pumped once per vision frame the same limit in pixels would clip
        ordinary movement, which is precisely the bug this replaced.
        """
        # Clamp the distance, then split it — not the other way around. Capping
        # the truncated whole and dropping the remainder destroys movement
        # slower than one unit per tick, because int(0.4) is 0 and the 0.4 that
        # would have accumulated goes in the bin. Clamping first keeps the
        # fraction alive, so a cap below one unit per tick still delivers its
        # speed, just intermittently.
        cap = float(self.cfg["max_speed"]) * dt if dt > 0 else 0.0
        if cap and value > cap:
            value = cap
        elif cap and value < -cap:
            value = -cap

        whole = int(value)              # truncates toward zero, which is right
        # Whatever the cap refused is already gone; `rest` is only ever the
        # sub-unit fraction, so it can't accumulate into a backlog.
        return whole, value - whole

    def _pump_absolute(self, dt):
        alpha = self._ease(dt)
        for axis in (0, 1):
            self.pos[axis] += (self.target[axis] - self.pos[axis]) * alpha

        here = (round(_clamp01(self.pos[0]) * hid.ABSOLUTE_MAX),
                round(_clamp01(self.pos[1]) * hid.ABSOLUTE_MAX))
        if here != self._last_absolute:
            self._last_absolute = here
            self._mouse(x=here[0], y=here[1])

    # -- discrete actions --------------------------------------------------

    def _button(self, state):
        if state == self.buttons:
            return
        self.buttons = state
        self.drag_since = self.clock() if state else None
        if not state:
            self.released_by = "gesture"
        self._mouse()

    def _scroll(self, ticks):
        ticks = int(round(ticks * float(self.cfg["scroll"])))
        while ticks:
            chunk = max(-127, min(127, ticks))
            self._mouse(wheel=chunk)
            ticks -= chunk

    def _swipe(self, direction):
        spec = self.cfg.get(f"swipe_{direction}")
        if not spec:
            return
        modifiers, key = hid.combo(spec)
        self._keys(modifiers, [key])
        self._keys(0, ())               # and let go, or it repeats

    # -- the wire ----------------------------------------------------------

    def _mouse(self, x=0, y=0, wheel=0, pan=0):
        self.transport.send_mouse(hid.mouse_report(
            buttons=self.buttons, x=x, y=y, wheel=wheel, pan=pan,
            report_id=self.transport.mouse_id, pointer=self.pointer))

    def _keys(self, modifiers, keys):
        self.transport.send_keyboard(hid.keyboard_report(
            modifiers=modifiers, keys=keys,
            report_id=self.transport.keyboard_id))


def _clamp01(v):
    return 0.0 if v < 0 else (1.0 if v > 1 else v)
