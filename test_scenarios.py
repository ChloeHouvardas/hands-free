"""End-to-end scenarios: what a person would notice, not what fired.

`test_gestures.py` checks the state machine, `test_hid.py` checks the bytes,
`test_driver.py` checks the guards. All three can pass while the device is
useless, because none of them asks the only question that matters: **point at
the camera — does the cursor go the right way?**

So these run whole scripted sessions from `handsfree/scripts.py` through the
real engine and the real driver into a recording transport, and assert the
outcome. Same scripts you can drive an actual Mac with:

    python3 -m handsfree run --source synth:drag

which means the thing demoed and the thing asserted can't drift apart.

No hardware, no camera, no Bluetooth.

    python3 test_scenarios.py
"""

from handsfree import scripts
from handsfree.config import load_config
from handsfree.driver import Driver
from handsfree.gestures import GestureEngine
from handsfree.transport.null import Backend as Null

TESTS = []


def test(fn):
    TESTS.append(fn)
    return fn


class Session:
    """One script, run all the way through, with the results to hand."""

    def __init__(self, name, **overrides):
        cfg = load_config()
        hid_cfg = dict(cfg.get("hid", {}), **overrides)
        self.name = name
        self.expect = scripts.expect(name)
        self.transport = Null(quiet=True)
        # thread=False and a clip clock: the script's seconds are what every
        # threshold is measured in, and the wall clock is not those seconds.
        self.driver = Driver(self.transport, hid_cfg, thread=False)
        engine = GestureEngine(cfg)

        self.events = []
        # A per-frame trace, because most of the interesting questions are
        # about a *window* of the session — did the cursor move while parked,
        # did it settle once the hand stopped — and totals can't answer those.
        self.trace = []
        for t, landmarks in scripts.build(name):
            self.driver.clock = lambda t=t: t
            frame_events = engine.update(landmarks, t)
            self.events += [(e.name, e.value) for e in frame_events]
            before = len(self.transport.sent)
            self.driver.handle(frame_events)
            self.driver.pump()
            self.trace.append((t, engine.state,
                               self.transport.sent[before:]))
        self.end = t
        self.held = self.driver.buttons
        self.cursor = list(self.driver.pos)
        self.engine = engine

    def close(self):
        self.driver.close()

    # -- what came out -----------------------------------------------------

    def count(self, name):
        return sum(1 for n, _v in self.events if n == name)

    @property
    def reports(self):
        return self.transport.sent

    def mouse_reports(self):
        return [r for kind, r in self.reports if kind == "mouse"]

    def travel(self, axis=0):
        lo = 1 + axis * 2
        return sum(int.from_bytes(r[lo:lo + 2], "little", signed=True)
                   for r in self.mouse_reports())

    def scrolls(self):
        return [int.from_bytes(r[5:6], "little", signed=True)
                for r in self.mouse_reports()]

    def button_bytes(self):
        return [r[0] for r in self.mouse_reports()]

    def names(self):
        return [n for n, _v in self.events if n != "cursor"]

    def moved_while(self, state):
        """Total cursor travel over the frames spent in a given engine state."""
        total = 0
        for _t, st, sent in self.trace:
            if st != state:
                continue
            for kind, r in sent:
                if kind == "mouse":
                    total += abs(int.from_bytes(r[1:3], "little", signed=True))
        return total

    def reports_after(self, seconds):
        """Every report sent after this point in the clip."""
        return [r for t, _st, sent in self.trace if t >= seconds for r in sent]


def check(session):
    """Assert a script against its own declared expectation."""
    e, s = session.expect, session
    where = f"{s.name} ({e.get('doc', '')})"

    if "clicks" in e:
        assert s.count("click_down") == e["clicks"], \
            f"{where}: expected {e['clicks']} click(s), got " \
            f"{s.count('click_down')} — {s.names()}"
    if "swipes" in e:
        assert s.count("swipe") == e["swipes"], \
            f"{where}: expected {e['swipes']} swipe(s), got " \
            f"{s.count('swipe')} — {s.names()}"
    if e.get("scroll") == 0:
        assert s.count("scroll") == 0, \
            f"{where}: expected no scrolling, got {s.count('scroll')}"
    if "button_at_end" in e:
        assert s.held == e["button_at_end"], \
            f"{where}: button was {s.held} at the end"
    if e.get("cursor_x") == "+":
        assert s.travel(0) > 0, \
            f"{where}: cursor should have gone right, moved {s.travel(0)}"
    if e.get("cursor_x") == "-":
        assert s.travel(0) < 0, \
            f"{where}: cursor should have gone left, moved {s.travel(0)}"
    if e.get("silent"):
        assert not s.reports, \
            f"{where}: should have produced nothing, sent {len(s.reports)} " \
            f"report(s) — {s.names()}"


