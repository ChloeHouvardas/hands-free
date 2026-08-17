"""Landmark geometry to gesture events.

`GestureEngine.update(landmarks, t)` is the interface everything else uses —
`replay.py` feeds it recordings, `main.py` will feed it the live camera. Keeping
that boundary is what lets the whole gesture layer be tuned on a laptop.

The shape of it:

    NO_HAND ──hand appears──> PARKED         cursor frozen, nothing fires
    PARKED  ──point 0.45s───> MOVE           cursor follows your palm
    MOVE    ──pinch─────────> DRAG           button held down
            ──two fingers───> SCROLL         palm offset sets scroll speed
            ──three fingers─> SWIPE          sideways travel fires once
            ──open palm─────> PARKED

Three rules everything else hangs off, all of them things the recordings
taught us rather than things that seemed sensible up front:

* **Pointing is the only way out of PARKED.** A hand just sitting in view
  spends most of its time with two or three fingers half out. Letting those
  poses take control directly is where nearly every false click and stray
  scroll came from. Point to take over, like putting your hand back on a mouse.

* **Pinch beats pose.** When you pinch, your index and middle fingers read as
  extended, which is exactly the two-finger scroll pose. So a pinch is checked
  first and wins outright — except from PARKED, where a resting hand's thumb
  sits near its index often enough to matter.

* **PARKED is a clutch.** Open your palm and the cursor stops dead; move your
  hand somewhere comfortable, point again, and the cursor picks up from where
  it was rather than jumping. Same as lifting a mouse off the desk.

Nothing here is frame-counted and nothing uses raw coordinates: every gate is
in seconds and every threshold is an angle or a multiple of hand size. That's
what makes it survive a frame rate that wanders and a camera mounted sideways.

    python gestures.py --rotate 90
"""

import argparse
import os
import tomllib
from collections import deque

from hand import (FINGERS, finger_angles, hand_scale, palm_centroid,
                  pinch_ratio, thumb_abduction)
from filters import OneEuro2D

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")


def load_config(path=CONFIG):
    with open(path, "rb") as f:
        return tomllib.load(f)


class Event:
    """A gesture happened. `name` is what, `value` is any detail."""

    __slots__ = ("name", "value")

    def __init__(self, name, value=None):
        self.name = name
        self.value = value

    def __repr__(self):
        return self.name if self.value is None else f"{self.name}({self.value})"

    def __eq__(self, other):     # so tests can compare against plain strings
        return self.name == other if isinstance(other, str) else NotImplemented


class Fingers:
    """Which fingers are extended, with hysteresis so they don't chatter."""

    def __init__(self, cfg):
        self.extend = cfg["extend"]
        self.curl = cfg["curl"]
        self.state = {name: False for name in FINGERS}

    def update(self, landmarks):
        angles = finger_angles(landmarks)
        for name, a in angles.items():
            if a > self.extend:
                self.state[name] = True
            elif a < self.curl:
                self.state[name] = False
        return self.state


def classify(fingers, thumb_out):
    """Finger states to a pose name.

    The conditions are deliberately loose in the places the recordings said
    they had to be. Real pointing is lazy: the pinky comes along for the ride
    about two thirds of the time and the ring finger half-uncurls. So POINT
    asks only that the index is out and the middle is in, and ignores the rest.

    Pinch is deliberately *not* handled here. It's a much cleaner signal than
    any of this and shouldn't have to wait behind the pose dwell.
    """
    i = fingers["index"]
    m = fingers["middle"]
    r = fingers["ring"]
    p = fingers["pinky"]

    if i and m and r:
        # An open palm and a three-finger swipe differ only in the pinky, and
        # the pinky is the least reliable landmark on the hand — a swiping hand
        # lets it drift out about a third of the time. So PALM has to earn it
        # twice: pinky out *and* thumb swung clear of the palm, which a swiping
        # hand keeps tucked.
        return "PALM" if (p and thumb_out) else "THREE"
    if i and m:
        return "TWO"
    if i:
        return "POINT"
    return "UNKNOWN"