# -- every script keeps its own promise --------------------------------------

@test
def test_every_script_does_what_it_says_it_does():
    """The whole file in one test: run them all, check each against itself."""
    for name in scripts.names():
        session = Session(name)
        try:
            check(session)
        finally:
            session.close()


@test
def test_no_script_can_leave_the_button_down():
    for name in scripts.names():
        session = Session(name)
        held, reports = session.held, session.button_bytes()
        session.close()
        if held:
            for_ = session.end - (session.driver.drag_since or session.end)
            assert for_ <= float(session.driver.cfg["max_drag"]), \
                f"{name} held the button {for_:.1f}s with no guard firing"
        assert not reports or session.button_bytes()[-1] == 0, \
            f"{name}: last report still has the button down"


# -- the individual promises, spelled out ------------------------------------

@test
def test_pointing_moves_the_cursor_and_stopping_stops_it():
    s = Session("move")
    try:
        assert s.travel(0) > 0, s.travel(0)
        # The script ends with a full second of a hand held still. A cursor
        # that is still creeping then would drift away from the hand forever.
        # Note the tail of `sent` is no use here: a still cursor sends nothing
        # at all, so the last reports are always the last *moving* ones.
        # Not "zero reports": the engine's One Euro filter is deliberately
        # slow to settle (min_cutoff 0.5 buys steadiness at the cost of lag),
        # so the cursor arrives over a few frames rather than instantly. What
        # must not happen is it *keeps* moving — a backlog draining, which is
        # what the old per-report cap produced.
        tail = s.reports_after(s.end - 0.6)
        drift = sum(abs(int.from_bytes(r[1:3], "little", signed=True))
                    for kind, r in tail if kind == "mouse")
        assert drift <= 2, \
            f"cursor drifted {drift}px over the last 0.6s of a still hand"
    finally:
        s.close()


@test
def test_one_pinch_is_exactly_one_click():
    """Two clicks from one pinch is a double-click nobody asked for."""
    s = Session("click")
    try:
        assert s.count("click_down") == 1, s.names()
        assert s.count("click_up") == 1, s.names()
    finally:
        s.close()


@test
def test_a_drag_moves_between_the_press_and_the_release():
    """A click_down and click_up with no movement between them isn't a drag,
    it's a click — and the file would still get dropped where it started."""
    s = Session("drag")
    try:
        down = up = None
        moved = 0
        for i, (kind, report) in enumerate(s.reports):
            if kind != "mouse":
                continue
            if report[0] == 1 and down is None:
                down = i
            if down is not None and up is None:
                if report[0] == 0 and i > down:
                    up = i
                else:
                    moved += abs(int.from_bytes(report[1:3], "little",
                                                signed=True))
        assert down is not None, "never pressed"
        assert up is not None, "never released"
        assert moved > 0, "the button was held but the cursor never moved"
    finally:
        s.close()


@test
def test_parking_stops_the_cursor_dead():
    """The clutch. While parked the hand keeps moving and the cursor must not."""
    s = Session("clutch")
    try:
        assert s.count("click_down") == 0, s.names()
        # The hand crosses 0.5 -> 0.95 of the frame while parked — nearly half
        # the frame, and the largest movement in the script. Not a total: the
        # earlier pointing phase legitimately moves the cursor ~560px, so only
        # the parked frames answer the question.
        assert s.moved_while("PARKED") == 0, \
            f"cursor moved {s.moved_while('PARKED')}px while parked"
        assert s.moved_while("MOVE") > 0, "nothing moved even while pointing"
    finally:
        s.close()


@test
def test_retaking_control_does_not_teleport_the_cursor():
    """Re-entering MOVE anchors against where the cursor already is. If it
    didn't, the cursor would jump to wherever your hand happened to be."""
    s = Session("clutch")
    try:
        steps = [abs(int.from_bytes(r[1:3], "little", signed=True))
                 for r in s.mouse_reports()]
        # Pumped once per vision frame, so a tick is ~1/9.5s of the speed cap.
        cap = float(s.driver.cfg["max_speed"]) / 9.5
        assert not steps or max(steps) <= cap + 1, \
            f"a single report moved {max(steps)}, cap is {cap:.0f} — a jump"
    finally:
        s.close()