class Pose:
    """Raw per-frame pose, held until it's been stable long enough to believe.

    Single frames misclassify constantly — the recordings show the right pose
    winning only 65-75% of frames even when the hand is holding still. Waiting
    a quarter second before committing turns that into something usable.

    Scroll and swipe wait noticeably longer than pointing does. They're modes
    you stay in rather than actions, so nothing is lost by being slow to trust
    them — and both fire real input, so a transient misread is expensive. On
    the recordings this is the difference between a hand resting on the desk
    quietly scrolling the page and it doing nothing at all.
    """

    def __init__(self, cfg):
        self.dwell = cfg["dwell"]
        self.modal = {"TWO": cfg["modal_dwell"], "THREE": cfg["modal_dwell"]}
        self.current = "UNKNOWN"
        self.candidate = "UNKNOWN"
        self.since = 0.0

    def update(self, raw, t):
        if raw != self.candidate:
            self.candidate = raw
            self.since = t
        elif raw != self.current:
            if t - self.since >= self.modal.get(raw, self.dwell):
                self.current = raw
        return self.current

    def steady_for(self, t):
        """How long the committed pose has been continuously observed."""
        return t - self.since if self.candidate == self.current else 0.0

    def force(self, pose, t):
        self.current = self.candidate = pose
        self.since = t


class GestureEngine:
    """Turns a stream of (landmarks, timestamp) into gesture events."""

    def __init__(self, config=None):
        cfg = config or load_config()
        self.cfg = cfg
        self.fingers = Fingers(cfg["fingers"])
        self.pose = Pose(cfg["pose"])
        self.smooth = OneEuro2D(cfg["cursor"]["min_cutoff"],
                                cfg["cursor"]["beta"],
                                cfg["cursor"]["d_cutoff"])

        self.state = "NO_HAND"
        self.pinched = False
        self.pinched_since = 0.0
        self.last_seen = None

        # Cursor lives in normalized screen space, so the engine can be tested
        # without knowing anything about a real display.
        self.cursor = [0.5, 0.5]
        self.anchor = None          # hand position when MOVE was entered
        self.cursor_anchor = None   # cursor position at that same moment

        self.scroll_anchor = None
        self.scroll_debt = 0.0
        self.scroll_at = 0.0
        self.swipe_track = deque()
        self.refractory_until = 0.0
        self.last_t = None

        # Rolling hand sizes, for spotting foreshortening.
        self.scales = deque(maxlen=20)

    # -- main entry point --------------------------------------------------

    def update(self, landmarks, t):
        """Returns a list of Events for this frame (usually empty)."""
        dt = 0.0 if self.last_t is None else max(0.0, t - self.last_t)
        self.last_t = t

        if not landmarks:
            return self._absent(t)

        self.last_seen = t

        scale = hand_scale(landmarks)
        self.scales.append(scale)
        if self._foreshortened(scale):
            # Hand is pointing at the camera. Every 2D measurement is garbage
            # here and no threshold can rescue it, so hold everything as-is.
            return []

        events = []
        if self.state == "NO_HAND":
            self.state = "PARKED"
            self.pose.force("UNKNOWN", t)
            events.append(Event("hand_found"))

        fingers = self.fingers.update(landmarks)
        was_pinched = self.pinched
        self.pinched = self._pinch(landmarks, fingers)
        if self.pinched and not was_pinched:
            self.pinched_since = t
        thumb_out = thumb_abduction(landmarks) > self.cfg["fingers"]["thumb_out"]
        pose = self.pose.update(classify(fingers, thumb_out), t)

        events += self._transition(pose, landmarks, t, dt)
        return events

    def take_control(self, landmarks, t):
        """Skip the pointing wake-up and go straight to MOVE.

        Only for replaying recordings: each clip was recorded already doing
        its gesture, with no pointing lead-in, because at the time nothing
        needed one. Live use always points first.
        """
        self.state = "MOVE"
        self.pose.force("POINT", t)
        self._anchor(palm_centroid(landmarks), t)

    # -- states ------------------------------------------------------------

    # Which state each pose wants us in. UNKNOWN is absent on purpose: a pose
    # we can't name should never move us, it should leave us where we were.
    ROUTE = {
        "PALM": "PARKED",
        "POINT": "MOVE",
        "TWO": "SCROLL",
        "THREE": "SWIPE",
    }

    def _transition(self, pose, landmarks, t, dt):
        events = []
        hand = palm_centroid(landmarks)
        scale = hand_scale(landmarks) or 1e-6

        target = self._target(pose, t)
        if target and target != self.state:
            events += self._enter(target, hand, t)

        # Whatever state we ended up in, do its per-frame work.
        if self.state in ("MOVE", "DRAG"):
            events += self._move(hand, t)
        elif self.state == "SCROLL":
            events += self._scroll(hand, scale, dt)
        elif self.state == "SWIPE":
            events += self._swipe(hand, scale, t)

        return events

    def _target(self, pose, t):
        """Where this frame says we should be, or None to stay put."""
        held = t - self.pinched_since >= self.cfg["pinch"]["dwell"]

        if self.pinched and held and self.state != "PARKED":
            # Pinching outranks the fingers. It has to: when you pinch, your
            # index and middle read as extended, which is exactly the
            # two-finger scroll pose. PARKED is the one exception — a resting
            # hand naturally sits with the thumb near the index, and the
            # recordings show that misfiring constantly if we let it click.
            return "DRAG"

        if self.state == "DRAG":
            # Pinch released. Fall back to MOVE rather than nothing, so an
            # unrecognisable pose can never leave the button held down.
            return self.ROUTE.get(pose, "MOVE")

        if self.state == "PARKED":
            # Parked is where your hand rests, and the only way out is to
            # point — deliberately, and for longer than a normal transition.
            #
            # This is the single most valuable rule in the file. A hand just
            # sitting in view spends most of its time with two or three
            # fingers out, and letting those poses wake the machine directly
            # is where nearly every false click and stray scroll came from.
            # Point to take control, exactly like putting a hand back on a
            # mouse.
            if pose not in self.cfg["pose"]["wake_poses"]:
                return None
            if self.pose.steady_for(t) < self.cfg["pose"]["wake"]:
                return None

        return self.ROUTE.get(pose)

    def _enter(self, target, hand, t):
        """Leave the current state cleanly, then set up the new one.

        Every transition goes through here, so there is exactly one place that
        releases the button — no path through the machine can leave it stuck
        down, which is the failure mode that locks up a Mac.
        """
        events = []
        if self.state == "DRAG":
            events.append(Event("click_up"))
        self.scroll_anchor = None
        self.swipe_track.clear()

        self.state = target

        if target in ("MOVE", "DRAG"):
            self._anchor(hand, t)
        elif target == "SCROLL":
            self.scroll_anchor = hand
            self.scroll_debt = 0.0
            self.scroll_at = t

        if target == "DRAG":
            events.append(Event("click_down"))
        return events

    def _anchor(self, hand, t):
        """Latch where the hand is now against where the cursor is now.

        This is the clutch. Everything after is measured as displacement from
        here, so re-entering MOVE never teleports the cursor.
        """
        self.anchor = hand
        self.cursor_anchor = list(self.cursor)
        self.smooth.reset()

    def _move(self, hand, t):
        if self.anchor is None:
            self._anchor(hand, t)
        gain = self.cfg["cursor"]["gain"]
        x, y = self.smooth(hand[0], hand[1], t)
        self.cursor = [
            _clamp(self.cursor_anchor[0] + (x - self.anchor[0]) * gain),
            _clamp(self.cursor_anchor[1] + (y - self.anchor[1]) * gain),
        ]
        return [Event("cursor", tuple(round(v, 4) for v in self.cursor))]

    def _scroll(self, hand, scale, dt):
        cfg = self.cfg["scroll"]

        # Sit still for a moment before scrolling anything, re-anchoring as we
        # go. A pose that flickers to TWO for two frames while your hand is
        # sweeping across the frame would otherwise fling the page — and the
        # recordings show that flicker happening during ordinary cursor moves.
        if self.last_t - self.scroll_at < cfg["settle"]:
            self.scroll_anchor = hand
            return []

        offset = (hand[1] - self.scroll_anchor[1]) / scale
        mag = abs(offset) - cfg["deadzone"]
        if mag <= 0:
            return []
        span = max(1e-6, cfg["full"] - cfg["deadzone"])
        rate = min(1.0, mag / span) * cfg["max_rate"]

        # Accumulate fractional ticks so slow scrolling still moves eventually
        # instead of rounding to zero every frame.
        self.scroll_debt += rate * dt * (1 if offset < 0 else -1)
        ticks = int(self.scroll_debt)
        if not ticks:
            return []
        self.scroll_debt -= ticks
        return [Event("scroll", ticks)]

    def _swipe(self, hand, scale, t):
        """Fire when the palm has travelled far enough sideways, recently.

        Not measured against a fixed anchor: a swipe that takes a bit longer
        than the window would reset its own anchor halfway and never fire, and
        the recordings are full of swipes like that. Instead keep the last
        window's worth of positions and compare against the furthest one — so
        a slow swipe and a fast one both register, and slow drift still can't
        accumulate because anything older than the window is dropped.
        """
        cfg = self.cfg["swipe"]
        self.swipe_track.append((t, hand[0], hand[1]))
        while self.swipe_track and t - self.swipe_track[0][0] > cfg["window"]:
            self.swipe_track.popleft()

        if t < self.refractory_until:
            return []

        best = None
        for _, px, py in self.swipe_track:
            dx = (hand[0] - px) / scale
            dy = (hand[1] - py) / scale
            if best is None or abs(dx) > abs(best[0]):
                best = (dx, dy)
        if best is None:
            return []

        dx, dy = best
        if abs(dx) >= cfg["distance"] and abs(dx) >= cfg["dominance"] * abs(dy):
            self.refractory_until = t + cfg["refractory"]
            self.swipe_track.clear()
            return [Event("swipe", "right" if dx > 0 else "left")]
        return []

    def _absent(self, t):
        if self.last_seen is None or self.state == "NO_HAND":
            return []
        if t - self.last_seen < self.cfg["pose"]["lost_grace"]:
            return []

        events = []
        if self.state == "DRAG":
            events.append(Event("click_up"))
        self.state = "NO_HAND"
        self.pinched = False
        self.anchor = None
        self.scroll_anchor = None
        self.swipe_track.clear()
        self.scales.clear()
        events.append(Event("hand_lost"))
        return events

    # -- helpers -----------------------------------------------------------

    def _pinch(self, landmarks, fingers):
        cfg = self.cfg["pinch"]

        # A hand with three fingers out isn't pinching, however close the thumb
        # happens to be sitting to the index — and a hand resting on a desk
        # sits exactly like that, which is where most of the false clicks in
        # the control recording came from. Pinching pulls the ring finger in
        # with the index; every real pinch on record has it curled.
        if fingers["ring"] and not self.pinched:
            return False

        ratio = pinch_ratio(landmarks)
        if self.pinched:
            return ratio <= cfg["exit"]
        return ratio < cfg["enter"]

    def _foreshortened(self, scale):
        if len(self.scales) < 10:
            return False
        median = sorted(self.scales)[len(self.scales) // 2]
        return scale < self.cfg["pose"]["foreshorten"] * median


def _clamp(v):
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270])
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    # Imported here, not at module level, so replay.py can use this file on a
    # machine with no camera and no picamera2.
    import time

    import cv2

    from landmarks import HandTracker, draw
    from preview import Preview

    tracker = HandTracker(args.width, args.height, rotate=args.rotate)
    preview = None if args.no_preview else Preview(port=args.port)
    engine = GestureEngine()
    t0 = time.perf_counter()

    try:
        for frame, landmarks in tracker.frames():
            t = time.perf_counter() - t0
            for event in engine.update(landmarks, t):
                if event.name != "cursor":
                    print(f"{t:6.2f}s  {event}", flush=True)

            if preview:
                if landmarks:
                    draw(frame, landmarks)
                h, w = frame.shape[:2]
                cv2.putText(frame, engine.state, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 220, 255), 2)
                cv2.putText(frame, engine.pose.current, (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
                cx = int(engine.cursor[0] * w)
                cy = int(engine.cursor[1] * h)
                cv2.circle(frame, (cx, cy), 9, (255, 0, 255), 2)
                preview.update(frame)
    finally:
        tracker.close()
        if preview:
            preview.close()


if __name__ == "__main__":
    main()