@test
def test_scrolling_goes_both_ways():
    s = Session("scroll")
    try:
        ticks = [t for t in s.scrolls() if t]
        assert ticks, "no scrolling happened at all"
        assert any(t > 0 for t in ticks) and any(t < 0 for t in ticks), \
            f"scroll only went one way: {ticks}"
    finally:
        s.close()


@test
def test_a_swipe_fires_once_and_releases_its_keys():
    """A key report with no release repeats — on a Mac that means switching
    spaces until something stops it."""
    s = Session("swipe")
    try:
        assert s.count("swipe") == 1, f"{s.count('swipe')} swipes: {s.names()}"
        keys = [r for kind, r in s.reports if kind == "keys"]
        assert len(keys) == 2, keys
        assert keys[0] != b"\x00" * 8, "pressed nothing"
        assert keys[1] == b"\x00" * 8, "never let go of the key"
    finally:
        s.close()


@test
def test_a_resting_hand_produces_absolutely_nothing():
    """The case the whole design is tuned around. Not 'few reports' — none."""
    s = Session("idle")
    try:
        assert not s.reports, \
            f"an idle hand sent {len(s.reports)} report(s): {s.names()}"
    finally:
        s.close()


@test
def test_a_hand_vanishing_mid_pinch_releases_before_it_is_declared_lost():
    """Order matters: click_up must come first, or the Mac keeps the button."""
    s = Session("vanish")
    try:
        names = s.names()
        assert "hand_lost" in names, names
        assert "click_up" in names, names
        assert names.index("click_up") < names.index("hand_lost"), names
        assert s.button_bytes()[-1] == 0, "left the button down"
    finally:
        s.close()


@test
def test_the_demo_script_exercises_every_gesture():
    """What `run --source synth:demo` drives a real Mac with — so if this
    passes, that demo is worth watching."""
    s = Session("demo")
    try:
        assert s.count("click_down") >= 1, s.names()
        assert s.count("scroll") >= 1, s.names()
        assert s.held == 0
    finally:
        s.close()


# -- robustness --------------------------------------------------------------

@test
def test_scenarios_hold_up_at_half_the_frame_rate():
    """Everything is time-gated, so dropping frames should cost accuracy, not
    correctness. This is what a slower Pi looks like."""
    for name in ("click", "swipe", "idle"):
        cfg = load_config()
        engine = GestureEngine(cfg)
        transport = Null(quiet=True)
        driver = Driver(transport, cfg.get("hid", {}), thread=False)
        try:
            for i, (t, lm) in enumerate(scripts.build(name)):
                if i % 2:
                    continue
                driver.clock = lambda t=t: t
                driver.handle(engine.update(lm, t))
                driver.pump()
            if name == "idle":
                assert not transport.sent, f"{name} at half rate sent input"
            assert driver.buttons == 0, f"{name} at half rate held the button"
        finally:
            driver.close()


@test
def test_scenarios_are_unchanged_by_which_way_up_the_camera_is():
    """The reason the whole feature set is angles rather than coordinates."""
    for rotation in (0, 90, 180, 270):
        cfg = load_config()
        engine = GestureEngine(cfg)
        transport = Null(quiet=True)
        driver = Driver(transport, cfg.get("hid", {}), thread=False)
        clicks = 0
        try:
            for t, lm in scripts.build("click"):
                if lm is not None and rotation:
                    lm = _rotate(lm, rotation)
                driver.clock = lambda t=t: t
                events = engine.update(lm, t)
                clicks += sum(1 for e in events if e.name == "click_down")
                driver.handle(events)
                driver.pump()
        finally:
            driver.close()
        assert clicks == 1, f"at {rotation} degrees got {clicks} click(s)"


def _rotate(landmarks, degrees):
    import math
    from types import SimpleNamespace
    a = math.radians(degrees)
    cos, sin = math.cos(a), math.sin(a)
    out = []
    for p in landmarks:
        x, y = p.x - 0.5, p.y - 0.5
        out.append(SimpleNamespace(x=x * cos - y * sin + 0.5,
                                   y=x * sin + y * cos + 0.5, z=p.z))
    return out


def main():
    failed = []
    for fn in TESTS:
        try:
            fn()
            print(f"  pass  {fn.__name__}")
        except AssertionError as e:
            failed.append(fn.__name__)
            print(f"  FAIL  {fn.__name__}\n          {e}")
        except Exception as e:
            failed.append(fn.__name__)
            print(f"  ERROR {fn.__name__}\n          {type(e).__name__}: {e}")
    print(f"\n{len(TESTS) - len(failed)}/{len(TESTS)} pass")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
